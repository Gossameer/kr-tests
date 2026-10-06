"""
Встроенная генерация вариантов через ИИ.

Как устроено:
  1. Учитель нажимает «Сгенерировать» — в ai_jobs появляется задание, а работа
     идёт в фоновых потоках. Страница опрашивает статус и может уйти/вернуться:
     всё состояние лежит в базе.
  2. Единица работы — КЛЕТКА таблицы: задания одного умения в одном варианте.
     Один запрос к ИИ = одно умение сразу для нескольких вариантов («составь
     задания этого умения для вариантов 1–4, все разные»). Так модель сама
     видит соседние варианты и не повторяется, а ответ короткий и приходит
     быстро. Умения генерируются параллельно.
     Если вариантов много, умение делится на части по вариантам; части идут
     друг за другом, и в следующую передаются задания предыдущих.
  3. Ответ разбираем так же, как фронтенд разбирает вставленный вручную JSON,
     а полноту проверяет та же схема TestCreate, что и при публикации.
     Не хватает варианта или он битый — повторяем ТОЛЬКО недостающее
     (до GEN_RETRIES раз), остальное остаётся.
  4. Самопроверка: каждое задание отдельно решает AI_CHECK_MODEL, НЕ видя
     правильного ответа. Она стартует сразу, как клетка сгенерирована, — не
     дожидаясь остальных, в том же общем лимите одновременных запросов
     (AI_MAX_CONCURRENCY). Не совпало — задание помечается needs_review.
  5. Клетка появляется у учителя, как только готова (составлена и проверена).
  6. Если сервер перезапустили посреди работы, незаконченные клетки помечаются
     «прервано»; готовое сохраняется, недостающее можно догенерировать.
  7. Ничего не публикуется само: результат уходит в таблицу проверки.
"""

import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app import db
from app.ai_client import AIError, RequestContext, chat
from app.answers import check_input_answer
from app.config import get_settings
from app.errors import describe_error
from app.formulas import formula_problem, plain_text
from app.schemas import TestCreate

logger = logging.getLogger(__name__)

# Сколько раз повторяем запрос после первой неудачной попытки.
GEN_RETRIES = 2
# Сколько уже готовых заданий умения показываем ИИ, чтобы он не повторялся.
# Больше — длиннее запрос и дороже, а пользы почти не добавляет.
AVOID_PER_SKILL = 15
AVOID_TEXT_LEN = 200

# Грубая оценка длины одного задания в ответе (текст + ответ + решение + JSON),
# в токенах. Кириллица «дорогая»: слово — это 2–4 токена. По оценке решаем,
# сколько вариантов умения просить за раз, чтобы ответ влез в AI_GEN_MAX_TOKENS.
TOKENS_PER_TASK = {"input": 170, "choice": 230}
# Какую долю AI_GEN_MAX_TOKENS считаем доступной под задания (остальное — запас).
TOKEN_BUDGET_SHARE = 0.8

# Общий лимит одновременных запросов к ИИ на весь сервер (AI_MAX_CONCURRENCY):
# генерация и самопроверка берут слоты из одного мешка. Создаётся при первом
# обращении — к этому моменту настройки уже прочитаны.
_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None
_check_pool: ThreadPoolExecutor | None = None

# Задания, которые прямо сейчас выполняет ЭТОТ процесс. Если задание в базе
# числится «идёт», а здесь его нет — потоки умерли вместе с прошлым процессом.
_live_jobs: set[int] = set()
_live_lock = threading.Lock()


def http_slots() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max(1, get_settings().ai_max_concurrency))
        return _slots


def check_pool() -> ThreadPoolExecutor:
    """Потоки самопроверки. Их больше, чем слотов: лишние просто ждут слот."""
    global _check_pool
    with _slots_lock:
        if _check_pool is None:
            _check_pool = ThreadPoolExecutor(
                max_workers=max(4, get_settings().ai_max_concurrency * 2),
                thread_name_prefix="ai-check",
            )
        return _check_pool


def is_live(job_id: int) -> bool:
    with _live_lock:
        return job_id in _live_jobs


# Флаги остановки заданий: при фатальной ошибке (нет денег, ключ не принят)
# остальные потоки этого задания перестают слать запросы.
_cancel_events: dict[int, threading.Event] = {}
_cancel_lock = threading.Lock()

INTERRUPTED = "Генерация прервалась: сервер перезапустили. Нажмите «Догенерировать недостающее»."


class VariantProblem(Exception):
    """Ответ ИИ не годится: битый JSON, неполный вариант, повтор заданий."""


# =====================================================================
# Промты
# =====================================================================


def format_rule(answer_format: str, stress: bool = False) -> str:
    """Понятное ИИ описание формата ответа (как в промте на фронтенде)."""
    if answer_format == "choice" and stress:
        # У слова из двух слогов четырёх разных ударений не бывает.
        return (
            '"format": "choice", от 2 до 4 вариантов ответа в "options" '
            'и номер верного в "correct" (с нуля)'
        )
    if answer_format == "choice":
        return (
            '"format": "choice", 4 варианта ответа в "options" '
            'и номер верного в "correct" (с нуля)'
        )
    return '"format": "input", список допустимых ответов в "answers"'


def subject_line(request: dict) -> str:
    parts = [request.get("subject", "").strip(), request.get("topic", "").strip()]
    return ", ".join(part for part in parts if part) or "не указаны"


# Школьная запись: так пишут в учебниках, и ученик не спотыкается о «*» и «/».
SCHOOL_NOTATION = (
    "математические выражения в тексте задания, вариантах ответа и решении пиши формулами "
    "LaTeX строго между знаками доллара: $...$ (другие обозначения формул не используй, "
    "каждый открытый $ закрывай); запись школьная: умножение — \\cdot ($3 \\cdot 4$), "
    "деление — двоеточие ($12 : 3$), дроби — \\frac ($\\frac{3}{5}$), смешанные числа — "
    "$2\\frac{1}{3}$, десятичная запятая — {,} ($2{,}5$), степень — $x^{2}$, корень — "
    "$\\sqrt{x}$; не используй «*» и «/». В JSON обратную черту удваивай: "
    '"$\\\\frac{3}{5}$". В "answers" разметки НЕТ: ответ записан так, как его наберёт '
    "ученик с клавиатуры — 3/5, 2 1/3, 2,5, -4, без $ и без команд LaTeX."
)

