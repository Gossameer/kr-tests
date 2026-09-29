"""
Встроенная генерация вариантов через ИИ.

Как устроено:
  1. Учитель нажимает «Сгенерировать» — в ai_jobs появляется задание, а работа
     идёт в фоновом потоке. Страница опрашивает статус и может уйти/вернуться:
     всё состояние лежит в базе.
  2. Каждый вариант — ОТДЕЛЬНЫЙ запрос. Один большой ответ на все варианты
     чаще обрывается на max_tokens и целиком ломается из-за одной запятой.
     Сначала делаем вариант 1, остальные — параллельно, не больше
     MAX_PARALLEL одновременно. В каждый запрос передаём уже готовые задания
     этого умения из других вариантов, чтобы ИИ не повторялся.
  3. Ответ разбираем так же, как фронтенд разбирает вставленный вручную JSON,
     а полноту проверяет та же схема TestCreate, что и при публикации.
     Битый JSON или неполный вариант — повтор (до GEN_RETRIES раз), потом
     вариант помечается «не удалось», остальные остаются.
  4. Самопроверка: каждое задание отдельно решает AI_CHECK_MODEL, НЕ видя
     правильного ответа. Ответ сравниваем так же, как ответ ученика
     (app/answers.py), а в «выборе» — по номеру варианта. Не совпало —
     задание помечается needs_review, учитель видит оба ответа.
  5. Ничего не публикуется само: результат уходит в таблицу проверки.
"""

import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app import db
from app.ai_client import AIError, RequestContext, chat
from app.answers import check_input_answer
from app.config import get_settings
from app.errors import describe_error
from app.schemas import TestCreate

logger = logging.getLogger(__name__)

# Вариантов одной проверочной работы генерируется одновременно не больше этого.
MAX_PARALLEL = 4
# Сколько раз повторяем вариант после первой неудачной попытки.
GEN_RETRIES = 2
# Сколько уже готовых заданий умения показываем ИИ, чтобы он не повторялся.
# Больше — длиннее запрос и дороже, а пользы почти не добавляет.
AVOID_PER_SKILL = 15
AVOID_TEXT_LEN = 200

# Общий потолок одновременных запросов на весь сервер: если генерацию
# запустят несколько учителей сразу, провайдер не получит лавину.
_global_slots = threading.BoundedSemaphore(MAX_PARALLEL * 2)

# Флаги остановки заданий: при фатальной ошибке (нет денег, ключ не принят)
# остальные потоки этого задания перестают слать запросы.
_cancel_events: dict[int, threading.Event] = {}
_cancel_lock = threading.Lock()

INTERRUPTED = "Генерация прервана перезапуском сервера — нажмите «повторить»."


class VariantProblem(Exception):
    """Ответ ИИ не годится: битый JSON, неполный вариант, повтор заданий."""


# =====================================================================
# Промты
# =====================================================================


def format_rule(answer_format: str) -> str:
    """Понятное ИИ описание формата ответа (как в промте на фронтенде)."""
    if answer_format == "choice":
        return (
            '"format": "choice", 4 варианта ответа в "options" '
            'и номер верного в "correct" (с нуля)'
        )
    return '"format": "input", список допустимых ответов в "answers"'


def subject_line(request: dict) -> str:
    parts = [request.get("subject", "").strip(), request.get("topic", "").strip()]
    return ", ".join(part for part in parts if part) or "не указаны"


def skills_block(skills: list[dict]) -> str:
    return "\n".join(
        f"{number}. {skill['title']} — заданий в варианте: {skill['tasks_per_variant']}, "
        f"формат: {format_rule(skill['answer_format'])}"
        for number, skill in enumerate(skills, start=1)
    )


def avoid_block(skills: list[dict], existing: dict[int, list[str]]) -> str:
    """Уже готовые задания по умениям — «так уже было, придумай другое»."""
    lines: list[str] = []
    for number, skill in enumerate(skills, start=1):
        texts = existing.get(number, [])[-AVOID_PER_SKILL:]
        if not texts:
            continue
        lines.append(f"Умение {number} «{skill['title']}»:")
        lines.extend(f"- {text[:AVOID_TEXT_LEN]}" for text in texts)
    return "\n".join(lines)


