"""
Создание контрольной учителем.

    POST /api/tests — принимает умения и варианты с заданиями, публикует
                      контрольную и возвращает две ссылки: ученикам и на результаты.

Эндпоинтов, отдающих правильные ответы по ученическому коду, здесь нет:
всё, что видит учитель, живёт на его секретной ссылке (app/routers/results.py).
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, status
from psycopg import errors as pg_errors

from app import db
from app.schemas import TestCreate, TestCreated

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


def generate_results_token() -> str:
    """
    Длинный секрет для ссылки на результаты.

    token_urlsafe(32) — 32 случайных байта (256 бит) в виде строки для адреса.
    Перебрать такую ссылку нельзя, поэтому она и работает вместо пароля,
    пока авторизации учителя нет.
    """
    return secrets.token_urlsafe(32)


def db_unavailable() -> HTTPException:
    """Одинаковый понятный ответ, когда база не отвечает."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="База данных недоступна. Проверьте, что PostgreSQL запущен.",
    )


@router.post(
    "",
    response_model=TestCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Создать и опубликовать контрольную",
)
def create_test(payload: TestCreate) -> TestCreated:
    """
    Сохраняет контрольную целиком в ОДНОЙ транзакции.

    Порядок вставки: контрольная → умения → задания по вариантам → варианты
    ответа. Если что-то пойдёт не так на последнем шаге, откатится всё:
    половинчатых контрольных, где часть вариантов пустая, быть не может.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        logger.error("Нет соединения с базой: %s", exc)
        raise db_unavailable() from exc

    results_token = generate_results_token()
    tasks_count = sum(len(variant.tasks) for variant in payload.variants)

    for attempt in range(CODE_ATTEMPTS):
        code = generate_code()

        try:
            with pool.connection() as conn, conn.transaction():
                # 1. Сама контрольная.
                test_row = conn.execute(
                    """
                    INSERT INTO tests (
                        title, teacher_name, share_token, results_token,
                        classes, shuffle, variants_count, is_published
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                    RETURNING id
                    """,
                    (
                        payload.title,
                        payload.teacher_name,
                        code,
                        results_token,
                        payload.classes,
                        payload.shuffle,
                        payload.variants_count,
                    ),
                ).fetchone()
                test_id = test_row["id"]

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
                "Создана контрольная id=%s код=%s: умений %s, вариантов %s, заданий %s",
                test_id,
                code,
                len(payload.skills),
                payload.variants_count,
                tasks_count,
            )
            return TestCreated(
                id=test_id,
                code=code,
                results_token=results_token,
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
            logger.exception("Не удалось сохранить контрольную")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось сохранить контрольную в базу данных.",
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Не удалось сгенерировать уникальный код ссылки. Попробуйте ещё раз.",
    )