# Ответ в условии — задание ничего не проверяет («Поставьте ударение: звонИт»).
NO_ANSWER_IN_TEXT = (
    "НИКОГДА не включай ответ в текст задания: в условии не должно быть ни правильного "
    "ответа, ни подсказки, которая его выдаёт (например, слова с уже выделенной ударной буквой)"
)

# Умения про ударение: правильный ответ отличается от неправильного только тем,
# какая буква заглавная. Вводом такое не проверить — только выбором.
STRESS_RE = re.compile(r"ударен|орфоэп", re.IGNORECASE)

STRESS_CHOICE = (
    "Это задание на постановку ударения. В тексте задания слово пиши строчными буквами, "
    "без выделения ударной гласной. Варианты ответа в \"options\" — ОДНО И ТО ЖЕ слово с "
    "ударением на разных слогах: ударная гласная ЗАГЛАВНОЙ буквой, остальные строчные "
    "(например: \"звОнит\", \"звонИт\"). Вариантов столько, сколько в слове гласных, "
    "но не меньше 2 и не больше 4. В каждом задании — своё слово."
)
STRESS_INPUT = (
    "Это задание на постановку ударения. В тексте задания слово пиши строчными буквами, "
    "без выделения ударной гласной. В \"answers\" — это слово с ударной гласной ЗАГЛАВНОЙ "
    "буквой, остальные строчные (например: \"звонИт\"). В каждом задании — своё слово."
)


def is_stress_skill(title: str) -> bool:
    return bool(STRESS_RE.search(title))


def stress_rule(skill: dict) -> str:
    """Дополнительное требование для умений про ударение (или пустая строка)."""
    if not is_stress_skill(skill["title"]):
        return ""
    return STRESS_CHOICE if skill["answer_format"] == "choice" else STRESS_INPUT


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


def variant_list(numbers: list[int]) -> str:
    """[1, 2, 3, 4] → «1–4», [2, 5] → «2, 5» — для промта и для лога."""
    if len(numbers) > 2 and numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"{numbers[0]}–{numbers[-1]}"
    return ", ".join(str(number) for number in numbers)


def skill_messages(
    request: dict,
    skill_index: int,
    variant_numbers: list[int],
    existing: list[str],
    previous_error: str,
) -> list[dict]:
    """
    Промт на ОДНО умение сразу для нескольких вариантов.

    Модель видит все варианты разом, поэтому сама делает их разными —
    передавать «уже было» нужно только из предыдущих частей и при повторе.
    """
    skill = request["skills"][skill_index - 1]
    count = skill["tasks_per_variant"]
    shape = TASK_SHAPE_CHOICE if skill["answer_format"] == "choice" else TASK_SHAPE_INPUT
    shape = shape.replace('"skill": 2,\n  ', "").replace('"skill": 1,\n  ', "")
    avoid = [text[:AVOID_TEXT_LEN] for text in existing][-AVOID_PER_SKILL:]

    parts = [
        "Составь задания для проверочной работы: одно умение, несколько вариантов.",
        f"Предмет и тема: {subject_line(request)}",
        f"Класс: {request.get('grade', '').strip() or 'не указан'}",
        f"Проверяемое умение: {skill['title']}",
        f"Формат ответа: {format_rule(skill['answer_format'], is_stress_skill(skill['title']))}",
        f"Варианты: {variant_list(variant_numbers)} "
        f"(всего вариантов в работе: {request['variants_count']}).",
        f"В КАЖДОМ варианте заданий на это умение: ровно {count}.",
        "",
        "Требования:",
        "- задания во всех вариантах РАЗНЫЕ: другие числа, данные, формулировки и примеры, "
        "но тот же тип и та же сложность — варианты должны быть равноценны;",
        "- у заданий с вводом ответа перечисли в \"answers\" все правильные формы записи "
        '(например "0,5" и "0.5"), ответ короткий — число или несколько слов;',
        '- к КАЖДОМУ заданию краткое решение в "solution" (1–2 строки, для учителя);',
        "- задания должны быть решаемы и иметь однозначный ответ;",
        f"- {NO_ANSWER_IN_TEXT};",
        f"- {SCHOOL_NOTATION}",
    ]
    if stress_rule(skill):
        parts += ["", stress_rule(skill)]

    if avoid:
        parts += ["", "Такие задания этого умения уже есть — НЕ повторяй их:"]
        parts += [f"- {text}" for text in avoid]

    if previous_error:
        parts += ["", f"Прошлый ответ не подошёл: {previous_error}. Исправь это."]

    example = ", ".join(
        f'{{"variant": {number}, "tasks": [{"..." if position else shape}]}}'
        for position, number in enumerate(variant_numbers[:2])
    )
    parts += [
        "",
        "Верни ТОЛЬКО JSON такой структуры (по одному объекту на каждый вариант из списка):",
        f'{{"variants": [{example}]}}',
        "",
        'Поле "variant" — номер варианта. Поле "correct" — номер правильного '
        'варианта в "options", нумерация с нуля.',
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
        f"Формат ответа: {format_rule(skill['answer_format'], is_stress_skill(skill['title']))}",
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
        NO_ANSWER_IN_TEXT + ".",
        SCHOOL_NOTATION[:1].upper() + SCHOOL_NOTATION[1:],
        stress_rule(skill),
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
            "без решения и пояснений. Ответ запиши без разметки, как с клавиатуры: "
            "дробь — 3/5, смешанное число — 2 1/3, десятичная дробь — 2,5."
        )
        if case_only_answer(task):
            # Как записать ударение — не подсказка: какая гласная ударная, модель решает сама.
            user += (
                " Если нужно поставить ударение, напиши слово строчными буквами, "
                "а ударную гласную — ЗАГЛАВНОЙ."
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
    """
    Заменяет «*» на школьный знак умножения в тексте, который увидит человек:
    в обычном тексте — «·», внутри формулы $…$ — команду \\cdot.
    """

    def replace(match: re.Match) -> str:
        inside_formula = text.count("$", 0, match.start()) % 2 == 1
        return " \\cdot " if inside_formula else " · "

    return STAR_RE.sub(replace, text)


# Обратная черта в JSON, после которой идёт не то, что положено по стандарту:
# «\c» из «\cdot», «\{», «\,». И команды LaTeX, которые начинаются как законная
# запись JSON и молча превратились бы в мусор: «\frac» — в «перевод страницы + rac»,
# «\times» — в «табуляцию + imes». Модели часто забывают удвоить черту.
LATEX_IN_JSON_RE = re.compile(
    r"(?<!\\)((?:\\\\)*)\\(?="
    r"[^\"\\/bfnrtu]"
    r"|(?:frac|beta|bar|begin|bullet|backslash|big|forall|neq?|nu|notin|nabla|rho|right|"
    r"rightarrow|rbrace|rangle|times|tau|theta|text|textrm|textbf|textit|tfrac|to|tg|tan|"
    r"th|triangle|textstyle)(?![a-zA-Z])"
    r")"
)


def fix_latex_escapes(raw: str) -> str:
    """Удваивает обратную черту перед командами LaTeX в тексте JSON."""
    return LATEX_IN_JSON_RE.sub(lambda match: match.group(1) + "\\\\", raw)


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
        # Ответ для ввода — без разметки: ученик наберёт «3/5», а не «$\frac{3}{5}$».
        "accepted_answers": (
            [plain_text(answer).strip() for answer in _as_string_list(source.get("answers"))]
            if answer_format == "input"
            else []
        ),
        "solution": school_signs(_as_string(source.get("solution"))),
    }


