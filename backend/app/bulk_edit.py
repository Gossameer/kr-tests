"""
Правка сразу во всех вариантах: ИИ поправляет или заменяет задания одного
умения (или одной позиции этого умения) во ВСЕХ вариантах работы.

Два режима:
  fix     — «Поправить»: те же задания, исправленные по замечанию учителя;
  replace — «Заменить»: новые задания на это умение с учётом пожелания.

Работает как генерация: варианты идут частями параллельно, ответ разбирается
и проверяется той же схемой, каждое задание проходит самопроверку и
автопроверки качества. Результат — «было → стало» по каждому варианту;
что принять, решает учитель на странице (сами задания здесь не меняются).

Результат задания (ai_jobs.result):
    {"items": [{"variant_no", "order", "status", "before", "after", "error"}]}
    status: pending → running → checking → ok | failed
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait

from app.ai_client import AIError, chat
from app.ai_generation import (
    GEN_RETRIES,
    NO_ANSWER_IN_TEXT,
    QUALITY_RULES,
    SCHOOL_NOTATION,
    SYSTEM_GENERATE,
    TASK_SHAPE_CHOICE,
    TASK_SHAPE_INPUT,
    JobRun,
    VariantProblem,
    _live_jobs,
    _live_lock,
    check_and_fix,
    check_pool,
    gen_limits,
    chunk_size,
    format_rule,
    http_slots,
    is_stress_skill,
    task_key,
    parse_skill_answer,
    stress_rule,
    subject_line,
    update_job,
    variant_list,
)
from app.config import get_settings

logger = logging.getLogger(__name__)

MODE_TEXT = {
    "fix": (
        "Поправь эти задания по замечанию учителя. Сохрани суть, тип, сложность и (если "
        "замечание этого не касается) числа и данные каждого задания — меняй только то, что "
        "нужно, чтобы выполнить замечание и требования ниже."
    ),
    "replace": (
        "Составь НОВЫЕ задания на это умение вместо этих, с учётом пожелания учителя. "
        "Новые задания не должны повторять текущие; варианты по-прежнему равноценны."
    ),
}


def effective_skill(request: dict) -> dict:
    """Умение, как его видит разбор ответа: при правке одной позиции — одно задание."""
    skill = dict(request["skills"][request["skill_index"] - 1])
    if request.get("order") is not None:
        skill["tasks_per_variant"] = 1
    return skill


def bulk_messages(request: dict, variant_numbers: list[int], previous_error: str) -> list[dict]:
    skill = request["skills"][request["skill_index"] - 1]
    count = effective_skill(request)["tasks_per_variant"]
    shape = TASK_SHAPE_CHOICE if skill["answer_format"] == "choice" else TASK_SHAPE_INPUT
    shape = shape.replace('"skill": 2,\n  ', "").replace('"skill": 1,\n  ', "")
    current = {item["variant_no"]: item["tasks"] for item in request["current"]}

    parts = [
        MODE_TEXT[request["mode"]],
        "",
        f"Замечание учителя: {request['wish']}",
        "",
        f"Предмет и тема: {subject_line(request)}",
        f"Класс: {request.get('grade', '').strip() or 'не указан'}",
        f"Проверяемое умение: {skill['title']}",
        f"Формат ответа: {format_rule(skill['answer_format'], is_stress_skill(skill['title']))}",
        f"В каждом варианте заданий здесь: ровно {count}.",
        "",
        "Текущие задания по вариантам:",
    ]
    for number in variant_numbers:
        for position, task in enumerate(current.get(number, []), start=1):
            answer = (
                f"варианты: {' | '.join(task['options'])}; верный № {(task['correct'] or 0) + 1}"
                if task["answer_format"] == "choice"
                else f"ответ: {' | '.join(task['accepted_answers'])}"
            )
            parts.append(f"- вариант {number}, задание {position}: {task['text']} ({answer})")

    parts += [
        "",
        "Требования:",
        '- к КАЖДОМУ заданию краткое решение в "solution" (1–3 строки, для учителя);',
        *[f"- {rule};" for rule in QUALITY_RULES],
        f"- {NO_ANSWER_IN_TEXT};",
        f"- {SCHOOL_NOTATION}",
    ]
    if stress_rule(skill):
        parts += ["", stress_rule(skill)]
    hint = skill.get("ai_hint", "").strip()
    if hint and hint != request["wish"].strip():
        parts += ["", f"Уточнение учителя к этому умению (выполни обязательно): {hint}"]
    if previous_error:
        parts += ["", f"Прошлый ответ не подошёл: {previous_error}. Исправь это."]

    example = ", ".join(
        f'{{"variant": {number}, "tasks": [{"..." if position else shape}]}}'
        for position, number in enumerate(variant_numbers[:2])
    )
    parts += [
        "",
        "Верни ТОЛЬКО JSON такой структуры (по объекту на каждый вариант из списка: "
        f"{variant_list(variant_numbers)}):",
        f'{{"variants": [{example}]}}',
    ]
    return [
        {"role": "system", "content": SYSTEM_GENERATE},
        {"role": "user", "content": "\n".join(parts)},
    ]


def initial_items(request: dict) -> dict:
    """Пустой результат: по строке на каждое задание каждого варианта."""
    first = request.get("order") or 0
    return {
        "items": [
            {
                "variant_no": item["variant_no"],
                "order": first + position,
                "status": "pending",
                "before": task,
                "after": None,
                "error": "",
            }
            for item in request["current"]
            for position, task in enumerate(item["tasks"])
        ]
    }


def _set_items(job_id: int, variant_numbers: list[int], **fields) -> None:
    def change(job: dict) -> None:
        for item in job["result"]["items"]:
            if item["variant_no"] in variant_numbers:
                item.update(fields)
        _finish_if_done(job)

    update_job(job_id, change)


def _finish_if_done(job: dict) -> None:
    if job["status"] == "running" and all(
        item["status"] in ("ok", "failed") for item in job["result"]["items"]
    ):
        job["status"] = "done"


def _accept(run: JobRun, variant_no: int, tasks: list[dict]) -> None:
    """Задания варианта готовы: сохраняем и сразу отправляем на самопроверку."""

    def mark_checking(job: dict) -> None:
        own = [item for item in job["result"]["items"] if item["variant_no"] == variant_no]
        for item, task in zip(own, tasks):
            item.update(status="checking", after=task, error="")

    update_job(run.job_id, mark_checking)

    def check(position: int, task: dict) -> None:
        try:
            checked = check_and_fix(
                run.ctx, run.request, run.request["skill_index"], task, run.cancel,
                label=f"правка: вариант {variant_no}, задание {position + 1}",
            )
        except AIError as error:
            if error.fatal:
                run.fail_fatal(error.message)
            from app.ai_generation import attach_review

            checked = attach_review(
                task, {"status": "unchecked", "generated": "", "checked": error.short},
                run.request.get("subject", ""),
            )

        def store(job: dict) -> None:
            own = [item for item in job["result"]["items"] if item["variant_no"] == variant_no]
            if position < len(own) and own[position]["status"] == "checking":
                own[position].update(status="ok", after=checked)
            _finish_if_done(job)

        update_job(run.job_id, store)

    run.add_futures([check_pool().submit(check, position, task) for position, task in enumerate(tasks)])


def _chunk(run: JobRun, variant_numbers: list[int]) -> None:
    """Одна часть вариантов: запрос → разбор → приём; недостающее — повтор."""
    settings = get_settings()
    request = run.request
    skill = effective_skill(request)
    need = list(variant_numbers)
    previous_error = ""
    problems: dict[int, str] = {}
    # При замене новые задания не должны совпадать с текущими.
    avoid = (
        {task_key(task) for item in request["current"] for task in item["tasks"]}
        if request["mode"] == "replace"
        else set()
    )

    for attempt in range(1 + GEN_RETRIES):
        if run.cancel.is_set():
            return
        _set_items(run.job_id, need, status="running")
        try:
            with http_slots():
                result = chat(
                    run.ctx,
                    kind="generate",
                    model=settings.ai_model,
                    messages=bulk_messages(request, need, previous_error),
                    max_tokens=gen_limits()[0],
                    thinking=gen_limits()[1],
                    label=f"правка: варианты {variant_list(need)}" + (f", повтор {attempt}" if attempt else ""),
                )
            ready, problems = parse_skill_answer(result.text, skill, request["skill_index"], need, avoid)
        except VariantProblem as problem:
            ready, problems = {}, {number: str(problem) for number in need}
        except AIError as error:
            if error.fatal:
                run.fail_fatal(error.message)
                return
            ready, problems = {}, {number: error.message for number in need}
            if error.exhausted:
                break

        for variant_no, tasks in ready.items():
            _accept(run, variant_no, tasks)
        need = [number for number in need if number not in ready]
        if not need:
            return
        previous_error = "; ".join(dict.fromkeys(problems.get(n, "") for n in need))[:300]
        logger.warning(
            "Правка во всех вариантах %s: ответ не подошёл — %s. %s",
            run.job_id,
            previous_error,
            "Повторов больше не будет." if attempt == GEN_RETRIES else f"Повтор {attempt + 1} из {GEN_RETRIES}.",
        )

    for number in need:
        _set_items(run.job_id, [number], status="failed", error=problems.get(number, "нет ответа"))


def run_bulk_job(job_id: int, teacher_id: int) -> None:
    from app.ai_generation import load_job_row

    try:
        request = load_job_row(job_id)["request"]
        run = JobRun(job_id, teacher_id, request)
        numbers = sorted(item["variant_no"] for item in request["current"])
        size = chunk_size(effective_skill(request))
        chunks = [numbers[start : start + size] for start in range(0, len(numbers), size)]
        with ThreadPoolExecutor(max_workers=max(1, len(chunks)), thread_name_prefix=f"ai-bulk-{job_id}") as pool:
            wait([pool.submit(_chunk, run, chunk) for chunk in chunks])
        run.wait_checks()

        def finalize(job: dict) -> None:
            for item in job["result"]["items"]:
                if item["status"] not in ("ok", "failed"):
                    item.update(status="failed", error="Правка остановилась раньше времени.")
            _finish_if_done(job)

        update_job(job_id, finalize)
    except Exception:  # noqa: BLE001
        logger.exception("Сбой правки во всех вариантах %s", job_id)

        def fail(job: dict) -> None:
            job["status"] = "failed"
            job["error"] = "Сбой на сервере при правке — подробности в логе сервера."

        update_job(job_id, fail)
    finally:
        with _live_lock:
            _live_jobs.discard(job_id)


def start_bulk_job(job_id: int, teacher_id: int) -> None:
    with _live_lock:
        _live_jobs.add(job_id)
    threading.Thread(target=run_bulk_job, args=(job_id, teacher_id), name=f"ai-bulk-{job_id}", daemon=True).start()
