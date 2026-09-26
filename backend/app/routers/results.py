"""
Роутер результатов — то, что видит УЧИТЕЛЬ по секретной ссылке.

    GET /api/results/{results_token}                        — таблица сдавших + сводка
    GET /api/results/{results_token}/attempts/{attempt_id}  — детали одной работы
    GET /api/results/{results_token}/export.xlsx            — выгрузка в Excel

Доступ: авторизации пока нет, вместо неё длинный секрет в адресе.
Искать контрольную здесь можно ТОЛЬКО по results_token — ученический
share_token в этих запросах не сработает, потому что это разные колонки.
"""

import io
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app import db
from app.schemas import (
    AttemptDetail,
    ResultsOverview,
    ResultsOverviewSettings,
    TestSettingsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/results", tags=["results"])

# Минимальная длина токена. Ученический код — 8 символов, он сюда не подойдёт
# и по длине, но проверку всё равно делает база: 404 вместо результатов.
TOKEN_MIN_LEN = 8
TOKEN_MAX_LEN = 200


def db_unavailable() -> HTTPException:
    """Одинаковый понятный ответ, когда база не отвечает."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="База данных недоступна. Проверьте, что PostgreSQL запущен.",
    )


def results_not_found() -> HTTPException:
    """
    404 на неверный токен.

    Текст без подробностей: по ответу нельзя понять, существует ли такая
    контрольная. Это важно, ведь ссылка заменяет пароль.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Результаты не найдены. Проверьте ссылку — она отличается от ученической.",
    )


def percent_of(score: int | None, max_score: int | None) -> int:
    """Процент верных ответов, округлённый до целого. Без деления на ноль."""
    if not score or not max_score:
        return 0
    return round(score * 100 / max_score)


def load_test_by_results_token(conn, results_token: str) -> dict:
    """Находит контрольную по СЕКРЕТНОМУ токену результатов или бросает 404."""
    row = conn.execute(
        """
        SELECT id, title, teacher_name, share_token, classes, shuffle, is_open
        FROM tests
        WHERE results_token = %s
        """,
        (results_token,),
    ).fetchone()

    if row is None:
        raise results_not_found()

    return row


def load_attempt_rows(conn, test_id: int) -> list[dict]:
    """Список сдавших: сортировка по классу, затем по фамилии и имени."""
    return conn.execute(
        """
        SELECT id, student_name, student_class, score, max_score, finished_at
        FROM attempts
        WHERE test_id = %s
        ORDER BY student_class, student_name, id
        """,
        (test_id,),
    ).fetchall()


def load_question_stats(conn, test_id: int) -> list[dict]:
    """
    Сводка по вопросам: сколько ответили верно, неверно и сколько пропустили.

    LEFT JOIN — чтобы вопрос попал в сводку даже если его никто не решал.
    count(a.id) FILTER (...) считает только существующие строки ответов,
    поэтому у нерешавшегося вопроса получаются честные нули.
    """
    return conn.execute(
        """
        SELECT
            q.id,
            q.position,
            q.text,
            count(a.id) FILTER (WHERE o.is_correct)                  AS correct_count,
            count(a.id) FILTER (WHERE a.option_id IS NOT NULL
                                  AND NOT o.is_correct)              AS wrong_count,
            count(a.id) FILTER (WHERE a.option_id IS NULL)           AS skipped_count
        FROM questions q
        LEFT JOIN answers a ON a.question_id = q.id
        LEFT JOIN options o ON o.id = a.option_id
        WHERE q.test_id = %s
        GROUP BY q.id, q.position, q.text
        ORDER BY q.position, q.id
        """,
        (test_id,),
    ).fetchall()


@router.get(
    "/{results_token}",
    response_model=ResultsOverview,
    summary="Таблица сдавших и сводка по вопросам",
)
def get_results(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
) -> ResultsOverview:
    """Всё, что нужно для главного экрана результатов, одним запросом."""
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        questions_count = conn.execute(
            "SELECT count(*) AS n FROM questions WHERE test_id = %s",
            (test_row["id"],),
        ).fetchone()["n"]

        attempt_rows = load_attempt_rows(conn, test_row["id"])
        stat_rows = load_question_stats(conn, test_row["id"])

    return ResultsOverview(
        title=test_row["title"],
        teacher_name=test_row["teacher_name"],
        code=test_row["share_token"],
        classes=test_row["classes"],
        shuffle=test_row["shuffle"],
        is_open=test_row["is_open"],
        questions_count=questions_count,
        attempts_count=len(attempt_rows),
        attempts=[
            {
                "attempt_id": row["id"],
                "student_name": row["student_name"],
                "student_class": row["student_class"],
                "score": row["score"] or 0,
                "max_score": row["max_score"] or questions_count,
                "percent": percent_of(row["score"], row["max_score"]),
                "finished_at": row["finished_at"],
            }
            for row in attempt_rows
        ],
        question_stats=[
            {
                "question_id": row["id"],
                "position": row["position"],
                "text": row["text"],
                "correct_count": row["correct_count"],
                "wrong_count": row["wrong_count"],
                "skipped_count": row["skipped_count"],
            }
            for row in stat_rows
        ],
    )