# Школьная запись: так пишут в учебниках, и ученик не спотыкается о «*» и «/».
SCHOOL_NOTATION = (
    "записывай математику по-школьному: умножение — знак «·» (например, 3 · 4), "
    "деление — двоеточие «:» (например, 12 : 3), дроби — через косую черту (3/5); "
    "не используй «*» и не пиши «/» для деления чисел."
)

SYSTEM_GENERATE = (
    "Ты составляешь задания для школьных проверочных работ. "
    "Отвечай строго JSON без пояснений и без markdown."
)

TASK_SHAPE_INPUT = """{
  "skill": 1,
  "text": "Текст задания",
  "format": "input",
  "answers": ["12", "12.0"],
  "solution": "Краткое решение"
}"""

TASK_SHAPE_CHOICE = """{
  "skill": 2,
  "text": "Текст задания",
  "format": "choice",
  "options": ["Вариант А", "Вариант Б", "Вариант В", "Вариант Г"],
  "correct": 0,
  "solution": "Краткое решение"
}"""


def variant_messages(
    request: dict,
    variant_no: int,
    existing: dict[int, list[str]],
    previous_error: str,
) -> list[dict]:
    """Промт на ОДИН вариант."""
    skills = request["skills"]
    total = sum(skill["tasks_per_variant"] for skill in skills)
    avoid = avoid_block(skills, existing)

    parts = [
        f"Составь вариант {variant_no} (из {request['variants_count']}) проверочной работы.",
        f"Предмет и тема: {subject_line(request)}",
        f"Класс: {request.get('grade', '').strip() or 'не указан'}",
        "",
        'Проверяемые умения (номер умения указывай в поле "skill"):',
        skills_block(skills),
        "",
        "Требования:",
        "- задания на ВСЕ умения, ровно в указанном количестве;",
        f"- всего заданий в варианте: {total};",
        "- у заданий с вводом ответа перечисли в \"answers\" все правильные формы записи "
        '(например "0,5" и "0.5"), ответ короткий — число или несколько слов;',
        '- к КАЖДОМУ заданию краткое решение в "solution" (1–2 строки, для учителя);',
        "- задания должны быть решаемы и иметь однозначный ответ;",
        f"- {SCHOOL_NOTATION}",
    ]

    if avoid:
        parts += [
            "",
            "В других вариантах уже есть такие задания. НЕ повторяй их: "
            "другие числа, данные и формулировки, но тот же тип и та же сложность.",
            avoid,
        ]

    if previous_error:
        parts += [
            "",
            f"Прошлый ответ не подошёл: {previous_error}. Исправь это.",
        ]

    parts += [
        "",
        "Верни ТОЛЬКО JSON такой структуры:",
        f'{{"variant": {variant_no}, "tasks": [{TASK_SHAPE_INPUT}, {TASK_SHAPE_CHOICE}]}}',
        "",
        'Поле "skill" — номер умения из списка выше. Поле "correct" — номер '
        'правильного варианта в "options", нумерация с нуля.',
    ]

    return [
        {"role": "system", "content": SYSTEM_GENERATE},
        {"role": "user", "content": "\n".join(parts)},
    ]


def task_messages(
    request: dict,
    previous_error: str,
) -> list[dict]:
    """Промт на замену ОДНОГО задания (то же умение и формат)."""
    skill = request["skills"][request["skill_index"] - 1]
    shape = TASK_SHAPE_CHOICE if skill["answer_format"] == "choice" else TASK_SHAPE_INPUT
    shape = shape.replace('"skill": 2,\n  ', "").replace('"skill": 1,\n  ', "")

    avoid = [text[:AVOID_TEXT_LEN] for text in request.get("avoid_texts", [])][
        -AVOID_PER_SKILL:
    ]

    parts = [
        "Придумай ОДНО новое задание для проверочной работы.",
        f"Предмет и тема: {subject_line(request)}",
        f"Класс: {request.get('grade', '').strip() or 'не указан'}",
        f"Проверяемое умение: {skill['title']}",
        f"Формат ответа: {format_rule(skill['answer_format'])}",
        f"Это задание для варианта {request['variant_no']}.",
        "",
        "Задание, которое нужно заменить (новое должно проверять то же умение и быть "
        "той же сложности, но с другими числами и данными):",
        request.get("current_text", "").strip() or "(пока пустое)",
    ]
    if avoid:
        parts += ["", "Такие задания уже есть — не повторяй их:"]
        parts += [f"- {text}" for text in avoid]
    parts += [
        "",
        'К заданию обязательно краткое решение в "solution".',
        SCHOOL_NOTATION[:1].upper() + SCHOOL_NOTATION[1:],
    ]
    if previous_error:
        parts += ["", f"Прошлый ответ не подошёл: {previous_error}. Исправь это."]
    parts += ["", "Верни ТОЛЬКО JSON одного задания:", shape]

    return [
        {"role": "system", "content": SYSTEM_GENERATE},
        {"role": "user", "content": "\n".join(parts)},
    ]


