"""
Роутер результатов — то, что видит УЧИТЕЛЬ по своей проверочной работе.

    GET    /api/tests/{id}/results                — таблица учеников и умений
    GET    /api/tests/{id}/attempts/{attempt_id}  — разбор одной работы
    GET    /api/tests/{id}/export.xlsx            — выгрузка в Excel (готова к печати)
    GET    /api/tests/{id}/print/print.zip        — «Скачать для печати»: варианты и ключ (Word)
    GET    /api/tests/{id}/print/variants.docx    — только варианты
    GET    /api/tests/{id}/print/key.docx         — только ключ ответов
    PATCH  /api/tests/{id}                        — открыть/закрыть приём работ целиком
    POST   /api/tests/{id}/classes                — добавить класс (новая ссылка)
    PATCH  /api/tests/{id}/classes/{class_id}     — открыть/закрыть приём по классу
    POST   /api/tests/{id}/attempts/{attempt_id}/annul — разрешить пересдачу
    DELETE /api/tests/{id}/attempts/{attempt_id}  — удалить работу совсем
    DELETE /api/tests/{id}?confirm_title=...      — удалить проверочную работу целиком

Доступ даёт учётная запись: владелец проверочной работы или администратор. Секретных
ссылок больше нет — чужую проверочную работу не открыть, даже зная её номер.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from psycopg import errors as pg_errors

from app import db, docx_export, xlsx_export
from app.auth import can_manage_test, forbidden, require_user
from app.routers.tests import add_class_link
from app.schemas import (
    AttemptDetail,
    ClassLinkCreate,
    ClassLinkOut,
    ClassLinkUpdate,
    ResultsOverview,
    ResultsOverviewSettings,
    TestSettingsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tests", tags=["results"])


def db_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Сервис временно недоступен. Подождите минуту и обновите страницу.",
    )


def test_not_found() -> HTTPException:
    """404 на несуществующую проверочную работу."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Проверочная работа не найдена. Возможно, её удалили — вернитесь к списку работ.",
    )


def percent_of(correct: int | None, total: int | None) -> int:
    """Процент, округлённый до целого. Без деления на ноль."""
    if not correct or not total:
        return 0
    return round(correct * 100 / total)


def load_test_for_user(conn, test_id: int, user: dict) -> dict:
    """
    Находит проверочную работу и проверяет права на неё.

    Чужая проверочная работа даёт 403, а не 404: скрывать сам факт её существования
    не от кого — все учителя школы и так видят друг друга.
    """
    row = conn.execute(
        """
        SELECT id, title, subject, teacher_id, teacher_name, share_token,
               classes, is_open, variants_count, links_by_class
        FROM tests
        WHERE id = %s
        """,
        (test_id,),
    ).fetchone()

    if row is None:
        raise test_not_found()

    if not can_manage_test(user, row):
        raise forbidden("Это проверочная работа другого учителя — открыть её может только автор.")

    return row


