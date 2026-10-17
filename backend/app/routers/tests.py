"""
Создание проверочной работы и список своих проверочных работ.

    POST /api/tests            — опубликовать проверочную работу (нужен вход)
    GET  /api/tests/{id}/draft — работа как черновик: для копии и для правки
    PUT  /api/tests/{id}       — заменить задания работы, пока её никто не писал
    GET  /api/my/tests         — список проверочных работ текущего учителя

Владелец проверочной работы — вошедший пользователь. ФИО учителя берётся из учётной
записи, а не из формы: так в результатах не появится «Учитель» или опечатка.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import errors as pg_errors

from app import db
from app.auth import can_manage_test, forbidden, require_user
from app.schemas import MyTestRow, TestCreate, TestCreated

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tests", tags=["tests"])

# Алфавит для кода ссылки: без 0/O/1/l/I — их путают при переписывании с доски.
CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
CODE_LENGTH = 8
# Сколько раз пробуем сгенерировать код, если случайно попался уже занятый.
CODE_ATTEMPTS = 5


def generate_code() -> str:
    """Случайный код для ссылки ученикам, например "k7mfp2xq"."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def unique_code(conn) -> str:
    """
    Код, которого нет ни среди общих кодов работ, ни среди кодов классов:
    и те и другие открываются по одному адресу /t/<код>.
    """
    while True:
        code = generate_code()
        taken = conn.execute(
            """
            SELECT 1 FROM tests WHERE share_token = %(code)s
            UNION ALL
            SELECT 1 FROM test_classes WHERE code = %(code)s
            LIMIT 1
            """,
            {"code": code},
        ).fetchone()
        if taken is None:
            return code


def add_class_link(conn, test_id: int, class_name: str, is_open: bool = True) -> dict:
    """Создаёт ссылку класса и возвращает её строку."""
    return conn.execute(
        """
        INSERT INTO test_classes (test_id, class_name, code, is_open)
        VALUES (%s, %s, %s, %s)
        RETURNING id, class_name, code, is_open, 0 AS attempts_count
        """,
        (test_id, class_name, unique_code(conn), is_open),
    ).fetchone()


def insert_content(conn, test_id: int, payload: TestCreate) -> None:
    """Умения, задания по вариантам и варианты ответа — для новой работы и для правки."""
    # 2. Умения. Запоминаем их id по порядковому номеру: задания
    #    ссылаются на умение номером (skill_index), а не id.
    skill_ids: dict[int, int] = {}
    for number, skill in enumerate(payload.skills, start=1):
        skill_row = conn.execute(
            """
            INSERT INTO skills (
                test_id, position, title, tasks_per_variant, answer_format, ai_hint
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                test_id,
                number,
                skill.title,
                skill.tasks_per_variant,
                skill.answer_format,
                skill.ai_hint,
            ),
        ).fetchone()
        skill_ids[number] = skill_row["id"]

    # 3. Задания по вариантам.
    for variant in payload.variants:
        for position, task in enumerate(variant.tasks, start=1):
            task_row = conn.execute(
                """
                INSERT INTO tasks (
                    test_id, skill_id, variant_no, position,
                    text, answer_format, accepted_answers, solution
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    test_id,
                    skill_ids[task.skill_index],
                    variant.variant_no,
                    position,
                    task.text,
                    task.answer_format,
                    task.accepted_answers,
                    task.solution,
                ),
            ).fetchone()

            # 4. Варианты ответа — только у заданий формата «выбор».
            if task.answer_format == "choice":
                option_rows = [
                    (
                        task_row["id"],
                        option_text,
                        index == task.correct,
                        index + 1,
                    )
                    for index, option_text in enumerate(task.options)
                ]
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO task_options
                            (task_id, text, is_correct, position)
                        VALUES (%s, %s, %s, %s)
                        """,
                        option_rows,
                    )


def db_unavailable() -> HTTPException:
    """Одинаковый понятный ответ, когда база не отвечает."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Сервис временно недоступен. Подождите минуту и попробуйте ещё раз — введённое на странице не пропадёт.",
    )