def check_messages(request: dict, task: dict) -> list[dict]:
    """
    Промт самопроверки. Правильного ответа в нём НЕТ — иначе модель просто
    согласится с ним, и проверка потеряет смысл.
    """
    context = (
        f"Предмет и тема: {subject_line(request)}. "
        f"Класс: {request.get('grade', '').strip() or 'не указан'}."
    )
    if task["answer_format"] == "choice":
        options = "\n".join(
            f"{number}) {option}" for number, option in enumerate(task["options"], start=1)
        )
        user = (
            f"{context}\n\nРеши задание и выбери правильный вариант ответа.\n\n"
            f"Задание: {task['text']}\n\nВарианты:\n{options}\n\n"
            "В ответе напиши ТОЛЬКО номер правильного варианта — одно число, "
            "без пояснений."
        )
    else:
        user = (
            f"{context}\n\nРеши задание.\n\nЗадание: {task['text']}\n\n"
            "В ответе напиши ТОЛЬКО окончательный ответ — число или несколько слов, "
            "без решения и пояснений."
        )
    return [
        {"role": "system", "content": "Ты внимательно решаешь школьные задания."},
        {"role": "user", "content": user},
    ]


# =====================================================================
# Разбор ответа ИИ (повторяет frontend/src/lib/parseTestJson.ts)
# =====================================================================

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def strip_reasoning(raw: str) -> str:
    """
    Убирает рассуждения модели из текста ответа.

    Кроме парного <think>…</think> бывает обрыв (открывающий тег есть,
    закрывающего нет — модель упёрлась в max_tokens) и обратный случай, когда
    сервис срезал открывающий тег и оставил только «…</think> ответ».
    """
    text = THINK_RE.sub("", raw)
    lower = text.lower()
    if "</think>" in lower:
        text = text[lower.rfind("</think>") + len("</think>") :]
    elif "<think>" in lower:
        text = text[: lower.find("<think>")]
    return text.replace("\x00", "").strip()


def extract_json_block(raw: str) -> str:
    """Вырезает JSON из ответа: ```-блок или от первой «{» до последней «}»."""
    text = strip_reasoning(raw)

    fenced = FENCE_RE.search(text)
    if fenced and fenced.group(1).strip():
        return fenced.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _as_string(value: object) -> str:
    # \x00 PostgreSQL не принимает ни в TEXT, ни в JSONB — запись упала бы целиком.
    return value.replace("\x00", "").strip() if isinstance(value, str) else ""


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    # Числа в ответах ИИ иногда пишет без кавычек: [12] вместо ["12"].
    items = [
        str(item) if isinstance(item, (int, float)) and not isinstance(item, bool) else item
        for item in value
    ]
    return [text for text in (_as_string(item) for item in items) if text]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int(value: object) -> int | None:
    """Целое из ответа ИИ: 2, 2.0 и "2" — одно и то же; остальное — None."""
    if _is_int(value):
        return value  # type: ignore[return-value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


# «*» как знак умножения: между пробелами («3 * 4») или вплотную между числами,
# буквами и скобками («3*4», «2*x», «(a+b)*c»). «**» (жирный markdown) не трогаем.
STAR_RE = re.compile(r"(?<=[\w)\]])\s*(?<!\*)\*(?!\*)\s*(?=[\w(\[])|\s\*\s")


def school_signs(text: str) -> str:
    """Заменяет «*» на школьный знак умножения «·» в тексте, который увидит человек."""
    return STAR_RE.sub(" · ", text)


