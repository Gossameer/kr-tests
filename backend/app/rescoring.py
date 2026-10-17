"""
Проверка ответов с учётом правок учителя и пересчёт уже сданных работ.

Правки учителя лежат в самом задании (миграция 011):
  extra_answers    — добавленные верные ответы для ввода (как «|» в ответах);
  extra_option_ids — варианты ответа, которые тоже засчитываются;
  credit_all       — задание засчитано всем, кто его получил;
  excluded         — задание не входит ни в баллы, ни в максимум, ни в умения.

Сами ответы учеников (answer_text, option_id) не меняются никогда. Меняется
только вердикт answers.is_correct и баллы попытки — их всегда можно посчитать
заново из ответов и правок, поэтому пересчёт идемпотентен: сколько раз его
ни запускай, результат один.
"""

import logging

from app.answers import check_input_answer

logger = logging.getLogger(__name__)


def is_correct(task: dict, option_id: int | None, answer_text: str | None,
               correct_options: set[int]) -> bool:
    """
    Верен ли ответ на задание с учётом правок учителя.

    task — строка задания: answer_format, accepted_answers, extra_answers,
    extra_option_ids, credit_all. correct_options — id исходно верных вариантов.
    """
    if task.get("credit_all"):
        return True
    if task["answer_format"] == "choice":
        return option_id is not None and (
            option_id in correct_options or option_id in (task.get("extra_option_ids") or [])
        )
    text = (answer_text or "").strip()
    accepted = list(task["accepted_answers"]) + list(task.get("extra_answers") or [])
    return bool(text) and check_input_answer(text, accepted)


def rescore_test(conn, test_id: int) -> dict:
    """
    Перепроверяет все ответы работы и пересчитывает баллы попыток.

    Возвращает {"answers_changed": n, "attempts_changed": m} — сколько вердиктов
    и сколько попыток изменилось (для журнала и ответа API).
    Вызывать внутри транзакции: правка задания и пересчёт — одно целое.
    """
    tasks = {
        row["id"]: row
        for row in conn.execute(
            """
            SELECT id, answer_format, accepted_answers, extra_answers,
                   extra_option_ids, credit_all, excluded
            FROM tasks WHERE test_id = %s
            """,
            (test_id,),
        ).fetchall()
    }
    correct_options: dict[int, set[int]] = {}
    for row in conn.execute(
        """
        SELECT o.task_id, o.id FROM task_options o
        JOIN tasks t ON t.id = o.task_id
        WHERE t.test_id = %s AND o.is_correct
        """,
        (test_id,),
    ).fetchall():
        correct_options.setdefault(row["task_id"], set()).add(row["id"])

    answers = conn.execute(
        """
        SELECT ans.id, ans.task_id, ans.option_id, ans.answer_text, ans.is_correct
        FROM answers ans
        JOIN attempts a ON a.id = ans.attempt_id
        WHERE a.test_id = %s
        """,
        (test_id,),
    ).fetchall()

    changes = [
        (verdict, row["id"])
        for row in answers
        if (
            verdict := is_correct(
                tasks[row["task_id"]],
                row["option_id"],
                row["answer_text"],
                correct_options.get(row["task_id"], set()),
            )
        )
        != row["is_correct"]
    ]
    if changes:
        with conn.cursor() as cursor:
            cursor.executemany("UPDATE answers SET is_correct = %s WHERE id = %s", changes)

    # Баллы и максимум — только по заданиям, которые учитываются. Максимум
    # считаем по заданиям ВАРИАНТА, а не по ответам: так он верен и для работ,
    # сданных до того, как в вариант что-то добавили.
    attempts_changed = conn.execute(
        """
        WITH totals AS (
            SELECT a.id,
                   (SELECT count(*) FROM answers ans JOIN tasks t ON t.id = ans.task_id
                     WHERE ans.attempt_id = a.id AND ans.is_correct AND NOT t.excluded) AS score,
                   (SELECT count(*) FROM tasks t
                     WHERE t.test_id = a.test_id AND t.variant_no = a.variant_no
                       AND NOT t.excluded) AS max_score
            FROM attempts a
            WHERE a.test_id = %s AND a.finished_at IS NOT NULL
        )
        UPDATE attempts a
        SET score = totals.score, max_score = totals.max_score
        FROM totals
        WHERE totals.id = a.id
          AND (a.score IS DISTINCT FROM totals.score OR a.max_score IS DISTINCT FROM totals.max_score)
        """,
        (test_id,),
    ).rowcount

    logger.info(
        "Пересчёт работы %s: изменилось вердиктов %s, попыток %s",
        test_id,
        len(changes),
        attempts_changed,
    )
    return {"answers_changed": len(changes), "attempts_changed": attempts_changed}