def load_json_object(raw: str) -> dict:
    if not raw.strip():
        raise VariantProblem("пустой ответ")
    try:
        data = json.loads(fix_latex_escapes(extract_json_block(raw)))
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


def parse_skill_answer(
    raw: str,
    skill: dict,
    skill_index: int,
    variant_numbers: list[int],
    avoid: set[str],
) -> tuple[dict[int, list[dict]], dict[int, str]]:
    """
    Ответ ИИ на промт умения → (готовые варианты, проблемы по вариантам).

    Каждый вариант проверяется ОТДЕЛЬНО: если из четырёх один битый, три
    хороших принимаем, а повторять будем только недостающий. Ответ, который
    не читается вовсе (битый JSON), бросает VariantProblem — тогда повторяется
    весь запрос.
    """
    data = load_json_object(raw)

    entries = data.get("variants")
    if not isinstance(entries, list):
        # Один вариант модель иногда отдаёт без обёртки: {"tasks": [...]}.
        if isinstance(data.get("tasks"), list) and len(variant_numbers) == 1:
            entries = [{"variant": variant_numbers[0], "tasks": data["tasks"]}]
        else:
            raise VariantProblem('в JSON нет списка "variants"')

    entries = [entry for entry in entries if isinstance(entry, dict)]
    by_number: dict[int, dict] = {}
    for entry in entries:
        number = _as_int(entry.get("variant"))
        if number is not None and number not in by_number:
            by_number[number] = entry
    # Номера не указаны, но объектов ровно столько, сколько просили, — по порядку.
    if not any(number in by_number for number in variant_numbers) and len(entries) == len(
        variant_numbers
    ):
        by_number = dict(zip(variant_numbers, entries))

    single_skill = {**skill, "tasks_per_variant": skill["tasks_per_variant"]}
    ready: dict[int, list[dict]] = {}
    problems: dict[int, str] = {}
    seen = set(avoid)

    for number in variant_numbers:
        entry = by_number.get(number)
        if entry is None:
            problems[number] = f"в ответе нет варианта {number}"
            continue
        raw_tasks = entry.get("tasks")
        if not isinstance(raw_tasks, list) or not all(isinstance(t, dict) for t in raw_tasks):
            problems[number] = f'у варианта {number} нет списка заданий "tasks"'
            continue
        try:
            tasks = validate_tasks(
                [single_skill],
                [read_task(raw_task, 1, skill["answer_format"]) for raw_task in raw_tasks],
            )
        except VariantProblem as problem:
            problems[number] = f"вариант {number}: {problem}"
            continue

        keys = [normalize_text(task["text"]) for task in tasks]
        if any(key in seen for key in keys) or len(set(keys)) != len(keys):
            problems[number] = f"вариант {number}: задание повторяет уже существующее"
            continue

        seen.update(keys)
        for task in tasks:
            task["skill_index"] = skill_index
        ready[number] = tasks

    return ready, problems


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


# --- Ударение -----------------------------------------------------------------
#
# Одно и то же ударение записывают по-разному: «звонИт», «звони́т» (знак
# ударения после буквы), «звонúт»/«каталóг» (латинская буква со штрихом).
# Проверяющая модель обычно ставит знак ударения, а генерация — заглавную букву,
# и обычное сравнение считало бы это расхождением.

ACUTE_MARKS = ("\u0301", "\u0300", "\u00b4")
# Латинские буквы со штрихом, которыми модели подменяют кириллические.
ACCENTED_LATIN = {"á": "а", "é": "е", "ó": "о", "ý": "у", "ú": "и", "í": "і"}
VOWELS = "аеёиоуыэюя"


def stress_key(raw: str) -> tuple[str, int] | None:
    """
    (слово строчными, номер ударной буквы) — или None, если это не слово
    с отмеченным ударением. Понимает знак ударения, латинскую букву
    со штрихом и одну заглавную гласную в слове из строчных.
    """
    text = unicodedata.normalize("NFC", raw.strip().strip("«»\"'.,!"))
    if not text or " " in text:
        return None

    letters: list[str] = []
    marked: list[int] = []
    for char in text:
        if char in ACUTE_MARKS:
            if letters:
                marked.append(len(letters) - 1)
        elif char.lower() in ACCENTED_LATIN:
            letters.append(ACCENTED_LATIN[char.lower()])
            marked.append(len(letters) - 1)
        else:
            letters.append(char)
    word = "".join(letters)

    if not marked:
        upper = [index for index, char in enumerate(word) if char.isupper()]
        if len(upper) == 1 and len(word) > 1 and word[upper[0]].lower() in VOWELS:
            marked = upper
    if len(marked) != 1 or not any(char.isalpha() for char in word):
        return None
    return word.lower().replace("ё", "е"), marked[0]