@router.post(
    "",
    response_model=TestCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Создать и опубликовать проверочную работу",
)
def create_test(payload: TestCreate, user: dict = Depends(require_user)) -> TestCreated:
    """
    Сохраняет проверочную работу целиком в ОДНОЙ транзакции.

    Порядок вставки: проверочная работа → умения → задания по вариантам → варианты
    ответа. Если что-то пойдёт не так на последнем шаге, откатится всё:
    половинчатых проверочных работ, где часть вариантов пустая, быть не может.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        logger.error("Нет соединения с базой: %s", exc)
        raise db_unavailable() from exc

    tasks_count = sum(len(variant.tasks) for variant in payload.variants)

    for attempt in range(CODE_ATTEMPTS):
        try:
            with pool.connection() as conn, conn.transaction():
                code = unique_code(conn)
                # 1. Сама проверочная работа. Ссылки у неё — по классам (см. ниже),
                #    общий код остаётся только как имя работы в логах и файлах.
                test_row = conn.execute(
                    """
                    INSERT INTO tests (
                        title, subject, teacher_id, teacher_name, share_token,
                        classes, shuffle, variants_count, is_published, links_by_class
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, TRUE)
                    RETURNING id
                    """,
                    (
                        payload.title,
                        payload.subject,
                        user["id"],
                        # ФИО берём из учётной записи — форма его больше не присылает.
                        user["full_name"],
                        code,
                        payload.classes,
                        payload.shuffle,
                        payload.variants_count,
                    ),
                ).fetchone()
                test_id = test_row["id"]

                # 1а. Своя ссылка на каждый класс.
                class_links = [
                    add_class_link(conn, test_id, class_name) for class_name in payload.classes
                ]

                insert_content(conn, test_id, payload)

            logger.info(
                "Создана проверочная работа id=%s код=%s (учитель %s): умений %s, вариантов %s, заданий %s",
                test_id,
                code,
                user["email"],
                len(payload.skills),
                payload.variants_count,
                tasks_count,
            )
            return TestCreated(
                id=test_id,
                code=code,
                class_links=class_links,
                title=payload.title,
                variants_count=payload.variants_count,
                skills_count=len(payload.skills),
                tasks_count=tasks_count,
            )

        except pg_errors.UniqueViolation:
            # Код ссылки уже занят — вероятность крошечная, но обработать надо.
            logger.warning("Код %s занят, генерирую новый (попытка %s)", code, attempt + 1)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось сохранить проверочную работу")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось сохранить работу. Подождите минуту и нажмите «Опубликовать» ещё раз.",
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Не удалось сгенерировать уникальный код ссылки. Попробуйте ещё раз.",
    )


ALREADY_WRITTEN = (
    "Работу уже писали: исправьте результаты в разборе (добавить ответ / засчитать / "
    "исключить) или сделайте копию работы."
)


def own_test(conn, test_id: int, user: dict) -> dict:
    row = conn.execute(
        """
        SELECT id, title, subject, teacher_id, classes, shuffle, variants_count, share_token
        FROM tests WHERE id = %s
        """,
        (test_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проверочная работа не найдена.")
    if not can_manage_test(user, row):
        raise forbidden("Это проверочная работа другого учителя — открыть её может только автор.")
    return row


def has_attempts(conn, test_id: int) -> bool:
    """Начинал ли работу хоть один ученик (аннулированные попытки тоже в счёт)."""
    return conn.execute("SELECT 1 FROM attempts WHERE test_id = %s LIMIT 1", (test_id,)).fetchone() is not None


@router.get("/{test_id}/draft", summary="Работа как черновик (для копии и правки)")
def test_draft(test_id: int, user: dict = Depends(require_user)) -> dict:
    """
    Шапка, умения (с «Уточнением для ИИ») и задания всех вариантов — в том виде,
    в каком их принимает POST /api/tests. Ответы, добавленные учителем после
    работы, входят в правильные ответы копии.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = own_test(conn, test_id, user)
        skills = conn.execute(
            """
            SELECT id, position, title, tasks_per_variant, answer_format, ai_hint
            FROM skills WHERE test_id = %s ORDER BY position, id
            """,
            (test_id,),
        ).fetchall()
        tasks = conn.execute(
            """
            SELECT id, skill_id, variant_no, position, text, answer_format,
                   accepted_answers || extra_answers AS accepted_answers, solution
            FROM tasks WHERE test_id = %s ORDER BY variant_no, position, id
            """,
            (test_id,),
        ).fetchall()
        options = conn.execute(
            """
            SELECT o.task_id, o.text, o.is_correct FROM task_options o
            JOIN tasks t ON t.id = o.task_id WHERE t.test_id = %s ORDER BY o.position, o.id
            """,
            (test_id,),
        ).fetchall()
        written = has_attempts(conn, test_id)

    skill_number = {skill["id"]: number for number, skill in enumerate(skills, start=1)}
    by_task: dict[int, list[dict]] = {}
    for option in options:
        by_task.setdefault(option["task_id"], []).append(option)

    variants: dict[int, list[dict]] = {}
    for task in tasks:
        own = by_task.get(task["id"], [])
        variants.setdefault(task["variant_no"], []).append(
            {
                "skill_index": skill_number[task["skill_id"]],
                "text": task["text"],
                "answer_format": task["answer_format"],
                "options": [option["text"] for option in own],
                "correct": next((i for i, option in enumerate(own) if option["is_correct"]), None),
                "accepted_answers": task["accepted_answers"],
                "solution": task["solution"],
            }
        )

    return {
        "id": test_row["id"],
        "title": test_row["title"],
        "subject": test_row["subject"],
        "classes": test_row["classes"],
        "variants_count": test_row["variants_count"],
        "shuffle": test_row["shuffle"],
        "skills": [
            {
                "title": skill["title"],
                "tasks_per_variant": skill["tasks_per_variant"],
                "answer_format": skill["answer_format"],
                "ai_hint": skill["ai_hint"],
            }
            for skill in skills
        ],
        "variants": [
            {"variant_no": number, "tasks": items} for number, items in sorted(variants.items())
        ],
        # Работу уже писали — менять её нельзя, только копировать.
        "has_attempts": written,
    }


