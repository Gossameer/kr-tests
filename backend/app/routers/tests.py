"""
Роутер контрольных работ.

    POST /api/tests        — учитель публикует контрольную (JSON от ИИ)
    GET  /api/tests/{code} — превью контрольной для учителя по короткому коду
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Path, status
from psycopg import errors as pg_errors

from app import db
from app.schemas import TestCreate, TestCreated, TestOut

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
                    INSERT INTO tests (title, teacher_name, share_token, is_published)
                    VALUES (%s, %s, %s, TRUE)
                    RETURNING id
                    """,
                    (payload.title, payload.teacher_name, code),
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


@router.get(
    "/{code}",
    response_model=TestOut,
    summary="Получить контрольную по коду (превью для учителя)",
)
def get_test(
    code: str = Path(
        min_length=4,
        max_length=32,
        description="Короткий код из ссылки, например k7mfp2xq",
    ),
) -> TestOut:
    """
    Отдаёт контрольную целиком, ВКЛЮЧАЯ признак правильного ответа.

    Это превью для учителя. Для страницы ученика понадобится отдельный
    эндпоинт без is_correct — иначе правильные ответы окажутся в браузере ученика.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        # 1. Сама контрольная по коду.
        test_row = conn.execute(
            """
            SELECT id, title, teacher_name, share_token, is_published, created_at
            FROM tests
            WHERE share_token = %s
            """,
            (code,),
        ).fetchone()

        if test_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Контрольная с кодом «{code}» не найдена.",
            )

        # 2. Все вопросы этой контрольной по порядку.
        question_rows = conn.execute(
            """
            SELECT id, text, position
            FROM questions
            WHERE test_id = %s
            ORDER BY position, id
            """,
            (test_row["id"],),
        ).fetchall()

        # 3. Все варианты сразу для всех вопросов — одним запросом,
        #    чтобы не дёргать базу отдельно на каждый вопрос.
        question_ids = [row["id"] for row in question_rows]
        option_rows: list[dict] = []
        if question_ids:
            option_rows = conn.execute(
                """
                SELECT id, question_id, text, is_correct, position
                FROM options
                WHERE question_id = ANY(%s)
                ORDER BY position, id
                """,
                (question_ids,),
            ).fetchall()

    # Раскладываем варианты по вопросам: {id вопроса: [варианты]}
    options_by_question: dict[int, list[dict]] = {}
    for option in option_rows:
        options_by_question.setdefault(option["question_id"], []).append(option)

    return TestOut(
        id=test_row["id"],
        code=test_row["share_token"],
        title=test_row["title"],
        teacher_name=test_row["teacher_name"],
        is_published=test_row["is_published"],
        created_at=test_row["created_at"],
        questions=[
            {
                "id": question["id"],
                "text": question["text"],
                "position": question["position"],
                "options": options_by_question.get(question["id"], []),
            }
            for question in question_rows
        ],
    )
