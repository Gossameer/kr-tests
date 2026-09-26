"""
Роутер контрольных работ.

    POST /api/tests        — учитель публикует контрольную (JSON от ИИ)
    GET  /api/tests/{code} — превью контрольной для учителя по короткому коду
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
    """
    Случайный код для ссылки, например "k7mfp2xq".

    secrets (а не random) — потому что код нельзя угадывать: по нему открывается тест.
    """
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def generate_results_token() -> str:
    """
    Длинный секрет для ссылки на результаты, например "xQ7...".

    token_urlsafe(32) — это 32 случайных байта (256 бит) в виде строки,
    пригодной для адреса. Перебрать такую ссылку нельзя, поэтому она и
    работает вместо пароля, пока авторизации учителя нет.
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
    Принимает JSON с вопросами, сохраняет контрольную и возвращает код ссылки.

    Всё сохранение идёт в ОДНОЙ транзакции: либо в базе появляется целая
    контрольная с вопросами и вариантами, либо не появляется ничего.
    Половинчатых тестов быть не может.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        logger.error("Нет соединения с базой: %s", exc)
        raise db_unavailable() from exc

    # Токен результатов достаточно сгенерировать один раз: повторов у него
    # не бывает, в отличие от короткого кода.
    results_token = generate_results_token()

    for attempt in range(CODE_ATTEMPTS):
        code = generate_code()

        try:
            # `with pool.connection()` берёт соединение из пула,
            # `with conn.transaction()` открывает транзакцию:
            # при выходе без ошибки — COMMIT, при исключении — ROLLBACK.
            with pool.connection() as conn, conn.transaction():
                # 1. Сама контрольная. RETURNING id сразу отдаёт присвоенный базой id.
                test_row = conn.execute(
                    """
                    INSERT INTO tests (
                        title, teacher_name, share_token, results_token,
                        classes, shuffle, is_published
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                    RETURNING id
                    """,
                    (
                        payload.title,
                        payload.teacher_name,
                        code,
                        results_token,
                        payload.classes,   # список Python -> массив TEXT[] в базе
                        payload.shuffle,
                    ),
                ).fetchone()
                test_id = test_row["id"]

                # 2. Вопросы и их варианты. position нумеруем с 1.
                for question_position, question in enumerate(payload.questions, start=1):
                    question_row = conn.execute(
                        """
                        INSERT INTO questions (test_id, text, position)
                        VALUES (%s, %s, %s)
                        RETURNING id
                        """,
                        (test_id, question.text, question_position),
                    ).fetchone()
                    question_id = question_row["id"]

                    # Готовим сразу все варианты одного вопроса и вставляем пачкой.
                    option_rows = [
                        (
                            question_id,
                            option_text,
                            option_index == question.correct,  # True только у правильного
                            option_index + 1,                  # position с 1
                        )
                        for option_index, option_text in enumerate(question.options)
                    ]
                    with conn.cursor() as cur:
                        cur.executemany(
                            """
                            INSERT INTO options (question_id, text, is_correct, position)
                            VALUES (%s, %s, %s, %s)
                            """,
                            option_rows,
                        )

            logger.info("Создана контрольная id=%s, код=%s", test_id, code)
            return TestCreated(
                id=test_id,
                code=code,
                results_token=results_token,
                title=payload.title,
                questions_count=len(payload.questions),
            )

        except pg_errors.UniqueViolation:
            # Такой код уже занят — вероятность крошечная, но обработать надо.
            logger.warning("Код %s занят, генерирую новый (попытка %s)", code, attempt + 1)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось сохранить контрольную")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось сохранить контрольную в базу данных.",
            ) from exc

    # Сюда попадаем, только если все попытки дали занятый код.
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Не удалось сгенерировать уникальный код ссылки. Попробуйте ещё раз.",
    )


# ---------------------------------------------------------------------
# Здесь раньше был GET /api/tests/{code} — «превью для учителя» с полем
# is_correct. Его убрали: ключом служил share_token, то есть тот самый код,
# который есть у КАЖДОГО ученика. Зная свою ссылку, ученик мог запросить этот
# адрес и увидеть все правильные ответы.
#
# Теперь правильные ответы отдаются только по results_token:
#   GET /api/results/{results_token}                       — таблица и сводка
#   GET /api/results/{results_token}/attempts/{attempt_id} — разбор работы
# (см. app/routers/results.py). По ученическому коду их не получить.
# ---------------------------------------------------------------------
