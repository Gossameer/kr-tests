"""
Роутер результатов — то, что видит УЧИТЕЛЬ по секретной ссылке.

    GET    /api/results/{token}                      — таблица учеников и умений
    GET    /api/results/{token}/attempts/{id}        — разбор одной работы
    GET    /api/results/{token}/export.xlsx          — выгрузка в Excel (3 листа)
    PATCH  /api/results/{token}                      — открыть/закрыть приём работ
    DELETE /api/results/{token}/attempts/{id}        — удалить работу (разрешить пересдачу)
    DELETE /api/results/{token}?confirm_title=...    — удалить контрольную целиком

Доступ: авторизации пока нет, вместо неё длинный секрет в адресе. Искать
контрольную здесь можно ТОЛЬКО по results_token — ученический код не подойдёт.
"""

import io
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app import db
from app.schemas import (
    AttemptDetail,
    ResultsOverview,
    ResultsOverviewSettings,
    TestSettingsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/results", tags=["results"])

# Ученический код — 8 символов; он не подойдёт и по длине, но проверку
# всё равно делает база: 404 вместо результатов.
TOKEN_MIN_LEN = 8
TOKEN_MAX_LEN = 200

# Пороги освоения умения. Ниже 50% — не сформировано, выше 65% — в порядке.
LEVEL_LOW = 50
LEVEL_MID = 65

# Заливка ячеек в Excel теми же порогами, что и цвета на экране.
FILL_LOW = PatternFill("solid", fgColor="F8CBCB")
FILL_MID = PatternFill("solid", fgColor="FFE9B0")
FILL_HIGH = PatternFill("solid", fgColor="CDEBD3")


def db_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="База данных недоступна. Проверьте, что PostgreSQL запущен.",
    )


def results_not_found() -> HTTPException:
    """404 на неверный токен, без подробностей: ссылка заменяет пароль."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Результаты не найдены. Проверьте ссылку — она отличается от ученической.",
    )


def percent_of(correct: int | None, total: int | None) -> int:
    """Процент, округлённый до целого. Без деления на ноль."""
    if not correct or not total:
        return 0
    return round(correct * 100 / total)


def fill_for(percent: int) -> PatternFill:
    """Заливка ячейки по проценту выполнения."""
    if percent < LEVEL_LOW:
        return FILL_LOW
    if percent <= LEVEL_MID:
        return FILL_MID
    return FILL_HIGH


def load_test_by_results_token(conn, results_token: str) -> dict:
    """Находит контрольную по СЕКРЕТНОМУ токену результатов или бросает 404."""
    row = conn.execute(
        """
        SELECT id, title, teacher_name, share_token, classes, is_open, variants_count
        FROM tests
        WHERE results_token = %s
        """,
        (results_token,),
    ).fetchone()

    if row is None:
        raise results_not_found()

    return row


def load_skills(conn, test_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT id, position, title, tasks_per_variant, answer_format
        FROM skills
        WHERE test_id = %s
        ORDER BY position, id
        """,
        (test_id,),
    ).fetchall()


def load_attempts(conn, test_id: int) -> list[dict]:
    """Список сдавших: сортировка по классу, затем по фамилии и имени."""
    return conn.execute(
        """
        SELECT id, student_name, student_class, variant_no,
               score, max_score, finished_at
        FROM attempts
        WHERE test_id = %s
        ORDER BY student_class, student_name, id
        """,
        (test_id,),
    ).fetchall()


def load_skill_matrix(conn, test_id: int) -> dict[tuple[int, int], dict]:
    """
    Сколько заданий каждого умения решил каждый ученик.

    Возвращает {(id попытки, id умения): {"correct": n, "total": m}}.
    Считаем по таблице answers: там уже лежит готовый вердикт, пересчитывать
    ответы на каждый показ таблицы незачем.
    """
    rows = conn.execute(
        """
        SELECT ans.attempt_id,
               t.skill_id,
               count(*)                          AS total,
               count(*) FILTER (WHERE ans.is_correct) AS correct
        FROM answers ans
        JOIN tasks t      ON t.id = ans.task_id
        JOIN attempts att ON att.id = ans.attempt_id
        WHERE att.test_id = %s
        GROUP BY ans.attempt_id, t.skill_id
        """,
        (test_id,),
    ).fetchall()

    return {
        (row["attempt_id"], row["skill_id"]): {
            "correct": row["correct"],
            "total": row["total"],
        }
        for row in rows
    }


