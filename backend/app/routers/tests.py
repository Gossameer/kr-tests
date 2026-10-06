"""
Создание проверочной работы и список своих проверочных работ.

    POST /api/tests   — опубликовать проверочную работу (нужен вход)
    GET  /api/my/tests — список проверочных работ текущего учителя

Владелец проверочной работы — вошедший пользователь. ФИО учителя берётся из учётной
записи, а не из формы: так в результатах не появится «Учитель» или опечатка.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import errors as pg_errors

from app import db
from app.auth import require_user
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

                # 2. Умения. Запоминаем их id по порядковому номеру: задания
                #    ссылаются на умение номером (skill_index), а не id.
                skill_ids: dict[int, int] = {}
                for number, skill in enumerate(payload.skills, start=1):
                    skill_row = conn.execute(
                        """
                        INSERT INTO skills (
                            test_id, position, title, tasks_per_variant, answer_format
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            test_id,
                            number,
                            skill.title,
                            skill.tasks_per_variant,
                            skill.answer_format,
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
