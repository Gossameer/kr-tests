"""
Исправление уже проведённой работы: правки заданий и пересчёт результатов.

    GET   /api/tests/{id}/tasks-review              — сводка по заданиям: сколько ошиблись
                                                      и самые частые неверные ответы
    POST  /api/tests/{id}/tasks/{task_id}/review    — правка задания (см. ReviewAction)
    GET   /api/tests/{id}/review-log                — журнал правок

Правки (все отменяемые):
  add_answer / remove_answer        — ещё один верный ответ для ввода;
  accept_option / unaccept_option   — ещё один засчитываемый вариант у выбора;
  credit_all / uncredit_all         — засчитать задание всем, кто его получил;
  exclude / include                 — исключить задание из оценки и вернуть;
  set_answer / set_option           — ИСПРАВИТЬ правильный ответ (заменить, а не добавить):
                                      у ввода — новый ответ (несколько записей через «|»),
                                      у выбора — другой верный вариант;
  restore_answer                    — вернуть исходный правильный ответ.

После каждой правки на сервере перепроверяются ВСЕ сданные попытки работы
(app/rescoring.py): баллы, проценты, умения, статистика и Excel считаются уже
по новым вердиктам. Ответы учеников не меняются.

Доступ — автор работы и администратор (как у результатов).
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, field_validator

from app import db
from app.answers import normalize_answer
from app.auth import require_user
from app.formulas import plain_text
from app.rescoring import rescore_test
from app.routers.results import db_unavailable, load_test_for_user
from app.schemas import MAX_ANSWER_LEN, MAX_ANSWERS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tests", tags=["task-review"])

# Сколько самых частых неверных ответов показывать у задания.
TOP_WRONG = 5

ACTION_NAMES = {
    "add_answer": "добавил верный ответ",
    "remove_answer": "убрал добавленный ответ",
    "accept_option": "засчитал вариант ответа",
    "unaccept_option": "отменил зачёт варианта",
    "credit_all": "засчитал задание всем",
    "uncredit_all": "отменил «засчитать всем»",
    "exclude": "исключил задание из оценки",
    "include": "вернул задание в оценку",
    "set_answer": "исправил правильный ответ",
    "set_option": "отметил другой вариант как верный",
    "restore_answer": "вернул исходный правильный ответ",
}

# Сколько фамилий показывать у частого неверного ответа — дальше «и ещё N».
NAMES_SHOWN = 5


def short_name(full_name: str) -> str:
    """«Иванов Иван» → «Иванов И.»: в сводке фамилии короткие, как в журнале."""
    parts = full_name.split()
    if len(parts) < 2:
        return full_name
    return f"{parts[0]} " + " ".join(f"{part[0]}." for part in parts[1:3])


class ReviewAction(BaseModel):
    action: Literal[
        "add_answer",
        "remove_answer",
        "accept_option",
        "unaccept_option",
        "credit_all",
        "uncredit_all",
        "exclude",
        "include",
        "set_answer",
        "set_option",
        "restore_answer",
    ]
    # Для add_answer / remove_answer — сам ответ.
    answer: str = ""
    # Для accept_option / unaccept_option — id варианта ответа.
    option_id: int | None = None

    @field_validator("answer")
    @classmethod
    def trim(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) > MAX_ANSWER_LEN:
            raise ValueError(f"ответ длиннее {MAX_ANSWER_LEN} символов")
        return cleaned


def task_state(conn, test_id: int, task_id: int) -> dict:
    """Задание с правками: что сейчас засчитывается."""
    row = conn.execute(
        """
        SELECT id, variant_no, position, text, answer_format, accepted_answers,
               extra_answers, extra_option_ids, credit_all, excluded,
               original_answers, original_correct_option
        FROM tasks WHERE id = %s AND test_id = %s
        """,
        (task_id, test_id),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Такого задания в этой работе нет. Обновите страницу.",
        )
    return row


def place(task: dict) -> str:
    return f"вариант {task['variant_no']}, задание {task['position']}"


def fix_correct_answer(conn, task: dict, action: str, payload: "ReviewAction") -> str:
    """
    «Исправить правильный ответ»: новый ответ пишется прямо в задание, исходный —
    в original_* (один раз, при первой правке), чтобы её можно было отменить.
    Возвращает подробность для журнала.
    """
    task_id = task["id"]
    if action == "set_answer":
        if task["answer_format"] != "input":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="У задания с выбором отмечают другой верный вариант, а не вводят ответ.",
            )
        answers = [plain_text(part).strip() for part in payload.answer.split("|")]
        answers = list(dict.fromkeys(answer for answer in answers if answer))[:MAX_ANSWERS]
        if not answers:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Впишите правильный ответ. Несколько записей — через «|».",
            )
        conn.execute(
            """
            UPDATE tasks SET original_answers = coalesce(original_answers, accepted_answers),
                             accepted_answers = %s
            WHERE id = %s
            """,
            (answers, task_id),
        )
        return f": было «{' | '.join(task['accepted_answers'])}», стало «{' | '.join(answers)}»"

    options = conn.execute(
        "SELECT id, text, is_correct FROM task_options WHERE task_id = %s ORDER BY position, id",
        (task_id,),
    ).fetchall()
    current = next((option for option in options if option["is_correct"]), None)

    if action == "set_option":
        if task["answer_format"] != "choice":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="У задания с вводом исправляют ответ, а не вариант.",
            )
        chosen = next((option for option in options if option["id"] == payload.option_id), None)
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Такого варианта ответа у задания нет. Обновите страницу.",
            )
        if chosen["is_correct"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Этот вариант и так верный.")
        conn.execute(
            "UPDATE tasks SET original_correct_option = coalesce(original_correct_option, %s) WHERE id = %s",
            (current["id"] if current else None, task_id),
        )
        conn.execute("UPDATE task_options SET is_correct = (id = %s) WHERE task_id = %s", (chosen["id"], task_id))
        # Вариант стал основным верным — отдельный «зачёт» ему больше не нужен.
        conn.execute(
            "UPDATE tasks SET extra_option_ids = array_remove(extra_option_ids, %s) WHERE id = %s",
            (chosen["id"], task_id),
        )
        before = plain_text(current["text"]) if current else "—"
        return f": было «{before}», стало «{plain_text(chosen['text'])}»"

    # restore_answer
    if task["original_answers"] is None and task["original_correct_option"] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Правильный ответ этого задания не исправляли — возвращать нечего.",
        )
    if task["original_answers"] is not None:
        conn.execute(
            "UPDATE tasks SET accepted_answers = original_answers, original_answers = NULL WHERE id = %s",
            (task_id,),
        )
        return f": снова «{' | '.join(task['original_answers'])}»"
    conn.execute(
        "UPDATE task_options SET is_correct = (id = %s) WHERE task_id = %s",
        (task["original_correct_option"], task_id),
    )
    conn.execute("UPDATE tasks SET original_correct_option = NULL WHERE id = %s", (task_id,))
    original = next((o for o in options if o["id"] == task["original_correct_option"]), None)
    return f": снова «{plain_text(original['text']) if original else '—'}»"


@router.post("/{test_id}/tasks/{task_id}/review", summary="Поправить задание и пересчитать работы")
def review_task(
    payload: ReviewAction,
    test_id: int = Path(ge=1),
    task_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> dict:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn, conn.transaction():
        test_row = load_test_for_user(conn, test_id, user)
        # Блокируем строку работы: две правки одновременно не должны пересчитать
        # результаты каждая по-своему.
        conn.execute("SELECT id FROM tests WHERE id = %s FOR UPDATE", (test_row["id"],))
        task = task_state(conn, test_row["id"], task_id)
        action = payload.action
        detail = place(task)

        if action in ("add_answer", "remove_answer"):
            if task["answer_format"] != "input":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="У задания с выбором ответа засчитывают вариант, а не вводят ответ.",
                )
            answer = plain_text(payload.answer).strip()
            if not answer:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Впишите ответ, который нужно засчитывать.",
                )
            extra = list(task["extra_answers"])
            key = normalize_answer(answer)
            if action == "add_answer":
                known = [normalize_answer(item) for item in task["accepted_answers"] + extra]
                if key in known:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Ответ «{answer}» уже засчитывается.",
                    )
                if len(task["accepted_answers"]) + len(extra) >= MAX_ANSWERS:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"У задания уже {MAX_ANSWERS} верных ответов — больше нельзя.",
                    )
                extra.append(answer)
            else:
                remaining = [item for item in extra if normalize_answer(item) != key]
                if len(remaining) == len(extra):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Такого добавленного ответа нет. Исходные ответы задания не удаляются.",
                    )
                extra = remaining
            conn.execute("UPDATE tasks SET extra_answers = %s WHERE id = %s", (extra, task_id))
            detail += f": «{answer}»"

        elif action in ("accept_option", "unaccept_option"):
            option = conn.execute(
                "SELECT id, text, is_correct FROM task_options WHERE id = %s AND task_id = %s",
                (payload.option_id, task_id),
            ).fetchone()
            if option is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Такого варианта ответа у задания нет. Обновите страницу.",
                )
            if option["is_correct"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Этот вариант и так правильный.",
                )
            extra = [item for item in task["extra_option_ids"] if item != option["id"]]
            if action == "accept_option":
                extra.append(option["id"])
            conn.execute("UPDATE tasks SET extra_option_ids = %s WHERE id = %s", (extra, task_id))
            detail += f": «{plain_text(option['text'])}»"

        elif action in ("credit_all", "uncredit_all"):
            conn.execute(
                "UPDATE tasks SET credit_all = %s WHERE id = %s", (action == "credit_all", task_id)
            )
        elif action in ("exclude", "include"):
            conn.execute(
                "UPDATE tasks SET excluded = %s WHERE id = %s", (action == "exclude", task_id)
            )
        else:
            detail += fix_correct_answer(conn, task, action, payload)

        changed = rescore_test(conn, test_row["id"])
        conn.execute(
            """
            INSERT INTO task_review_log (test_id, task_id, user_id, user_name, action, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (test_row["id"], task_id, user["id"], user["full_name"], action, detail),
        )
        task = task_state(conn, test_row["id"], task_id)

    logger.info(
        "Работа %s: %s %s (%s); пересчитано попыток %s",
        test_row["share_token"],
        user["email"],
        ACTION_NAMES[action],
        detail,
        changed["attempts_changed"],
    )
    return {
        "task_id": task_id,
        "accepted_answers": task["accepted_answers"],
        "original_answers": task["original_answers"],
        "extra_answers": task["extra_answers"],
        "extra_option_ids": task["extra_option_ids"],
        "credit_all": task["credit_all"],
        "excluded": task["excluded"],
        **changed,
    }