def read_task(source: dict, skill_index: int, fallback_format: str) -> dict:
    """
    Одно задание ИИ → поля TaskIn.

    Отличие от фронтенда одно: если ИИ забыл поле "format", берём формат
    умения, а не «ввод» — при генерации мы его точно знаем.
    """
    raw_format = _as_string(source.get("format")).lower()
    answer_format = raw_format if raw_format in ("input", "choice") else fallback_format
    correct = _as_int(source.get("correct"))

    return {
        "skill_index": skill_index,
        "text": school_signs(_as_string(source.get("text"))),
        "answer_format": answer_format,
        "options": (
            [school_signs(option) for option in _as_string_list(source.get("options"))]
            if answer_format == "choice"
            else []
        ),
        "correct": correct if answer_format == "choice" else None,
        "accepted_answers": (
            _as_string_list(source.get("answers")) if answer_format == "input" else []
        ),
        "solution": school_signs(_as_string(source.get("solution"))),
    }


def load_json_object(raw: str) -> dict:
    if not raw.strip():
        raise VariantProblem("пустой ответ")
    try:
        data = json.loads(extract_json_block(raw))
    except ValueError:
        raise VariantProblem("ответ не является корректным JSON") from None
    if not isinstance(data, dict):
        raise VariantProblem("ожидался JSON-объект")
    return data


def normalize_text(text: str) -> str:
    """Для поиска повторов: регистр и пробелы не важны."""
    return " ".join(text.lower().replace("ё", "е").split())


def validate_tasks(skills: list[dict], tasks: list[dict]) -> list[dict]:
    """
    Проверка полноты — ТА ЖЕ схема, что при публикации (TestCreate).

    Подставляем заглушки в шапку и проверяем один вариант. Схема заодно
    нормализует задания (обрезает пробелы, чистит пустые ответы).
    """
    try:
        checked = TestCreate.model_validate(
            {
                "title": "генерация",
                "subject": "генерация",
                "classes": ["-"],
                "variants_count": 1,
                "shuffle": False,
                "skills": skills,
                "variants": [{"variant_no": 1, "tasks": tasks}],
            }
        )
    except ValidationError as exc:
        messages = [describe_error(error) for error in exc.errors()]
        # «Вариант 1, …» тут вводит в заблуждение: номер варианта подставной.
        message = " ; ".join(messages).replace("Вариант 1, ", "").replace("Вариант 1: ", "")
        raise VariantProblem(message) from None

    result = [task.model_dump() for task in checked.variants[0].tasks]

    for number, task in enumerate(result, start=1):
        if not task["solution"]:
            raise VariantProblem(f"у задания {number} нет краткого решения (solution)")

    return result


def parse_variant(raw: str, skills: list[dict], avoid: set[str]) -> list[dict]:
    """Ответ ИИ на промт варианта → проверенные задания (в формате TaskIn)."""
    data = load_json_object(raw)

    # Бывает, что ИИ всё-таки заворачивает вариант в {"variants": [...]}.
    if not isinstance(data.get("tasks"), list) and isinstance(data.get("variants"), list):
        first = data["variants"][0] if data["variants"] else None
        data = first if isinstance(first, dict) else {}

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        raise VariantProblem('в JSON нет списка заданий "tasks"')

    tasks: list[dict] = []
    for number, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise VariantProblem(f"задание {number} должно быть объектом")
        skill = _as_int(raw_task.get("skill"))
        if skill is None or skill < 1:
            raise VariantProblem(f'у задания {number} не указан номер умения (поле "skill")')
        fallback = skills[skill - 1]["answer_format"] if skill <= len(skills) else "input"
        tasks.append(read_task(raw_task, skill, fallback))

    # Порядок заданий — по умениям: так их удобнее проверять в таблице.
    tasks.sort(key=lambda task: task["skill_index"])
    tasks = validate_tasks(skills, tasks)

    seen: set[str] = set()
    for task in tasks:
        key = normalize_text(task["text"])
        if key in avoid:
            raise VariantProblem(
                f"задание «{task['text'][:60]}» повторяет задание другого варианта"
            )
        if key in seen:
            raise VariantProblem(f"задание «{task['text'][:60]}» повторяется внутри варианта")
        seen.add(key)

    return tasks


def parse_single_task(raw: str, skill: dict, avoid: set[str]) -> dict:
    """Ответ ИИ на промт замены → одно проверенное задание."""
    data = load_json_object(raw)
    if isinstance(data.get("tasks"), list) and data["tasks"] and isinstance(data["tasks"][0], dict):
        data = data["tasks"][0]

    task = read_task(data, 1, skill["answer_format"])
    single_skill = {**skill, "tasks_per_variant": 1}
    [task] = validate_tasks([single_skill], [task])

    if normalize_text(task["text"]) in avoid:
        raise VariantProblem("новое задание повторяет уже существующее")
    return task