def load_class_links(conn, test_id: int) -> list[dict]:
    """
    Ссылки по классам с числом сдавших. Порядок — как классы указаны в работе
    (добавленные позже — в конце).
    """
    return conn.execute(
        """
        SELECT tc.id, tc.class_name, tc.code, tc.is_open,
               (SELECT count(*) FROM attempts a
                 WHERE a.test_id = tc.test_id AND a.student_class = tc.class_name
                   AND a.finished_at IS NOT NULL AND a.annulled_at IS NULL) AS attempts_count
        FROM test_classes tc
        WHERE tc.test_id = %s
        ORDER BY tc.id
        """,
        (test_id,),
    ).fetchall()


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
    """
    Все попытки: сортировка по классу, затем по фамилии и имени.
    Аннулированные тоже здесь (с отметкой) — учитель видит историю пересдач,
    но в умения, итоги и выгрузку они не идут.
    """
    return conn.execute(
        """
        SELECT id, student_name, student_class, variant_no,
               score, max_score, finished_at, annulled_at IS NOT NULL AS annulled
        FROM attempts
        WHERE test_id = %s
        ORDER BY student_class, student_name, annulled_at IS NOT NULL, id
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
        WHERE att.test_id = %s AND att.annulled_at IS NULL
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
        WHERE att.test_id = %s AND att.annulled_at IS NULL
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
    "/{test_id}/results",
    response_model=ResultsOverview,
    summary="Таблица учеников, матрица умений и сводка",
)
def get_results(
    test_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> ResultsOverview:
    """Всё, что нужно главному экрану результатов, одним запросом."""
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        skills = load_skills(conn, test_row["id"])
        attempt_rows = load_attempts(conn, test_row["id"])
        matrix = load_skill_matrix(conn, test_row["id"])
        skill_stats = load_skill_stats(conn, test_row["id"], skills)
        class_links = load_class_links(conn, test_row["id"])

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
                "annulled": attempt["annulled"],
                "skill_percents": skill_percents,
            }
        )

    return ResultsOverview(
        id=test_row["id"],
        title=test_row["title"],
        subject=test_row["subject"],
        teacher_name=test_row["teacher_name"],
        code=test_row["share_token"],
        classes=test_row["classes"],
        variants_count=test_row["variants_count"],
        is_open=test_row["is_open"],
        links_by_class=test_row["links_by_class"],
        class_links=class_links,
        skills=skills,
        attempts_count=sum(1 for attempt in attempts if not attempt["annulled"]),
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
    "/{test_id}/attempts/{attempt_id}",
    response_model=AttemptDetail,
    summary="Разбор одной работы",
)
def get_attempt_detail(
    test_id: int = Path(ge=1),
    attempt_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> AttemptDetail:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)

        # test_id в условии обязателен: иначе по одному токену можно было бы
        # листать работы из чужих проверочных работ, подставляя id.
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
                detail="Ответ этого ученика не найден — возможно, его уже удалили. Обновите страницу.",
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


def load_task_marks(conn, test_id: int) -> dict[int, dict[int, bool]]:
    """{id попытки: {номер задания в варианте: верно?}} — баллы по заданиям для Excel."""
    rows = conn.execute(
        """
        SELECT ans.attempt_id, t.position, ans.is_correct
        FROM answers ans
        JOIN tasks t      ON t.id = ans.task_id
        JOIN attempts att ON att.id = ans.attempt_id
        WHERE att.test_id = %s AND att.annulled_at IS NULL
        """,
        (test_id,),
    ).fetchall()
    marks: dict[int, dict[int, bool]] = {}
    for row in rows:
        marks.setdefault(row["attempt_id"], {})[row["position"]] = row["is_correct"]
    return marks


def load_print_variants(conn, test_id: int) -> dict[int, list[dict]]:
    """
    Все задания работы по вариантам — с ответами и умениями: для печати и ключа.
    {номер варианта: [задания по порядку]}.
    """
    task_rows = conn.execute(
        """
        SELECT t.id, t.variant_no, t.position, t.text, t.answer_format,
               t.accepted_answers, s.title AS skill_title, s.position AS skill_position
        FROM tasks t
        JOIN skills s ON s.id = t.skill_id
        WHERE t.test_id = %s
        ORDER BY t.variant_no, t.position, t.id
        """,
        (test_id,),
    ).fetchall()
    option_rows = conn.execute(
        """
        SELECT o.task_id, o.text, o.is_correct
        FROM task_options o
        JOIN tasks t ON t.id = o.task_id
        WHERE t.test_id = %s
        ORDER BY o.position, o.id
        """,
        (test_id,),
    ).fetchall()

    options: dict[int, list[dict]] = {}
    for option in option_rows:
        options.setdefault(option["task_id"], []).append(option)

    variants: dict[int, list[dict]] = {}
    for task in task_rows:
        own = options.get(task["id"], [])
        variants.setdefault(task["variant_no"], []).append(
            {
                "text": task["text"],
                "answer_format": task["answer_format"],
                "options": [option["text"] for option in own],
                "correct": next(
                    (index for index, option in enumerate(own) if option["is_correct"]), None
                ),
                "accepted_answers": task["accepted_answers"],
                "skill_title": f"{task['skill_position']}. {task['skill_title']}",
            }
        )
    return variants


def file_response(content: bytes, filename: str, media_type: str) -> Response:
    # Имя файла только из латиницы и цифр: кириллица в Content-Disposition
    # ломается в части браузеров.
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/{test_id}/print/{kind}",
    summary="Скачать для печати: варианты и ключ ответов (Word)",
    response_class=Response,
)
def print_files(
    test_id: int = Path(ge=1),
    kind: str = Path(description="variants.docx, key.docx или print.zip (оба файла)"),
    user: dict = Depends(require_user),
) -> Response:
    """
    «Варианты» — для учеников: каждый вариант с новой страницы, с шапкой и
    местом для ответа. «Ключ ответов» — для учителя. Формулы — формулами Word.
    """
    if kind not in ("variants.docx", "key.docx", "print.zip"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Такого файла нет.")

    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        variants = load_print_variants(conn, test_row["id"])

    code = test_row["share_token"]
    if kind == "variants.docx":
        return file_response(docx_export.build_variants(test_row, variants), f"variants-{code}.docx", DOCX_TYPE)
    if kind == "key.docx":
        return file_response(docx_export.build_key(test_row, variants), f"key-{code}.docx", DOCX_TYPE)
    archive = docx_export.build_zip(
        code, docx_export.build_variants(test_row, variants), docx_export.build_key(test_row, variants)
    )
    return file_response(archive, f"print-{code}.zip", "application/zip")


@router.get(
    "/{test_id}/export.xlsx",
    summary="Выгрузить результаты в Excel",
    response_class=Response,
    responses={200: {"content": {XLSX_TYPE: {}}, "description": "Файл .xlsx"}},
)
def export_results(
    test_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> Response:
    """
    Листы «Результаты» (баллы по заданиям), «По умениям» (умение × класс) и
    «Задания». Только сданные и не аннулированные работы. Оформление и
    настройки печати — в app/xlsx_export.py.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        test_id = test_row["id"]

        skills = load_skills(conn, test_id)
        attempts = [
            attempt
            for attempt in load_attempts(conn, test_id)
            if not attempt["annulled"] and attempt["finished_at"] is not None
        ]
        matrix = load_skill_matrix(conn, test_id)
        marks = load_task_marks(conn, test_id)

        # Все задания с их статистикой — для листа «Задания».
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
            LEFT JOIN (
                SELECT a.id, a.task_id, a.is_correct
                FROM answers a
                JOIN attempts att ON att.id = a.attempt_id
                WHERE att.annulled_at IS NULL AND att.finished_at IS NOT NULL
            ) ans ON ans.task_id = t.id
            WHERE t.test_id = %s
            GROUP BY t.id, t.variant_no, t.position, t.text, t.answer_format,
                     t.accepted_answers, t.solution, s.title, right_option.text
            ORDER BY t.variant_no, t.position, t.id
            """,
            (test_id,),
        ).fetchall()

    content = xlsx_export.build_results_xlsx(test_row, skills, attempts, marks, matrix, task_rows)
    return file_response(content, f"results-{test_row['share_token']}.xlsx", XLSX_TYPE)


# =====================================================================
# Управление проверочной работой (всё — по той же секретной ссылке)
# =====================================================================


@router.patch(
    "/{test_id}",
    response_model=ResultsOverviewSettings,
    summary="Открыть или закрыть приём работ",
)
def update_settings(
    payload: TestSettingsUpdate,
    test_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> ResultsOverviewSettings:
    """Закрытый приём ничего не удаляет: сданные работы остаются на месте."""
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn, conn.transaction():
        test_row = load_test_for_user(conn, test_id, user)
        conn.execute(
            "UPDATE tests SET is_open = %s WHERE id = %s",
            (payload.is_open, test_row["id"]),
        )
        # «Закрыть приём» целиком закрывает и все ссылки классов (и наоборот).
        conn.execute(
            "UPDATE test_classes SET is_open = %s WHERE test_id = %s",
            (payload.is_open, test_row["id"]),
        )

    logger.info(
        "Приём работ по тесту %s: %s",
        test_row["share_token"],
        "открыт" if payload.is_open else "закрыт",
    )
    return ResultsOverviewSettings(is_open=payload.is_open)


@router.post(
    "/{test_id}/classes",
    response_model=ClassLinkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить класс к опубликованной работе",
)
def add_class(
    payload: ClassLinkCreate,
    test_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> ClassLinkOut:
    """Новый класс получает свою ссылку; остальные ссылки и сданные работы не меняются."""
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    try:
        with pool.connection() as conn, conn.transaction():
            test_row = load_test_for_user(conn, test_id, user)
            row = add_class_link(conn, test_row["id"], payload.class_name)
            conn.execute(
                """
                UPDATE tests SET classes = array_append(classes, %s)
                WHERE id = %s AND NOT (%s = ANY(classes))
                """,
                (payload.class_name, test_row["id"], payload.class_name),
            )
    except pg_errors.UniqueViolation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Класс «{payload.class_name}» уже есть в этой работе.",
        ) from None

    logger.info("К работе %s добавлен класс %s", test_row["share_token"], payload.class_name)
    return ClassLinkOut(**row)


@router.patch(
    "/{test_id}/classes/{class_id}",
    response_model=ClassLinkOut,
    summary="Открыть или закрыть приём работ по одному классу",
)
def update_class(
    payload: ClassLinkUpdate,
    test_id: int = Path(ge=1),
    class_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> ClassLinkOut:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        # test_id в условии обязателен: иначе, зная номер, можно было бы
        # закрыть приём в чужой работе.
        updated = conn.execute(
            "UPDATE test_classes SET is_open = %s WHERE id = %s AND test_id = %s RETURNING id",
            (payload.is_open, class_id, test_row["id"]),
        ).fetchone()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Такого класса в этой работе нет. Обновите страницу.",
            )
        row = next(link for link in load_class_links(conn, test_row["id"]) if link["id"] == class_id)

    logger.info(
        "Приём работ по тесту %s, класс %s: %s",
        test_row["share_token"],
        row["class_name"],
        "открыт" if payload.is_open else "закрыт",
    )
    return ClassLinkOut(**row)


@router.post(
    "/{test_id}/attempts/{attempt_id}/annul",
    summary="Разрешить пересдачу: аннулировать попытку",
)
def annul_attempt(
    test_id: int = Path(ge=1),
    attempt_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> dict:
    """
    Попытка остаётся в базе с отметкой «аннулирована»: её видно в списке работ,
    но в умения, итоги, статистику и Excel она не идёт. Ученик (и его устройство)
    снова может начать работу — по той же ссылке.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)
        row = conn.execute(
            """
            UPDATE attempts SET annulled_at = coalesce(annulled_at, now())
            WHERE id = %s AND test_id = %s
            RETURNING id, student_name
            """,
            (attempt_id, test_row["id"]),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ответ этого ученика не найден — возможно, его уже удалили. Обновите страницу.",
            )

    logger.info(
        "Разрешена пересдача: попытка %s (%s) в тесте %s аннулирована, учитель %s",
        attempt_id,
        row["student_name"],
        test_row["share_token"],
        user["email"],
    )
    return {"attempt_id": attempt_id, "annulled": True}