@router.get("/{test_id}/tasks-review", summary="Сводка по заданиям: где ошиблись")
def tasks_review(test_id: int = Path(ge=1), user: dict = Depends(require_user)) -> dict:
    """
    По каждому заданию всех вариантов: сколько учеников его получили, сколько
    ответили неверно и какие неверные ответы встречались чаще всего — чтобы
    при 30 вариантах не открывать каждую работу по отдельности.

    Считаются только сданные и не аннулированные работы. Неверные ответы ввода
    группируются по смыслу («0,5» и «0.5» — один ответ), выбора — по варианту.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        tasks = conn.execute(
            """
            SELECT t.id, t.variant_no, t.position, t.text, t.answer_format,
                   t.accepted_answers, t.extra_answers, t.extra_option_ids,
                   t.original_answers, t.original_correct_option,
                   t.credit_all, t.excluded, s.id AS skill_id, s.position AS skill_position,
                   s.title AS skill_title
            FROM tasks t JOIN skills s ON s.id = t.skill_id
            WHERE t.test_id = %s
            ORDER BY s.position, t.variant_no, t.position
            """,
            (test_row["id"],),
        ).fetchall()
        options = conn.execute(
            """
            SELECT o.id, o.task_id, o.text, o.is_correct FROM task_options o
            JOIN tasks t ON t.id = o.task_id
            WHERE t.test_id = %s ORDER BY o.position, o.id
            """,
            (test_row["id"],),
        ).fetchall()
        answers = conn.execute(
            """
            SELECT ans.task_id, ans.option_id, ans.answer_text, ans.is_correct, a.student_name
            FROM answers ans JOIN attempts a ON a.id = ans.attempt_id
            WHERE a.test_id = %s AND a.finished_at IS NOT NULL AND a.annulled_at IS NULL
              AND NOT a.is_trial
            """,
            (test_row["id"],),
        ).fetchall()

    options_by_task: dict[int, list[dict]] = {}
    for option in options:
        options_by_task.setdefault(option["task_id"], []).append(option)
    answers_by_task: dict[int, list[dict]] = {}
    for answer in answers:
        answers_by_task.setdefault(answer["task_id"], []).append(answer)

    result = []
    for task in tasks:
        own = answers_by_task.get(task["id"], [])
        wrong = [answer for answer in own if not answer["is_correct"]]

        # Частые неверные ответы: {ключ: [сколько, как показать, id варианта]}.
        groups: dict[str, list] = {}
        for answer in wrong:
            if task["answer_format"] == "choice":
                if answer["option_id"] is None:
                    key, shown = "", ""
                else:
                    key = f"option:{answer['option_id']}"
                    shown = next(
                        (o["text"] for o in options_by_task.get(task["id"], []) if o["id"] == answer["option_id"]),
                        "",
                    )
            else:
                raw = (answer["answer_text"] or "").strip()
                key, shown = (normalize_answer(raw), raw) if raw else ("", "")
            group = groups.setdefault(key, [0, shown, answer["option_id"], []])
            group[0] += 1
            group[3].append(short_name(answer["student_name"]))

        top = sorted(groups.values(), key=lambda item: (-item[0], item[1]))
        original_option = next(
            (o["text"] for o in options_by_task.get(task["id"], []) if o["id"] == task["original_correct_option"]),
            None,
        )
        result.append(
            {
                "task_id": task["id"],
                "variant_no": task["variant_no"],
                "position": task["position"],
                "skill_id": task["skill_id"],
                "skill_position": task["skill_position"],
                "skill_title": task["skill_title"],
                "text": task["text"],
                "answer_format": task["answer_format"],
                "accepted_answers": task["accepted_answers"],
                "extra_answers": task["extra_answers"],
                # Учитель исправил правильный ответ: что было (для «Вернуть прежний ответ»).
                "original_answer": (
                    " | ".join(task["original_answers"])
                    if task["original_answers"] is not None
                    else original_option
                ),
                "options": [
                    {
                        "id": option["id"],
                        "text": option["text"],
                        "is_correct": option["is_correct"],
                        "accepted": option["id"] in task["extra_option_ids"],
                    }
                    for option in options_by_task.get(task["id"], [])
                ],
                "credit_all": task["credit_all"],
                "excluded": task["excluded"],
                "answered": len(own),
                "wrong": len(wrong),
                # Пустая строка — «не ответил»: такой ответ засчитать нельзя.
                "top_wrong": [
                    {
                        "answer": shown,
                        "count": count,
                        "option_id": option_id,
                        "students": sorted(names)[:NAMES_SHOWN],
                        "more": max(0, len(names) - NAMES_SHOWN),
                    }
                    for count, shown, option_id, names in top[:TOP_WRONG]
                ],
            }
        )

    return {"tasks": result}


@router.get("/{test_id}/review-log", summary="Журнал правок заданий")
def review_log(test_id: int = Path(ge=1), user: dict = Depends(require_user)) -> list[dict]:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        rows = conn.execute(
            """
            SELECT id, created_at, user_name, action, detail, task_id
            FROM task_review_log WHERE test_id = %s ORDER BY id DESC LIMIT 200
            """,
            (test_row["id"],),
        ).fetchall()
    return [{**row, "action_name": ACTION_NAMES.get(row["action"], row["action"])} for row in rows]