# =====================================================================
# Самопроверка
# =====================================================================

ANSWER_PREFIX_RE = re.compile(r"^\s*(окончательный\s+)?ответ\s*[:\-–—]?\s*", re.IGNORECASE)
LETTERS = "абвгдежзик"
LATIN = "abcdefghij"


def clean_reply(reply: str) -> str:
    """
    Достаёт сам ответ из реплики модели.

    Модели любят «Ответ: 25.» или «**25**» — срезаем приставку, разметку и
    точку в конце. Если строк несколько, берём последнюю непустую: обычно
    рассуждение выше, а итог — внизу.
    """
    text = strip_reasoning(reply)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    text = lines[-1]
    text = ANSWER_PREFIX_RE.sub("", text)
    text = text.strip().strip("*`$\"'«»").strip()
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1].rstrip()
    return text[:200]


def parse_choice_reply(reply: str, options_count: int) -> int | None:
    """Номер выбранного варианта (с нуля) или None, если не понять."""
    text = clean_reply(reply).lower()
    number = re.search(r"\d+", text)
    if number:
        index = int(number.group()) - 1
        return index if 0 <= index < options_count else None
    if text[:1] and text[:1] in LETTERS[:options_count]:
        return LETTERS.index(text[:1])
    if text[:1] and text[:1] in LATIN[:options_count]:
        return LATIN.index(text[:1])
    return None


def generated_answer(task: dict) -> str:
    """Ответ генерации в виде для подсказки учителю."""
    if task["answer_format"] == "choice":
        index = task["correct"]
        return f"{index + 1}) {task['options'][index]}"
    return " | ".join(task["accepted_answers"])


def self_check(ctx: RequestContext, request: dict, task: dict, cancel: threading.Event) -> dict:
    """
    Решает задание второй моделью и сравнивает ответы.

    Возвращает review: status 'ok' | 'mismatch' | 'unchecked', плюс оба ответа.
    Фатальная ошибка (ключ, деньги) пробрасывается — генерацию надо остановить.
    Прочие сбои проверки → 'unchecked': задание тоже требует взгляда учителя.
    """
    settings = get_settings()
    review = {"status": "unchecked", "generated": generated_answer(task), "checked": ""}

    try:
        _raise_if_cancelled(cancel)
        with _global_slots:
            result = chat(
                ctx,
                kind="check",
                model=settings.ai_check_model_name,
                messages=check_messages(request, task),
                max_tokens=settings.ai_check_max_tokens,
                reasoning_effort=settings.ai_check_reasoning_effort,
            )
    except AIError as error:
        if error.fatal:
            raise
        review["checked"] = "не удалось проверить"
        return review

    try:
        return _compare(task, result.text, review)
    except Exception:  # noqa: BLE001
        # Странный ответ проверяющей модели не должен ронять весь вариант:
        # задание просто уходит учителю на ручную проверку.
        logger.warning("Не удалось сравнить ответ самопроверки", exc_info=True)
        review["status"] = "unchecked"
        review["checked"] = "не удалось проверить"
        return review


def _compare(task: dict, reply: str, review: dict) -> dict:
    """Сравнивает ответ проверяющей модели с ответом генерации."""
    if task["answer_format"] == "choice":
        index = parse_choice_reply(reply, len(task["options"]))
        if index is None:
            review["checked"] = clean_reply(reply) or "пустой ответ"
            review["status"] = "mismatch"
            return review
        review["checked"] = f"{index + 1}) {task['options'][index]}"
        review["status"] = "ok" if index == task["correct"] else "mismatch"
        return review

    answer = clean_reply(reply)
    review["checked"] = answer or "пустой ответ"
    review["status"] = (
        "ok" if answer and check_input_answer(answer, task["accepted_answers"]) else "mismatch"
    )
    return review


def attach_review(task: dict, review: dict) -> dict:
    return {**task, "review": review, "needs_review": review["status"] != "ok"}


# =====================================================================
# Хранение заданий генерации
# =====================================================================


def _raise_if_cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise AIError("Генерация остановлена из-за ошибки ИИ-сервиса.", fatal=True)


def cancel_event(job_id: int) -> threading.Event:
    with _cancel_lock:
        event = _cancel_events.get(job_id)
        if event is None or event.is_set():
            event = threading.Event()
            _cancel_events[job_id] = event
        return event