@router.delete(
    "/{test_id}/attempts/{attempt_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить работу ученика совсем",
)
def delete_attempt(
    test_id: int = Path(ge=1),
    attempt_id: int = Path(ge=1),
    user: dict = Depends(require_user),
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
        test_row = load_test_for_user(conn, test_id, user)

        deleted = conn.execute(
            "DELETE FROM attempts WHERE id = %s AND test_id = %s RETURNING id",
            (attempt_id, test_row["id"]),
        ).fetchone()

        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ответ этого ученика не найден — возможно, его уже удалили. Обновите страницу.",
            )

    logger.info("Удалена работа %s из теста %s", attempt_id, test_row["share_token"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить проверочную работу вместе с ответами учеников",
)
def delete_test(
    test_id: int = Path(ge=1),
    user: dict = Depends(require_user),
    confirm_title: str = Query(
        description="Точное название проверочной работы — подтверждение удаления",
    ),
) -> Response:
    """
    Удаляет проверочную работу со всеми умениями, заданиями и работами. Необратимо.

    Защита двойная: подтверждение в браузере И название, которое сверяет сервер.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_test_for_user(conn, test_id, user)

        # Сравниваем без пробелов по краям: учитель мог скопировать название.
        if confirm_title.strip() != test_row["title"].strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Название не совпадает — работа не удалена. "
                    "Введите название точно так, как оно указано в заголовке."
                ),
            )

        conn.execute("DELETE FROM tests WHERE id = %s", (test_row["id"],))

    logger.info("Удалена проверочная работа %s («%s»)", test_row["share_token"], test_row["title"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