@router.get(
    "/{results_token}/attempts/{attempt_id}",
    response_model=AttemptDetail,
    summary="Детали одной работы",
)
def get_attempt_detail(
    results_token: str = Path(min_length=TOKEN_MIN_LEN, max_length=TOKEN_MAX_LEN),
    attempt_id: int = Path(ge=1),
) -> AttemptDetail:
    """
    По каждому вопросу: что выбрал ученик, что было правильным, верно или нет.

    Здесь правильные ответы показывать можно — это экран учителя, защищённый
    секретной ссылкой. В публичном роутере (app/routers/public.py) их нет.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        # Проверяем, что работа относится ИМЕННО к этой контрольной: иначе по одному
        # токену можно было бы листать работы из чужих тестов, подставляя id.
        attempt_row = conn.execute(
            """
            SELECT id, student_name, student_class, score, max_score, finished_at
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

        # Один запрос на все вопросы работы:
        #   chosen  — вариант, который выбрал ученик (может не быть строки → пропуск),
        #   correct — правильный вариант этого вопроса.
        item_rows = conn.execute(
            """
            SELECT
                q.id                AS question_id,
                q.position          AS position,
                q.text              AS question_text,
                chosen.text         AS chosen_option_text,
                correct.text        AS correct_option_text,
                (a.option_id IS NOT NULL)          AS answered,
                COALESCE(chosen.is_correct, FALSE) AS is_correct
            FROM questions q
            LEFT JOIN answers a  ON a.question_id = q.id AND a.attempt_id = %s
            LEFT JOIN options chosen  ON chosen.id = a.option_id
            LEFT JOIN options correct ON correct.question_id = q.id
                                     AND correct.is_correct
            WHERE q.test_id = %s
            ORDER BY q.position, q.id
            """,
            (attempt_id, test_row["id"]),
        ).fetchall()

    return AttemptDetail(
        attempt_id=attempt_row["id"],
        student_name=attempt_row["student_name"],
        student_class=attempt_row["student_class"],
        score=attempt_row["score"] or 0,
        max_score=attempt_row["max_score"] or len(item_rows),
        percent=percent_of(attempt_row["score"], attempt_row["max_score"]),
        finished_at=attempt_row["finished_at"],
        items=[
            {
                "question_id": row["question_id"],
                "position": row["position"],
                "question_text": row["question_text"],
                "chosen_option_text": row["chosen_option_text"],
                "correct_option_text": row["correct_option_text"],
                "answered": row["answered"],
                "is_correct": row["is_correct"],
            }
            for row in item_rows
        ],
    )