def _without_nul(value):
    """PostgreSQL не хранит символ \\x00 ни в TEXT, ни в JSONB — вычищаем его везде."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: _without_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_nul(item) for item in value]
    return value


def update_job(job_id: int, change: Callable[[dict], None]) -> dict:
    """
    Меняет задание под блокировкой строки.

    Варианты пишут в один и тот же JSON параллельно; SELECT ... FOR UPDATE
    не даёт двум потокам затереть изменения друг друга.
    change(job) правит словарь job на месте: result, status, error.
    """
    with db.get_pool().connection() as conn, conn.transaction():
        row = conn.execute(
            "SELECT id, kind, status, result, error FROM ai_jobs WHERE id = %s FOR UPDATE",
            (job_id,),
        ).fetchone()
        if row is None:
            # Задание удалили вместе с учётной записью — писать некуда.
            return {"id": job_id, "kind": "", "status": "failed", "result": {}, "error": ""}
        job = {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "result": row["result"] or {},
            "error": row["error"],
        }
        change(job)
        conn.execute(
            """
            UPDATE ai_jobs
            SET result = %s, status = %s, error = %s, updated_at = now(),
                finished_at = CASE WHEN %s = 'running' THEN NULL
                                   ELSE coalesce(finished_at, now()) END
            WHERE id = %s
            """,
            (
                Jsonb(_without_nul(job["result"])),
                job["status"],
                _without_nul(job["error"]),
                job["status"],
                job_id,
            ),
        )
    return job


def load_job_row(job_id: int) -> dict | None:
    with db.get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, teacher_id, kind, status, request, result, error,
                   created_at, updated_at, finished_at
            FROM ai_jobs WHERE id = %s
            """,
            (job_id,),
        ).fetchone()


TERMINAL = ("ok", "failed")


def _complete_if_all_terminal(job: dict) -> None:
    """Все варианты закончились — задание готово (если не упало целиком)."""
    variants = job["result"].get("variants", [])
    if job["status"] == "running" and all(v["status"] in TERMINAL for v in variants):
        job["status"] = "done"


def _set_variant(job_id: int, variant_no: int, **fields) -> None:
    def change(job: dict) -> None:
        variant = job["result"]["variants"][variant_no - 1]
        variant.update(fields)
        _complete_if_all_terminal(job)

    update_job(job_id, change)


def _fail_whole_job(job_id: int, message: str) -> None:
    """Фатальная ошибка: незаконченные варианты — «не удалось», задание — failed."""

    def change(job: dict) -> None:
        for variant in job["result"].get("variants", []):
            if variant["status"] not in TERMINAL:
                variant.update(status="failed", stage="", error=message)
        job["status"] = "failed"
        job["error"] = message

    update_job(job_id, change)


def existing_texts(job_id: int, skip_variant: int) -> tuple[dict[int, list[str]], set[str]]:
    """
    Уже готовые задания других вариантов: по умениям (для промта)
    и нормализованные (для поиска повторов).
    """
    row = load_job_row(job_id)
    by_skill: dict[int, list[str]] = {}
    normalized: set[str] = set()
    for variant in (row["result"] or {}).get("variants", []):
        if variant["variant_no"] == skip_variant:
            continue
        for task in variant.get("tasks", []):
            by_skill.setdefault(task["skill_index"], []).append(task["text"])
            normalized.add(normalize_text(task["text"]))
    return by_skill, normalized


# =====================================================================
# Фоновая работа
# =====================================================================