def is_stress_answer(answer: str) -> bool:
    """
    Это слово с отмеченным ударением («звонИт», «звони́т»), а не просто слово
    с заглавной буквы: «Иван» и «Африка» — обычные ответы.
    """
    answer = answer.strip()
    return stress_key(answer) is not None and answer != answer.capitalize()


def case_only_answer(task: dict) -> bool:
    """Верный ответ отличается от неверного только регистром буквы («звонИт»)?"""
    answers = task["accepted_answers"] if task["answer_format"] == "input" else []
    return any(is_stress_answer(answer) and answer != answer.lower() for answer in answers)


def _in_text(answer: str, text: str) -> bool:
    """Есть ли ответ в тексте задания. Для слов с ударением важен регистр."""
    answer = answer.strip()
    # Числа не смотрим: «Найдите 25 % от 100» с ответом 25 — нормальное задание.
    if len(answer) < 3 or not any(char.isalpha() for char in answer):
        return False
    if is_stress_answer(answer):
        return answer in text
    pattern = r"(?<!\w)" + re.escape(answer.lower().replace("ё", "е")) + r"(?!\w)"
    return re.search(pattern, text.lower().replace("ё", "е")) is not None


def answer_visible(task: dict) -> bool:
    """Виден ли правильный ответ прямо в условии."""
    text = task["text"]
    if task["answer_format"] == "input":
        return any(_in_text(answer, text) for answer in task["accepted_answers"])
    options = task["options"]
    correct = options[task["correct"]]
    # В «выборе» варианты иногда перечислены в самом условии — это нормально.
    # Подозрительно, когда в условии есть ТОЛЬКО верный.
    return _in_text(correct, text) and not all(
        _in_text(option, text) for index, option in enumerate(options) if index != task["correct"]
    )


def task_warning(task: dict) -> str:
    """Что не так с самим заданием (не с ответом): причина для пометки needs_review."""
    warnings: list[str] = []
    if answer_visible(task):
        warnings.append("ответ виден в условии")
    if case_only_answer(task):
        warnings.append(
            "ввод не различает регистр — засчитается любое ударение; "
            "переключите умение на «выбор»"
        )
    # Битая формула: ученик увидит сырую разметку вместо дроби.
    for piece in (task["text"], *task["options"], task["solution"]):
        problem = formula_problem(piece)
        if problem:
            warnings.append(f"ошибка в формуле: {problem}")
            break
    return "; ".join(warnings)


def self_check(
    ctx: RequestContext,
    request: dict,
    task: dict,
    cancel: threading.Event,
    label: str = "",
) -> dict:
    """
    Решает задание второй моделью и сравнивает ответы.

    Возвращает review: status 'ok' | 'mismatch' | 'unchecked', плюс оба ответа.
    Фатальная ошибка (ключ, деньги) пробрасывается — генерацию надо остановить.
    Прочие сбои → 'unchecked', а в «checked» — короткая причина для учителя
    («нет связи с ИИ-сервисом»), подробность — в логе и журнале запросов.

    Временные сбои (обрыв связи, таймаут, 429, 5xx) повторяет сам chat().
    Здесь добавляется один повтор на случай, когда лимит токенов целиком ушёл
    на размышления и ответ пришёл пустым: пробуем ещё раз с лимитом вдвое больше.
    """
    settings = get_settings()
    review = {"status": "unchecked", "generated": generated_answer(task), "checked": ""}
    max_tokens = settings.ai_check_max_tokens
    result = None

    for attempt in range(2):
        try:
            _raise_if_cancelled(cancel)
            with http_slots():
                result = chat(
                    ctx,
                    kind="check",
                    model=settings.ai_check_model_name,
                    messages=check_messages(request, task),
                    max_tokens=max_tokens,
                    reasoning_effort=settings.ai_check_reasoning_effort,
                    label=label + (", лимит ×2" if attempt else ""),
                )
            break
        except AIError as error:
            if error.fatal:
                raise
            if error.truncated and attempt == 0:
                max_tokens *= 2
                logger.info(
                    "Самопроверка (%s): лимит токенов ушёл на размышления — "
                    "повторяем с лимитом %s",
                    label or "—",
                    max_tokens,
                )
                continue
            review["checked"] = error.short
            logger.warning("Самопроверка не выполнена (%s): %s", label or "—", error.reason)
            return review

    try:
        return _compare(task, result.text, review)
    except Exception:  # noqa: BLE001
        # Странный ответ проверяющей модели не должен ронять всю клетку:
        # задание просто уходит учителю на ручную проверку.
        logger.warning("Не удалось сравнить ответ самопроверки (%s)", label or "—", exc_info=True)
        review["status"] = "unchecked"
        review["checked"] = "непонятный ответ ИИ"
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

    # Ударение сравниваем по смыслу: «каталóг», «катало́г» и «каталОг» — одно и то же,
    # а «каталог» строчными — это не ответ на вопрос об ударении.
    expected = [stress_key(item) for item in task["accepted_answers"] if is_stress_answer(item)]
    if expected:
        review["status"] = "ok" if stress_key(answer) in expected else "mismatch"
        return review

    review["status"] = (
        "ok" if answer and check_input_answer(answer, task["accepted_answers"]) else "mismatch"
    )
    return review


def attach_review(task: dict, review: dict) -> dict:
    """
    Задание + итог проверки. needs_review — учителю стоит посмотреть:
    самопроверка не сошлась или не выполнилась, либо с самим заданием что-то
    не так (ответ виден в условии, ввод не различит ударение).
    """
    warning = task_warning(task)
    return {
        **task,
        "review": {**review, "warning": warning},
        "needs_review": review["status"] != "ok" or bool(warning),
    }


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


# Клетка закончена: готова, не удалась или прервана перезапуском сервера.
CELL_DONE = ("ok", "failed", "interrupted")