@router.get(
    "/{results_token}/export.xlsx",
    summary="Выгрузить таблицу результатов в Excel",
    # Описываем ответ вручную: это файл, а не JSON, и схемы у него нет.
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
    Собирает .xlsx в памяти и отдаёт файлом.

    Столбцы: ФИО, класс, балл, процент, время сдачи, затем по одному столбцу
    на каждый вопрос: «+» верно, «−» неверно, пусто — вопрос пропущен.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        question_rows = conn.execute(
            """
            SELECT id, position
            FROM questions
            WHERE test_id = %s
            ORDER BY position, id
            """,
            (test_row["id"],),
        ).fetchall()

        attempt_rows = load_attempt_rows(conn, test_row["id"])

        # Все ответы всех работ этой контрольной — одним запросом,
        # чтобы не дёргать базу по разу на ученика.
        answer_rows = conn.execute(
            """
            SELECT
                a.attempt_id,
                a.question_id,
                (a.option_id IS NOT NULL)      AS answered,
                COALESCE(o.is_correct, FALSE)  AS is_correct
            FROM answers a
            JOIN attempts att ON att.id = a.attempt_id
            LEFT JOIN options o ON o.id = a.option_id
            WHERE att.test_id = %s
            """,
            (test_row["id"],),
        ).fetchall()

    # Отметка по каждой паре (работа, вопрос): «+», «−» или пусто.
    marks: dict[tuple[int, int], str] = {}
    for row in answer_rows:
        if not row["answered"]:
            mark = ""  # вопрос пропущен
        elif row["is_correct"]:
            mark = "+"
        else:
            mark = "−"
        marks[(row["attempt_id"], row["question_id"])] = mark

    # --- Собираем сам файл ------------------------------------------------
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Результаты"

    header = ["ФИО", "Класс", "Балл", "Максимум", "%", "Время сдачи"]
    header += [f"В{row['position']}" for row in question_rows]
    sheet.append(header)

    for attempt in attempt_rows:
        finished: datetime | None = attempt["finished_at"]
        row_values = [
            attempt["student_name"],
            attempt["student_class"],
            attempt["score"] or 0,
            attempt["max_score"] or len(question_rows),
            percent_of(attempt["score"], attempt["max_score"]),
            # Excel не умеет хранить часовой пояс — приводим к местному времени
            # и убираем сведения о зоне.
            finished.astimezone().replace(tzinfo=None) if finished else None,
        ]
        row_values += [
            marks.get((attempt["id"], question["id"]), "") for question in question_rows
        ]
        sheet.append(row_values)

    # Шапка жирная и закреплена: при прокрутке длинного списка её видно.
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"

    # Формат даты в столбце «Время сдачи» (шестой по счёту).
    for row in sheet.iter_rows(min_row=2, min_col=6, max_col=6):
        for cell in row:
            cell.number_format = "DD.MM.YYYY HH:MM"

    # Ширина столбцов по содержимому: берём самую длинную строку в столбце.
    for column_index, column_cells in enumerate(sheet.columns, start=1):
        longest = 0
        for cell in column_cells:
            if cell.value is None:
                continue
            if isinstance(cell.value, datetime):
                length = 16  # "31.12.2026 23:59"
            else:
                length = len(str(cell.value))
            longest = max(longest, length)
        # +2 символа на воздух, но не шире 60 — иначе длинный вопрос растянет таблицу.
        sheet.column_dimensions[
            sheet.cell(row=1, column=column_index).column_letter
        ].width = min(longest + 2, 60)

    # --- Второй лист: сводка по вопросам ---------------------------------
    # Учителю удобнее видеть её сразу в файле, а не только на экране.
    with pool.connection() as conn:
        stat_rows = load_question_stats(conn, test_row["id"])

    stats_sheet = workbook.create_sheet("По вопросам")
    stats_sheet.append(["№", "Вопрос", "Верно", "Неверно", "Пропущено"])
    for stat in stat_rows:
        stats_sheet.append(
            [
                stat["position"],
                stat["text"],
                stat["correct_count"],
                stat["wrong_count"],
                stat["skipped_count"],
            ]
        )
    for cell in stats_sheet[1]:
        cell.font = Font(bold=True)
    stats_sheet.column_dimensions["A"].width = 5
    stats_sheet.column_dimensions["B"].width = 60
    for letter in ("C", "D", "E"):
        stats_sheet.column_dimensions[letter].width = 12

    # Пишем книгу в память, а не в файл на диске.
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    # Имя файла — только латиница и цифры: кириллица в заголовке Content-Disposition
    # ломается в части браузеров.
    filename = f"results-{test_row['share_token']}.xlsx"

    return Response(
        content=buffer.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
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
    """
    Переключает приём работ.

    Закрытый приём не удаляет ничего: ученики просто видят сообщение
    «приём работ закрыт», а уже сданные работы остаются на месте.
    """
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
    Удаляет одну работу.

    Это и есть способ разрешить пересдачу: уникальный индекс из миграции 004
    больше не сработает, и ученик сможет сдать заново. Ответы удалятся сами —
    у answers.attempt_id стоит ON DELETE CASCADE.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        # test_id в условии обязателен: иначе по одному токену можно было бы
        # удалять работы из чужих контрольных, подставляя произвольный id.
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
    Удаляет контрольную со всеми вопросами и работами. Действие необратимо.

    Защита от случайного нажатия двойная: подтверждение в браузере И название,
    которое сервер сверяет сам. Без совпадения названия удаления не будет,
    даже если запрос отправлен мимо страницы.

    Вопросы, варианты, работы и ответы удаляются каскадом (см. миграции 001 и 004).
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_by_results_token(conn, results_token)

        # Сравниваем без учёта пробелов по краям: учитель мог скопировать название.
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