def generate_variant(
    ctx: RequestContext, request: dict, variant_no: int, cancel: threading.Event
) -> None:
    """
    Один вариант целиком: генерация (с повторами) → самопроверка → запись.

    Фатальные ошибки (AIError.fatal) пробрасываются наверх: их обработает
    run_test_job и остановит всё задание.
    """
    settings = get_settings()
    job_id = ctx.job_id
    assert job_id is not None

    skills = request["skills"]
    previous_error = ""
    tasks: list[dict] | None = None
    attempts = 0

    for attempt in range(1 + GEN_RETRIES):
        attempts = attempt + 1
        _raise_if_cancelled(cancel)
        _set_variant(job_id, variant_no, status="running", stage="generate", attempts=attempts)

        # Снимок готовых заданий берём перед КАЖДОЙ попыткой: пока мы ждали,
        # соседние варианты могли уже закончиться.
        by_skill, avoid = existing_texts(job_id, variant_no)
        try:
            with _global_slots:
                result = chat(
                    ctx,
                    kind="generate",
                    model=settings.ai_model,
                    messages=variant_messages(request, variant_no, by_skill, previous_error),
                    max_tokens=settings.ai_gen_max_tokens,
                )
            if result.finish_reason == "length":
                # Обрыв на max_tokens — JSON почти наверняка недописан.
                logger.info("Генерация %s, вариант %s оборван по max_tokens", job_id, variant_no)
            tasks = parse_variant(result.text, skills, avoid)
            break
        except VariantProblem as problem:
            previous_error = str(problem)
            logger.info(
                "Генерация %s, вариант %s, попытка %s: %s", job_id, variant_no, attempts, problem
            )
        except AIError as error:
            if error.fatal:
                raise
            previous_error = ""
            last_ai_error = error.message
            logger.info(
                "Генерация %s, вариант %s, попытка %s: %s",
                job_id,
                variant_no,
                attempts,
                error.message,
            )
            if attempt == GEN_RETRIES:
                _set_variant(
                    job_id, variant_no, status="failed", stage="", error=last_ai_error, tasks=[]
                )
                return
        except Exception as exc:  # noqa: BLE001
            # Разбор споткнулся о то, чего мы не ждали. Это не «внутренняя ошибка
            # сервера», а непонятный ответ ИИ: повторяем, как при битом JSON.
            previous_error = "ответ ИИ не удалось разобрать"
            logger.warning(
                "Генерация %s, вариант %s, попытка %s: не удалось разобрать ответ (%s: %s)",
                job_id,
                variant_no,
                attempts,
                type(exc).__name__,
                str(exc)[:200],
                exc_info=True,
            )

    if tasks is None:
        _set_variant(
            job_id,
            variant_no,
            status="failed",
            stage="",
            error=f"ИИ не справился за {attempts} попытки: {previous_error}",
            tasks=[],
        )
        return

    _set_variant(job_id, variant_no, stage="check")
    checked = [attach_review(task, self_check(ctx, request, task, cancel)) for task in tasks]

    def finish(job: dict) -> None:
        variant = job["result"]["variants"][variant_no - 1]
        variant.update(
            status="ok",
            stage="",
            error="",
            tasks=checked,
            version=variant.get("version", 0) + 1,
        )
        _complete_if_all_terminal(job)

    update_job(job_id, finish)