def load_skill_stats(conn, test_id: int, skills: list[dict]) -> list[dict]:
    """Сводка по умениям в целом: сколько заданий решено верно по всем работам."""
    rows = conn.execute(
        """
        SELECT t.skill_id,
               count(*)                          AS total,
               count(*) FILTER (WHERE ans.is_correct) AS correct
        FROM answers ans
        JOIN tasks t      ON t.id = ans.task_id
        JOIN attempts att ON att.id = ans.attempt_id
        WHERE att.test_id = %s
        GROUP BY t.skill_id
        """,
        (test_id,),
    ).fetchall()

    by_skill = {row["skill_id"]: row for row in rows}

    stats = []
    for skill in skills:
        row = by_skill.get(skill["id"])
        correct = row["correct"] if row else 0
        total = row["total"] if row else 0
        stats.append(
            {
                "skill_id": skill["id"],
                "position": skill["position"],
                "title": skill["title"],
                "correct": correct,
                "total": total,
                "percent": percent_of(correct, total),
            }
        )
    return stats


@router.get(
    "/{results_token}",
    response_model=ResultsOverview,
    summary="Таблица учеников, матрица умений и сводка",
)
def get_results(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
) -> ResultsOverview:
    """Всё, что нужно главному экрану результатов, одним запросом."""
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)
        skills = load_skills(conn, test_row["id"])
        attempt_rows = load_attempts(conn, test_row["id"])
        matrix = load_skill_matrix(conn, test_row["id"])
        skill_stats = load_skill_stats(conn, test_row["id"], skills)

    attempts = []
    for attempt in attempt_rows:
        # Процент по каждому умению для этого ученика.
        skill_percents: dict[int, int] = {}
        for skill in skills:
            cell = matrix.get((attempt["id"], skill["id"]))
            if cell is not None:
                skill_percents[skill["id"]] = percent_of(cell["correct"], cell["total"])

        attempts.append(
            {
                "attempt_id": attempt["id"],
                "student_name": attempt["student_name"],
                "student_class": attempt["student_class"],
                "variant_no": attempt["variant_no"],
                "score": attempt["score"] or 0,
                "max_score": attempt["max_score"] or 0,
                "percent": percent_of(attempt["score"], attempt["max_score"]),
                "finished_at": attempt["finished_at"],
                "skill_percents": skill_percents,
            }
        )

    return ResultsOverview(
        title=test_row["title"],
        teacher_name=test_row["teacher_name"],
        code=test_row["share_token"],
        classes=test_row["classes"],
        variants_count=test_row["variants_count"],
        is_open=test_row["is_open"],
        skills=skills,
        attempts_count=len(attempts),
        attempts=attempts,
        skill_stats=skill_stats,
    )


def load_attempt_items(conn, attempt_id: int, test_id: int, variant_no: int) -> list[dict]:
    """
    Разбор работы: по каждому заданию варианта — ответ ученика и правильный ответ.

    Здесь правильные ответы показывать МОЖНО: это экран учителя, защищённый
    секретной ссылкой. В публичном роутере их нет.
    """
    rows = conn.execute(
        """
        SELECT
            t.id                AS task_id,
            t.position          AS position,
            t.text              AS text,
            t.answer_format     AS answer_format,
            t.accepted_answers  AS accepted_answers,
            t.solution          AS solution,
            s.title             AS skill_title,
            chosen.text         AS chosen_text,
            right_option.text   AS right_option_text,
            ans.answer_text     AS answer_text,
            ans.id IS NOT NULL  AS has_row,
            COALESCE(ans.is_correct, FALSE) AS is_correct
        FROM tasks t
        JOIN skills s ON s.id = t.skill_id
        LEFT JOIN answers ans        ON ans.task_id = t.id AND ans.attempt_id = %s
        LEFT JOIN task_options chosen ON chosen.id = ans.option_id
        LEFT JOIN task_options right_option
               ON right_option.task_id = t.id AND right_option.is_correct
        WHERE t.test_id = %s AND t.variant_no = %s
        ORDER BY t.position, t.id
        """,
        (attempt_id, test_id, variant_no),
    ).fetchall()

    items = []
    for row in rows:
        if row["answer_format"] == "choice":
            student_answer = row["chosen_text"]
            correct_answer = row["right_option_text"] or ""
        else:
            student_answer = row["answer_text"]
            # Допустимых ответов может быть несколько — показываем все.
            correct_answer = " / ".join(row["accepted_answers"])

        items.append(
            {
                "task_id": row["task_id"],
                "position": row["position"],
                "skill_title": row["skill_title"],
                "text": row["text"],
                "answer_format": row["answer_format"],
                "student_answer": student_answer,
                "correct_answer": correct_answer,
                "solution": row["solution"],
                "answered": bool(student_answer),
                "is_correct": row["is_correct"],
            }
        )

    return items