@router.put("/{test_id}", response_model=TestCreated, summary="Заменить задания работы (пока её не писали)")
def update_test(test_id: int, payload: TestCreate, user: dict = Depends(require_user)) -> TestCreated:
    """
    Работа опубликована, но её ещё никто не начинал — задания можно заменить
    целиком (после правки во всех вариантах). Ссылки классов сохраняются: новым
    классам выдаются новые ссылки, убранные классы удаляются.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn, conn.transaction():
        test_row = own_test(conn, test_id, user)
        conn.execute("SELECT id FROM tests WHERE id = %s FOR UPDATE", (test_id,))
        if has_attempts(conn, test_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_WRITTEN)

        conn.execute(
            """
            UPDATE tests SET title = %s, subject = %s, classes = %s, shuffle = %s,
                             variants_count = %s, variant_counter = 0
            WHERE id = %s
            """,
            (payload.title, payload.subject, payload.classes, payload.shuffle, payload.variants_count, test_id),
        )
        # Умения уходят вместе с заданиями и вариантами ответа (каскад).
        conn.execute("DELETE FROM skills WHERE test_id = %s", (test_id,))
        insert_content(conn, test_id, payload)

        existing = {
            row["class_name"]: row
            for row in conn.execute(
                "SELECT id, class_name FROM test_classes WHERE test_id = %s", (test_id,)
            ).fetchall()
        }
        for name, row in existing.items():
            if name not in payload.classes:
                conn.execute("DELETE FROM test_classes WHERE id = %s", (row["id"],))
        for name in payload.classes:
            if name not in existing:
                add_class_link(conn, test_id, name)
        conn.execute("UPDATE test_classes SET variant_counter = 0 WHERE test_id = %s", (test_id,))
        class_links = conn.execute(
            """
            SELECT id, class_name, code, is_open, 0 AS attempts_count
            FROM test_classes WHERE test_id = %s ORDER BY id
            """,
            (test_id,),
        ).fetchall()

    logger.info("Изменены задания работы %s (учитель %s)", test_row["share_token"], user["email"])
    return TestCreated(
        id=test_id,
        code=test_row["share_token"],
        class_links=class_links,
        title=payload.title,
        variants_count=payload.variants_count,
        skills_count=len(payload.skills),
        tasks_count=sum(len(variant.tasks) for variant in payload.variants),
    )


# =====================================================================
# Список своих проверочных работ
# =====================================================================

# Отдельный роутер: адрес /api/my/tests не вписывается в префикс /api/tests.
my_router = APIRouter(prefix="/api/my", tags=["tests"])


@my_router.get(
    "/tests",
    response_model=list[MyTestRow],
    summary="Мои проверочные работы",
)
def my_tests(user: dict = Depends(require_user)) -> list[MyTestRow]:
    """
    Проверочные работы текущего учителя, новые сверху.

    Администратор видит все проверочные работы школы: он отвечает за неё целиком
    и должен уметь открыть чужие результаты (например, когда учитель уволился).
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.share_token AS code, t.title, t.subject, t.classes,
                   t.variants_count, t.created_at, t.links_by_class,
                   t.teacher_id, t.teacher_name,
                   -- У работы со ссылками по классам «приём открыт», пока открыт
                   -- хотя бы один класс.
                   CASE WHEN t.links_by_class
                        THEN EXISTS (SELECT 1 FROM test_classes c
                                     WHERE c.test_id = t.id AND c.is_open)
                        ELSE t.is_open END AS is_open,
                   count(a.id) AS attempts_count
            FROM tests t
            LEFT JOIN attempts a ON a.test_id = t.id AND a.finished_at IS NOT NULL
                                AND a.annulled_at IS NULL
            WHERE %s OR t.teacher_id = %s
            GROUP BY t.id
            ORDER BY t.created_at DESC, t.id DESC
            """,
            (user["role"] == "admin", user["id"]),
        ).fetchall()

    return [MyTestRow(**row) for row in rows]