def new_cell(variant_no: int, skill_index: int) -> dict:
    return {
        "variant_no": variant_no,
        "skill_index": skill_index,
        # pending → running (составляется) → checking (самопроверка) → ok;
        # failed — ИИ не справился; interrupted — сервер перезапустили.
        "status": "pending",
        "attempts": 0,
        # Растёт при каждой готовой версии клетки: фронтенд переносит клетку
        # в таблицу один раз и не затирает правки учителя при следующем опросе.
        "version": 0,
        "error": "",
        "tasks": [],
    }


def initial_result(variants_count: int, skills_count: int) -> dict:
    """Пустой результат: по клетке на каждую пару «умение × вариант»."""
    return {
        "variants_count": variants_count,
        "skills_count": skills_count,
        "interrupted": False,
        "cells": [
            new_cell(variant_no, skill_index)
            for skill_index in range(1, skills_count + 1)
            for variant_no in range(1, variants_count + 1)
        ],
    }


def ensure_cells(result: dict, request: dict) -> dict:
    """
    Приводит результат к клеткам.

    Задания, созданные до перехода на клетки, хранили результат по вариантам:
    {"variants": [{variant_no, status, tasks}]}. Раскладываем их по клеткам,
    чтобы старые черновики открывались и их можно было догенерировать.
    """
    if "cells" in result:
        return result

    skills_count = len(request.get("skills", []))
    old_variants = result.get("variants", [])
    converted = initial_result(len(old_variants), skills_count)
    for cell in converted["cells"]:
        old = old_variants[cell["variant_no"] - 1]
        tasks = [t for t in old.get("tasks", []) if t.get("skill_index") == cell["skill_index"]]
        if old.get("status") == "ok" and tasks:
            cell.update(status="ok", tasks=tasks, version=max(1, old.get("version", 1)))
        elif old.get("status") == "failed":
            cell.update(status="failed", error=old.get("error", ""))
    return converted


def cell_of(result: dict, variant_no: int, skill_index: int) -> dict:
    return result["cells"][(skill_index - 1) * result["variants_count"] + (variant_no - 1)]


def _complete_if_all_done(job: dict) -> None:
    """Все клетки закончились — задание готово (если не упало целиком)."""
    cells = job["result"].get("cells", [])
    if job["status"] == "running" and all(cell["status"] in CELL_DONE for cell in cells):
        job["status"] = "done"


def _set_cell(job_id: int, variant_no: int, skill_index: int, **fields) -> None:
    def change(job: dict) -> None:
        cell_of(job["result"], variant_no, skill_index).update(fields)
        _complete_if_all_done(job)

    update_job(job_id, change)


def _fail_whole_job(job_id: int, message: str) -> None:
    """Фатальная ошибка: незаконченные клетки — «не удалось», задание — failed."""

    def change(job: dict) -> None:
        for cell in job["result"].get("cells", []):
            if cell["status"] not in CELL_DONE:
                cell.update(status="failed", error=message, tasks=[])
        job["status"] = "failed"
        job["error"] = message

    update_job(job_id, change)


def skill_texts(job_id: int, skill_index: int) -> list[str]:
    """Тексты уже принятых заданий умения по всем вариантам — «так уже было»."""
    row = load_job_row(job_id)
    return [
        task["text"]
        for cell in (row["result"] or {}).get("cells", [])
        if cell["skill_index"] == skill_index
        for task in cell.get("tasks", [])
    ]