@router.get(
    "/{results_token}/attempts/{attempt_id}",
    response_model=AttemptDetail,
    summary="Разбор одной работы",
)
def get_attempt_detail(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
    attempt_id: int = Path(ge=1),
) -> AttemptDetail:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        # test_id в условии обязателен: иначе по одному токену можно было бы
        # листать работы из чужих контрольных, подставляя id.
        attempt_row = conn.execute(
            """
            SELECT id, student_name, student_class, variant_no,
                   score, max_score, finished_at
            FROM attempts
            WHERE id = %s AND test_id = %s
            """,
            (attempt_id, test_row["id"]),
        ).fetchone()

        if attempt_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Такая работа в этой контрольной не найдена.",
            )

        items = load_attempt_items(
            conn, attempt_id, test_row["id"], attempt_row["variant_no"]
        )

    return AttemptDetail(
        attempt_id=attempt_row["id"],
        student_name=attempt_row["student_name"],
        student_class=attempt_row["student_class"],
        variant_no=attempt_row["variant_no"],
        score=attempt_row["score"] or 0,
        max_score=attempt_row["max_score"] or len(items),
        percent=percent_of(attempt_row["score"], attempt_row["max_score"]),
        finished_at=attempt_row["finished_at"],
        items=items,
    )


@router.get(
    "/{results_token}/export.xlsx",
    summary="Выгрузить результаты в Excel",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
            "description": "Файл .xlsx",
        }
    },
)
def export_results(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
) -> Response:
    """
    Три листа:
      «Ученики» — класс, ФИО, вариант, балл, процент, время сдачи;
      «Умения»  — матрица «ученик × умение» с цветом и строкой по классу;
      «Задания» — все задания по вариантам с ответом, решением и статистикой.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)
        test_id = test_row["id"]

        skills = load_skills(conn, test_id)
        attempts = load_attempts(conn, test_id)
        matrix = load_skill_matrix(conn, test_id)
        skill_stats = load_skill_stats(conn, test_id, skills)

        # Все задания с их статистикой — для третьего листа.
        task_rows = conn.execute(
            """
            SELECT t.id, t.variant_no, t.position, t.text, t.answer_format,
                   t.accepted_answers, t.solution, s.title AS skill_title,
                   right_option.text AS right_option_text,
                   count(ans.id)                          AS answered,
                   count(ans.id) FILTER (WHERE ans.is_correct) AS correct
            FROM tasks t
            JOIN skills s ON s.id = t.skill_id
            LEFT JOIN task_options right_option
                   ON right_option.task_id = t.id AND right_option.is_correct
            LEFT JOIN answers ans ON ans.task_id = t.id
            WHERE t.test_id = %s
            GROUP BY t.id, t.variant_no, t.position, t.text, t.answer_format,
                     t.accepted_answers, t.solution, s.title, right_option.text
            ORDER BY t.variant_no, t.position, t.id
            """,
            (test_id,),
        ).fetchall()

    workbook = Workbook()

    # ---------- Лист 1: ученики ----------
    sheet = workbook.active
    sheet.title = "Ученики"
    sheet.append(["Класс", "ФИО", "Вариант", "Балл", "Максимум", "%", "Время сдачи"])

    for attempt in attempts:
        finished: datetime | None = attempt["finished_at"]
        sheet.append(
            [
                attempt["student_class"],
                attempt["student_name"],
                attempt["variant_no"],
                attempt["score"] or 0,
                attempt["max_score"] or 0,
                percent_of(attempt["score"], attempt["max_score"]),
                # Excel не хранит часовой пояс — приводим к местному времени.
                finished.astimezone().replace(tzinfo=None) if finished else None,
            ]
        )

    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    for row in sheet.iter_rows(min_row=2, min_col=7, max_col=7):
        for cell in row:
            cell.number_format = "DD.MM.YYYY HH:MM"
    for letter, width in zip("ABCDEFG", (10, 28, 9, 8, 11, 7, 18)):
        sheet.column_dimensions[letter].width = width

    # ---------- Лист 2: умения ----------
    skills_sheet = workbook.create_sheet("Умения")
    skills_sheet.append(
        ["Класс", "ФИО"] + [f"{skill['position']}. {skill['title']}" for skill in skills]
    )

    for attempt in attempts:
        row_values = [attempt["student_class"], attempt["student_name"]]
        for skill in skills:
            cell = matrix.get((attempt["id"], skill["id"]))
            row_values.append(percent_of(cell["correct"], cell["total"]) if cell else None)
        skills_sheet.append(row_values)

    # Последняя строка — итог по всем работам (то же, что «строка по классу» на экране).
    skills_sheet.append(
        ["", "Итого по классу"] + [stat["percent"] for stat in skill_stats]
    )

    for cell in skills_sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for cell in skills_sheet[skills_sheet.max_row]:
        cell.font = Font(bold=True)
    skills_sheet.freeze_panes = "C2"

    # Цвет по тем же порогам, что и на экране: <50 красный, 50–65 жёлтый, >65 зелёный.
    for row in skills_sheet.iter_rows(min_row=2, min_col=3):
        for cell in row:
            if isinstance(cell.value, int):
                cell.fill = fill_for(cell.value)
                cell.number_format = '0"%"'
                cell.alignment = Alignment(horizontal="center")

    skills_sheet.column_dimensions["A"].width = 10
    skills_sheet.column_dimensions["B"].width = 28
    for index in range(len(skills)):
        letter = skills_sheet.cell(row=1, column=3 + index).column_letter
        skills_sheet.column_dimensions[letter].width = 16

    # ---------- Лист 3: задания ----------
    tasks_sheet = workbook.create_sheet("Задания")
    tasks_sheet.append(
        [
            "Вариант",
            "№",
            "Умение",
            "Задание",
            "Формат",
            "Правильный ответ",
            "Решение",
            "Верно",
            "Ответов",
            "%",
        ]
    )

    for task in task_rows:
        if task["answer_format"] == "choice":
            correct_answer = task["right_option_text"] or ""
            format_name = "выбор"
        else:
            correct_answer = " / ".join(task["accepted_answers"])
            format_name = "ввод"

        tasks_sheet.append(
            [
                task["variant_no"],
                task["position"],
                task["skill_title"],
                task["text"],
                format_name,
                correct_answer,
                task["solution"],
                task["correct"],
                task["answered"],
                percent_of(task["correct"], task["answered"]),
            ]
        )

    for cell in tasks_sheet[1]:
        cell.font = Font(bold=True)
    tasks_sheet.freeze_panes = "A2"
    for letter, width in zip("ABCDEFGHIJ", (9, 5, 24, 52, 9, 22, 40, 8, 9, 7)):
        tasks_sheet.column_dimensions[letter].width = width
    for row in tasks_sheet.iter_rows(min_row=2, min_col=4, max_col=4):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    # Имя файла только из латиницы и цифр: кириллица в Content-Disposition
    # ломается в части браузеров.
    filename = f"results-{test_row['share_token']}.xlsx"

    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =====================================================================
# Управление контрольной (всё — по той же секретной ссылке)
# =====================================================================


@router.patch(
    "/{results_token}",
    response_model=ResultsOverviewSettings,
    summary="Открыть или закрыть приём работ",
)
def update_settings(
    payload: TestSettingsUpdate,
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
) -> ResultsOverviewSettings:
    """Закрытый приём ничего не удаляет: сданные работы остаются на месте."""
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)
        conn.execute(
            "UPDATE tests SET is_open = %s WHERE id = %s",
            (payload.is_open, test_row["id"]),
        )

    logger.info(
        "Приём работ по тесту %s: %s",
        test_row["share_token"],
        "открыт" if payload.is_open else "закрыт",
    )
    return ResultsOverviewSettings(is_open=payload.is_open)


@router.delete(
    "/{results_token}/attempts/{attempt_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить работу ученика (разрешить пересдачу)",
)
def delete_attempt(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
    attempt_id: int = Path(ge=1),
) -> Response:
    """
    Удаляет одну работу — так учитель разрешает пересдачу: запись о попытке
    исчезает, и ученик снова может начать. Ответы уходят каскадом.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        deleted = conn.execute(
            "DELETE FROM attempts WHERE id = %s AND test_id = %s RETURNING id",
            (attempt_id, test_row["id"]),
        ).fetchone()

        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Такая работа в этой контрольной не найдена.",
            )

    logger.info("Удалена работа %s из теста %s", attempt_id, test_row["share_token"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{results_token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить контрольную вместе со всеми работами",
)
def delete_test(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
    confirm_title: str = Query(
        description="Точное название контрольной — подтверждение удаления",
    ),
) -> Response:
    """
    Удаляет контрольную со всеми умениями, заданиями и работами. Необратимо.

    Защита двойная: подтверждение в браузере И название, которое сверяет сервер.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        # Сравниваем без пробелов по краям: учитель мог скопировать название.
        if confirm_title.strip() != test_row["title"].strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Название не совпадает — контрольная не удалена. "
                    "Введите название точно так, как оно указано в заголовке."
                ),
            )

        conn.execute("DELETE FROM tests WHERE id = %s", (test_row["id"],))

    logger.info("Удалена контрольная %s («%s»)", test_row["share_token"], test_row["title"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