def run_test_job(job_id: int, teacher_id: int, variant_numbers: list[int]) -> None:
    """
    Поток задания «вся проверочная работа» (или повтор отдельных вариантов).

    Первый вариант из списка делаем отдельно: остальные получат его задания
    как образец «так уже было» и не будут его повторять. Дальше — параллельно.
    """
    ctx = RequestContext(teacher_id=teacher_id, job_id=job_id)
    cancel = cancel_event(job_id)
    row = load_job_row(job_id)
    request = row["request"]

    def safe(variant_no: int) -> None:
        try:
            generate_variant(ctx, request, variant_no, cancel)
        except AIError as error:
            if error.fatal:
                if not cancel.is_set():
                    cancel.set()
                    _fail_whole_job(job_id, error.message)
                return
            _set_variant(job_id, variant_no, status="failed", stage="", error=error.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Сбой генерации %s, вариант %s", job_id, variant_no)
            # Учителю — без трейсбека, но с типом ошибки: по нему её легко найти в логе.
            _set_variant(
                job_id,
                variant_no,
                status="failed",
                stage="",
                error=(
                    f"Сбой на сервере при генерации варианта ({type(exc).__name__}). "
                    "Подробности — в логе сервера; нажмите «повторить»."
                ),
            )

    try:
        first, rest = variant_numbers[0], variant_numbers[1:]
        safe(first)
        if rest and not cancel.is_set():
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
                list(pool.map(safe, rest))
    finally:
        logger.info("Генерация %s закончена", job_id)


def start_test_job(job_id: int, teacher_id: int, variant_numbers: list[int]) -> None:
    threading.Thread(
        target=run_test_job,
        args=(job_id, teacher_id, variant_numbers),
        name=f"ai-job-{job_id}",
        daemon=True,
    ).start()


def run_task_job(job_id: int, teacher_id: int) -> None:
    """Поток задания «заменить одно задание»: генерация (с повторами) → проверка."""
    settings = get_settings()
    ctx = RequestContext(teacher_id=teacher_id, job_id=job_id)
    cancel = cancel_event(job_id)
    request = load_job_row(job_id)["request"]
    skill = request["skills"][request["skill_index"] - 1]
    avoid = {normalize_text(text) for text in request.get("avoid_texts", [])}

    def fail(message: str) -> None:
        def change(job: dict) -> None:
            job["status"] = "failed"
            job["error"] = message

        update_job(job_id, change)

    previous_error = ""
    task: dict | None = None
    try:
        for attempt in range(1 + GEN_RETRIES):
            try:
                with _global_slots:
                    result = chat(
                        ctx,
                        kind="generate",
                        model=settings.ai_model,
                        messages=task_messages(request, previous_error),
                        max_tokens=settings.ai_gen_max_tokens,
                    )
                task = parse_single_task(result.text, skill, avoid)
                break
            except VariantProblem as problem:
                previous_error = str(problem)
            except AIError as error:
                if error.fatal or attempt == GEN_RETRIES:
                    raise
                previous_error = ""
            except Exception as exc:  # noqa: BLE001
                previous_error = "ответ ИИ не удалось разобрать"
                logger.warning(
                    "Замена задания %s: не удалось разобрать ответ (%s: %s)",
                    job_id,
                    type(exc).__name__,
                    str(exc)[:200],
                    exc_info=True,
                )

        if task is None:
            fail(f"ИИ не справился за {1 + GEN_RETRIES} попытки: {previous_error}")
            return

        task["skill_index"] = request["skill_index"]
        checked = attach_review(task, self_check(ctx, request, task, cancel))

        def done(job: dict) -> None:
            job["result"] = {"task": checked}
            job["status"] = "done"

        update_job(job_id, done)
    except AIError as error:
        fail(error.message)
    except Exception:  # noqa: BLE001
        logger.exception("Сбой замены задания %s", job_id)
        fail("Сбой на сервере при генерации задания — подробности в логе сервера.")


def start_task_job(job_id: int, teacher_id: int) -> None:
    threading.Thread(
        target=run_task_job,
        args=(job_id, teacher_id),
        name=f"ai-task-{job_id}",
        daemon=True,
    ).start()


def recover_interrupted_jobs() -> None:
    """
    При старте сервера: задания, которые шли в момент остановки, уже никто
    не доделает — потоки умерли вместе с процессом. Помечаем незаконченные
    варианты «не удалось», чтобы учитель мог нажать «повторить».

    Работает при одном процессе uvicorn (так и запускается в Docker).
    """
    try:
        with db.get_pool().connection() as conn:
            rows = conn.execute("SELECT id FROM ai_jobs WHERE status = 'running'").fetchall()
    except Exception:  # noqa: BLE001
        # Например, миграция 008 ещё не накачена — это не повод не стартовать.
        logger.warning("Не удалось проверить незаконченные задания генерации", exc_info=True)
        return

    for row in rows:

        def change(job: dict) -> None:
            if job["kind"] == "task":
                job["status"] = "failed"
                job["error"] = INTERRUPTED
                return
            for variant in job["result"].get("variants", []):
                if variant["status"] not in TERMINAL:
                    variant.update(status="failed", stage="", error=INTERRUPTED)
            _complete_if_all_terminal(job)

        update_job(row["id"], change)

    if rows:
        logger.info("Незаконченных заданий генерации после перезапуска: %s", len(rows))


# =====================================================================
# Вид задания для ответа API
# =====================================================================


def job_view(row: dict) -> dict:
    """Статус и результат задания для фронтенда. Ничего секретного тут нет."""
    result = row["result"] or {}
    variants = result.get("variants", [])
    ok = sum(1 for v in variants if v["status"] == "ok")
    failed = sum(1 for v in variants if v["status"] == "failed")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "error": row["error"],
        "request": row["request"],
        "total": len(variants),
        "done": ok + failed,
        "ok": ok,
        "failed": failed,
        "variants": variants,
        "task": result.get("task"),
        "needs_review": sum(
            1 for v in variants for t in v.get("tasks", []) if t.get("needs_review")
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def initial_variants(variants_count: int) -> list[dict]:
    return [
        {
            "variant_no": number,
            "status": "pending",
            "stage": "",
            "attempts": 0,
            "version": 0,
            "error": "",
            "tasks": [],
        }
        for number in range(1, variants_count + 1)
    ]