def chunk_size(skill: dict) -> int:
    """
    Сколько вариантов умения просить одним запросом.

    Не больше AI_GEN_CHUNK_VARIANTS и не больше, чем влезет в AI_GEN_MAX_TOKENS:
    если заданий на умение много, ответ на все варианты сразу оборвётся.
    """
    settings = get_settings()
    per_variant = TOKENS_PER_TASK[skill["answer_format"]] * skill["tasks_per_variant"]
    fits = int(settings.ai_gen_max_tokens * TOKEN_BUDGET_SHARE // per_variant)
    return max(1, min(settings.ai_gen_chunk_variants, fits))


# =====================================================================
# Фоновая работа
# =====================================================================


class JobRun:
    """Один запуск генерации: общий контекст для потоков умений и самопроверок."""

    def __init__(self, job_id: int, teacher_id: int, request: dict) -> None:
        self.job_id = job_id
        self.request = request
        self.ctx = RequestContext(teacher_id=teacher_id, job_id=job_id)
        self.cancel = cancel_event(job_id)
        self.started = time.monotonic()
        self._futures: list[Future] = []
        self._lock = threading.Lock()

    def add_futures(self, futures: list[Future]) -> None:
        with self._lock:
            self._futures.extend(futures)

    def wait_checks(self) -> None:
        """Ждёт все самопроверки, включая те, что добавились, пока ждали."""
        while True:
            with self._lock:
                pending = [future for future in self._futures if not future.done()]
            if not pending:
                return
            wait(pending)

    def fail_fatal(self, message: str) -> None:
        """Ключ не принят, кончились деньги и т. п. — останавливаем всё задание."""
        if not self.cancel.is_set():
            self.cancel.set()
            _fail_whole_job(self.job_id, message)


def accept_cell(run: JobRun, skill_index: int, variant_no: int, tasks: list[dict]) -> None:
    """
    Клетка сгенерирована: сохраняем задания и СРАЗУ запускаем самопроверку
    каждого — в общем пуле, не дожидаясь остальных клеток и умений.
    Когда проверены все задания клетки, она становится готовой.
    """
    _set_cell(run.job_id, variant_no, skill_index, status="checking", tasks=tasks, error="")

    reviews: list[dict | None] = [None] * len(tasks)
    lock = threading.Lock()
    left = [len(tasks)]

    def check_one(position: int, task: dict) -> None:
        try:
            review = self_check(
                run.ctx,
                run.request,
                task,
                run.cancel,
                label=f"умение {skill_index}, вариант {variant_no}, задание {position + 1}",
            )
        except AIError as error:
            if error.fatal:
                run.fail_fatal(error.message)
            review = None
        except Exception:  # noqa: BLE001
            logger.warning("Сбой самопроверки задания", exc_info=True)
            review = None
        if review is None:
            review = {
                "status": "unchecked",
                "generated": generated_answer(task),
                "checked": "сбой на сервере",
            }

        with lock:
            reviews[position] = review
            left[0] -= 1
            finished = left[0] == 0
        if not finished:
            return

        def finish(job: dict) -> None:
            cell = cell_of(job["result"], variant_no, skill_index)
            # Клетку могли уже пометить «не удалось» (фатальная ошибка) — не воскрешаем.
            if cell["status"] != "checking":
                return
            cell.update(
                status="ok",
                error="",
                tasks=[attach_review(t, r) for t, r in zip(tasks, reviews)],
                version=cell.get("version", 0) + 1,
            )
            _complete_if_all_done(job)

        update_job(run.job_id, finish)

    pool = check_pool()
    run.add_futures([pool.submit(check_one, position, task) for position, task in enumerate(tasks)])


def generate_chunk(run: JobRun, skill_index: int, variant_numbers: list[int]) -> None:
    """
    Одно умение для нескольких вариантов: запрос → разбор → приём готовых клеток.
    Повторяем только те варианты, которых не хватило (до GEN_RETRIES раз).

    Временные сбои связи повторяет сам chat(); сюда они доходят, только когда
    повторы исчерпаны, — тогда запрос больше не мучаем. Если ответ оборван по
    лимиту токенов, просим вдвое меньше вариантов за раз.

    В лог пишется, ПОЧЕМУ понадобился повтор: что именно было не так с ответом.
    """
    settings = get_settings()
    skill = run.request["skills"][skill_index - 1]
    need = list(variant_numbers)
    problems: dict[int, str] = {}
    previous_error = ""

    for attempt in range(1 + GEN_RETRIES):
        _raise_if_cancelled(run.cancel)
        for variant_no in need:
            _set_cell(run.job_id, variant_no, skill_index, status="running", attempts=attempt + 1)

        # Готовые задания умения берём перед КАЖДОЙ попыткой: туда уже попали
        # и предыдущие части, и принятые варианты этой части.
        existing = skill_texts(run.job_id, skill_index)
        label = f"умение {skill_index}, варианты {variant_list(need)}" + (
            f", повтор {attempt}" if attempt else ""
        )
        ready: dict[int, list[dict]] = {}
        result = None
        truncated = False
        give_up = False
        try:
            with http_slots():
                result = chat(
                    run.ctx,
                    kind="generate",
                    model=settings.ai_model,
                    messages=skill_messages(
                        run.request, skill_index, need, existing, previous_error
                    ),
                    max_tokens=settings.ai_gen_max_tokens,
                    thinking=False if settings.ai_gen_thinking_off else None,
                    label=label,
                )
            truncated = result.finish_reason == "length"
            ready, problems = parse_skill_answer(
                result.text, skill, skill_index, need, {normalize_text(t) for t in existing}
            )
        except VariantProblem as problem:
            problems = {number: str(problem) for number in need}
        except AIError as error:
            if error.fatal:
                raise
            truncated = error.truncated
            # Связь так и не появилась за все повторы — третий заход ничего не даст.
            give_up = error.exhausted
            problems = {number: error.message for number in need}
        except Exception as exc:  # noqa: BLE001
            # Разбор споткнулся о то, чего мы не ждали: это непонятный ответ ИИ,
            # а не «внутренняя ошибка сервера» — повторяем, как при битом JSON.
            logger.warning(
                "Генерация %s, %s: не удалось разобрать ответ (%s: %s)",
                run.job_id,
                label,
                type(exc).__name__,
                str(exc)[:200],
                exc_info=True,
            )
            problems = {number: "ответ ИИ не удалось разобрать" for number in need}

        for variant_no, tasks in ready.items():
            accept_cell(run, skill_index, variant_no, tasks)

        missing = [number for number in need if number not in ready]
        if not missing:
            return

        # Что именно не подошло — без повторов одной и той же фразы.
        previous_error = "; ".join(dict.fromkeys(problems.get(n, "") for n in missing))[:300]
        detail = ""
        if truncated:
            tokens = f"{result.completion_tokens} токенов" if result else "ответ пуст"
            thinking = f", из них размышления {result.reasoning_tokens}" if result and result.reasoning_tokens else ""
            detail = (
                f" Ответ оборван по лимиту AI_GEN_MAX_TOKENS={settings.ai_gen_max_tokens} "
                f"({tokens}{thinking})."
            )
        # Оборванный ответ на несколько вариантов не повторяем как есть, а делим.
        split = truncated and len(missing) > 1 and not give_up
        last_try = attempt == GEN_RETRIES or give_up
        logger.warning(
            "Генерация %s, %s: ответ не подошёл — %s.%s Не хватает вариантов: %s. %s",
            run.job_id,
            label,
            previous_error,
            detail,
            variant_list(missing),
            "Просим варианты по частям."
            if split
            else "Повторов больше не будет."
            if last_try
            else f"Повтор {attempt + 1} из {GEN_RETRIES}.",
        )
        need = missing
        if give_up:
            break

        if split:
            # Ответ не влезает в лимит — просим варианты двумя частями.
            half = (len(need) + 1) // 2
            logger.warning(
                "Генерация %s, умение %s: делим запрос пополам — варианты %s и %s",
                run.job_id,
                skill_index,
                variant_list(need[:half]),
                variant_list(need[half:]),
            )
            generate_chunk(run, skill_index, need[:half])
            generate_chunk(run, skill_index, need[half:])
            return

    for variant_no in need:
        _set_cell(
            run.job_id,
            variant_no,
            skill_index,
            status="failed",
            tasks=[],
            error=(
                problems.get(variant_no, "нет ответа")
                if give_up
                else f"ИИ не справился за {1 + GEN_RETRIES} попытки: "
                f"{problems.get(variant_no, 'нет ответа')}"
            ),
        )


def skill_chain(run: JobRun, skill_index: int, variant_numbers: list[int]) -> None:
    """
    Всё умение: части по вариантам идут ДРУГ ЗА ДРУГОМ (следующая получает
    задания предыдущих), а разные умения — параллельно.
    """
    size = chunk_size(run.request["skills"][skill_index - 1])
    try:
        for start in range(0, len(variant_numbers), size):
            generate_chunk(run, skill_index, variant_numbers[start : start + size])
    except AIError as error:
        # Сюда доходят только фатальные ошибки (ключ, деньги, остановка задания):
        # обычные generate_chunk превращает в повтор или «не удалось».
        run.fail_fatal(error.message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Сбой генерации %s, умение %s", run.job_id, skill_index)
        message = (
            f"Сбой на сервере при генерации ({type(exc).__name__}). "
            "Подробности — в логе сервера; нажмите «Догенерировать недостающее»."
        )

        def change(job: dict) -> None:
            for variant_no in variant_numbers:
                cell = cell_of(job["result"], variant_no, skill_index)
                if cell["status"] in ("pending", "running"):
                    cell.update(status="failed", error=message, tasks=[])
            _complete_if_all_done(job)

        update_job(run.job_id, change)


def run_test_job(job_id: int, teacher_id: int) -> None:
    """
    Поток задания: генерирует все клетки, которые ещё не готовы.

    Один и тот же код и для первой генерации, и для «Догенерировать
    недостающее»: готовые клетки не трогаем, остальные группируем по умениям.
    """
    try:
        row = load_job_row(job_id)
        request = row["request"]
        result = ensure_cells(row["result"] or {}, request)
        run = JobRun(job_id, teacher_id, request)

        todo: dict[int, list[int]] = {}
        for cell in result["cells"]:
            if cell["status"] != "ok":
                todo.setdefault(cell["skill_index"], []).append(cell["variant_no"])

        if todo:
            with ThreadPoolExecutor(
                max_workers=len(todo), thread_name_prefix=f"ai-job-{job_id}"
            ) as pool:
                futures = [
                    pool.submit(skill_chain, run, skill_index, sorted(numbers))
                    for skill_index, numbers in todo.items()
                ]
                wait(futures)
            # Умения составлены — дожидаемся самопроверок, которые ещё в полёте.
            run.wait_checks()

        def finalize(job: dict) -> None:
            # Страховка: ничего не должно остаться «в работе», когда потоки ушли.
            for cell in job["result"].get("cells", []):
                if cell["status"] not in CELL_DONE:
                    cell.update(status="failed", error="Генерация остановилась раньше времени.")
            _complete_if_all_done(job)

        job = update_job(job_id, finalize)
        cells = job["result"].get("cells", [])
        logger.info(
            "Генерация %s закончена за %.1f с: клеток %s, готово %s, не удалось %s",
            job_id,
            time.monotonic() - run.started,
            len(cells),
            sum(1 for cell in cells if cell["status"] == "ok"),
            sum(1 for cell in cells if cell["status"] == "failed"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Сбой генерации %s", job_id)
        try:
            _fail_whole_job(job_id, "Сбой на сервере при генерации — подробности в логе сервера.")
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось записать сбой генерации %s", job_id)
    finally:
        with _live_lock:
            _live_jobs.discard(job_id)


def start_test_job(job_id: int, teacher_id: int) -> None:
    # Отмечаем «живым» ДО запуска потока: опрос статуса не должен принять
    # только что созданное задание за осиротевшее.
    with _live_lock:
        _live_jobs.add(job_id)
    threading.Thread(
        target=run_test_job,
        args=(job_id, teacher_id),
        name=f"ai-job-{job_id}",
        daemon=True,
    ).start()


def run_task_job(job_id: int, teacher_id: int) -> None:
    """Поток задания «заменить одно задание»: генерация (с повторами) → проверка."""
    settings = get_settings()
    ctx = RequestContext(teacher_id=teacher_id, job_id=job_id)
    cancel = cancel_event(job_id)

    def fail(message: str) -> None:
        def change(job: dict) -> None:
            job["status"] = "failed"
            job["error"] = message

        update_job(job_id, change)

    previous_error = ""
    task: dict | None = None
    try:
        request = load_job_row(job_id)["request"]
        skill = request["skills"][request["skill_index"] - 1]
        avoid = {normalize_text(text) for text in request.get("avoid_texts", [])}
        label = f"замена: умение {request['skill_index']}, вариант {request['variant_no']}"

        for attempt in range(1 + GEN_RETRIES):
            reason = ""
            try:
                with http_slots():
                    result = chat(
                        ctx,
                        kind="generate",
                        model=settings.ai_model,
                        messages=task_messages(request, previous_error),
                        max_tokens=settings.ai_gen_max_tokens,
                        thinking=False if settings.ai_gen_thinking_off else None,
                        label=label + (f", повтор {attempt}" if attempt else ""),
                    )
                task = parse_single_task(result.text, skill, avoid)
                break
            except VariantProblem as problem:
                previous_error = str(problem)
            except AIError as error:
                # Связь не появилась за все повторы — ещё заходы ничего не дадут.
                if error.fatal or error.exhausted or attempt == GEN_RETRIES:
                    raise
                previous_error = ""
                reason = error.reason
            except Exception as exc:  # noqa: BLE001
                previous_error = "ответ ИИ не удалось разобрать"
                logger.warning(
                    "Замена задания %s: не удалось разобрать ответ (%s: %s)",
                    job_id,
                    type(exc).__name__,
                    str(exc)[:200],
                    exc_info=True,
                )
            # Почему понадобился повтор — как в generate_chunk.
            logger.warning(
                "Генерация %s, %s: ответ не подошёл — %s. %s",
                job_id,
                label,
                (previous_error or reason)[:300],
                "Повторов больше не будет."
                if attempt == GEN_RETRIES
                else f"Повтор {attempt + 1} из {GEN_RETRIES}.",
            )

        if task is None:
            fail(f"ИИ не справился за {1 + GEN_RETRIES} попытки: {previous_error}")
            return

        task["skill_index"] = request["skill_index"]
        checked = attach_review(task, self_check(ctx, request, task, cancel, label=label))

        def done(job: dict) -> None:
            job["result"] = {"task": checked}
            job["status"] = "done"

        update_job(job_id, done)
    except AIError as error:
        fail(error.message)
    except Exception:  # noqa: BLE001
        logger.exception("Сбой замены задания %s", job_id)
        fail("Сбой на сервере при генерации задания — подробности в логе сервера.")
    finally:
        with _live_lock:
            _live_jobs.discard(job_id)


def start_task_job(job_id: int, teacher_id: int) -> None:
    with _live_lock:
        _live_jobs.add(job_id)
    threading.Thread(
        target=run_task_job,
        args=(job_id, teacher_id),
        name=f"ai-task-{job_id}",
        daemon=True,
    ).start()


# =====================================================================
# Прерванные задания и возобновление
# =====================================================================


def mark_interrupted(job_id: int) -> dict:
    """
    Задание числится «идёт», но его потоков больше нет (сервер перезапустили).

    Готовые клетки остаются. Клетки, где задания уже составлены, но самопроверка
    не успела, тоже сохраняем — с отметкой «самопроверка не выполнилась», чтобы
    учитель посмотрел их сам. Остальные помечаем «прервано»: их можно
    догенерировать, не трогая готовое.
    """

    def change(job: dict) -> None:
        if job["status"] != "running":
            return
        if job["kind"] == "task":
            job["status"] = "failed"
            job["error"] = "Генерация задания прервалась: сервер перезапустили. Нажмите ещё раз."
            return

        result = job["result"]
        interrupted = False
        for cell in result.get("cells", []):
            if cell["status"] in CELL_DONE:
                continue
            if cell["status"] == "checking" and cell.get("tasks"):
                cell.update(
                    status="ok",
                    error="",
                    version=cell.get("version", 0) + 1,
                    tasks=[
                        task
                        if "review" in task
                        else attach_review(
                            task,
                            {
                                "status": "unchecked",
                                "generated": generated_answer(task),
                                "checked": "сервер перезапустили",
                            },
                        )
                        for task in cell["tasks"]
                    ],
                )
            else:
                cell.update(status="interrupted", error=INTERRUPTED, tasks=[])
                interrupted = True
        # Задания старого вида (по вариантам) — без клеток: просто закрываем.
        for variant in result.get("variants", []):
            if variant.get("status") not in ("ok", "failed"):
                variant.update(status="failed", stage="", error=INTERRUPTED)
                interrupted = True
        result["interrupted"] = interrupted
        job["status"] = "done"

    return update_job(job_id, change)


def recover_interrupted_jobs() -> None:
    """
    При старте сервера: задания, которые шли в момент остановки, уже никто
    не доделает — потоки умерли вместе с процессом. Помечаем их прерванными.

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
        mark_interrupted(row["id"])

    if rows:
        logger.info("Генераций, прерванных перезапуском сервера: %s", len(rows))


def orphaned(row: dict) -> bool:
    """
    Осиротело ли задание: в базе «идёт», а в этом процессе его потоков нет,
    и оно давно не обновлялось.

    Подстраховка к recover_interrupted_jobs: если по какой-то причине при
    старте задание не закрыли, оно не будет висеть «составляется» вечно.
    Ждём дольше таймаута одного запроса — чтобы не принять за мёртвое задание,
    которое просто ждёт долгий ответ ИИ (или идёт в другом процессе).
    """
    if row["status"] != "running" or is_live(row["id"]):
        return False
    with db.get_pool().connection() as conn:
        age = conn.execute(
            "SELECT extract(epoch FROM now() - updated_at) AS age FROM ai_jobs WHERE id = %s",
            (row["id"],),
        ).fetchone()
    return age is not None and float(age["age"]) > get_settings().ai_timeout_seconds + 60


def prepare_resume(job_id: int, variant_no: int | None = None) -> tuple[int, str]:
    """
    Готовит задание к «Догенерировать недостающее»: все не готовые клетки
    (не удались или прерваны) снова становятся в очередь. Готовые не трогаем.

    Возвращает (сколько клеток поставлено в очередь, проблема).
    """
    count = 0
    problem = ""

    def change(job: dict) -> None:
        nonlocal count, problem
        if job["kind"] != "test":
            problem = "Догенерировать можно только проверочную работу."
            return
        if job["status"] == "running":
            problem = "Генерация ещё идёт — дождитесь её окончания."
            return

        request = load_job_row(job_id)["request"]
        job["result"] = ensure_cells(job["result"], request)
        for cell in job["result"]["cells"]:
            if cell["status"] == "ok":
                continue
            if variant_no is not None and cell["variant_no"] != variant_no:
                continue
            cell.update(status="pending", error="", attempts=0, tasks=[])
            count += 1
        if count == 0:
            problem = "Все задания уже составлены — догенерировать нечего."
            return
        job["result"]["interrupted"] = False
        # Задание оживает: если оно упало целиком (например, кончились деньги,
        # а админ пополнил счёт), общую ошибку снимаем.
        job["status"] = "running"
        job["error"] = ""

    update_job(job_id, change)
    return count, problem


# =====================================================================
# Вид задания для ответа API
# =====================================================================


def job_view(row: dict) -> dict:
    """Статус и результат задания для фронтенда. Ничего секретного тут нет."""
    result = row["result"] or {}
    if row["kind"] == "test":
        result = ensure_cells(result, row["request"])
    cells = result.get("cells", [])

    def count(*statuses: str) -> int:
        return sum(1 for cell in cells if cell["status"] in statuses)

    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "error": row["error"],
        "request": row["request"],
        "variants_count": result.get("variants_count", 0),
        # Прогресс считаем по клеткам «умение × вариант», а не по вариантам.
        "total": len(cells),
        "done": count("ok"),
        "failed": count("failed"),
        "interrupted_cells": count("interrupted"),
        "generating": count("pending", "running"),
        "checking": count("checking"),
        # Сервер перезапустили посреди генерации — есть что догенерировать.
        "interrupted": bool(result.get("interrupted")) or count("interrupted") > 0,
        "cells": cells,
        "task": result.get("task"),
        "needs_review": sum(
            1 for cell in cells for task in cell.get("tasks", []) if task.get("needs_review")
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
