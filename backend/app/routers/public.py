"""
Публичный роутер — то, что открывает УЧЕНИК по ссылке.

    GET  /api/public/tests/{code}          — получить тест (без правильных ответов)
    POST /api/public/tests/{code}/attempts — сдать работу и узнать результат

Главное правило этого файла: правильные ответы не должны покидать сервер.
Поэтому is_correct читается только внутри функций и никогда не попадает
в ответ клиенту. Проверка работы тоже целиком серверная: даже если ученик
подделает запрос, балл посчитается по данным из базы.
"""

import logging

from fastapi import APIRouter, HTTPException, Path, status

from app import db
from app.schemas import AttemptCreate, AttemptResult, PublicTestOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public/tests", tags=["public"])


def db_unavailable() -> HTTPException:
    """Одинаковый понятный ответ, когда база не отвечает."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="База данных недоступна. Попробуйте открыть ссылку чуть позже.",
    )


def test_not_found(code: str) -> HTTPException:
    """
    404 для случаев «нет такого кода» и «тест не опубликован».

    Текст одинаковый: ученику всё равно, какая из причин, а учителю подсказку
    даёт его собственный экран публикации.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"Контрольная по ссылке «{code}» не найдена или ещё не опубликована. "
            "Проверьте ссылку у учителя."
        ),
    )


def load_published_test(conn, code: str) -> dict:
    """Находит опубликованную контрольную по коду или бросает 404."""
    row = conn.execute(
        """
        SELECT id, title, teacher_name
        FROM tests
        WHERE share_token = %s AND is_published = TRUE
        """,
        (code,),
    ).fetchone()

    if row is None:
        raise test_not_found(code)

    return row


@router.get(
    "/{code}",
    response_model=PublicTestOut,
    summary="Тест для ученика (без правильных ответов)",
)
def get_public_test(
    code: str = Path(min_length=4, max_length=32, description="Код из ссылки"),
) -> PublicTestOut:
    """
    Отдаёт название и вопросы с вариантами в том же порядке, что задал учитель.

    В SELECT по options СПЕЦИАЛЬНО не берётся is_correct — так признак
    правильного ответа физически не может утечь в ответ.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_published_test(conn, code)

        question_rows = conn.execute(
            """
            SELECT id, text, position
            FROM questions
            WHERE test_id = %s
            ORDER BY position, id
            """,
            (test_row["id"],),
        ).fetchall()

        question_ids = [row["id"] for row in question_rows]
        option_rows: list[dict] = []
        if question_ids:
            option_rows = conn.execute(
                """
                SELECT id, question_id, text
                FROM options
                WHERE question_id = ANY(%s)
                ORDER BY position, id
                """,
                (question_ids,),
            ).fetchall()

    # Раскладываем варианты по вопросам.
    options_by_question: dict[int, list[dict]] = {}
    for option in option_rows:
        options_by_question.setdefault(option["question_id"], []).append(option)

    return PublicTestOut(
        code=code,
        title=test_row["title"],
        teacher_name=test_row["teacher_name"],
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


@router.post(
    "/{code}/attempts",
    response_model=AttemptResult,
    status_code=status.HTTP_201_CREATED,
    summary="Сдать работу и получить результат",
)
def submit_attempt(
    payload: AttemptCreate,
    code: str = Path(min_length=4, max_length=32, description="Код из ссылки"),
) -> AttemptResult:
    """
    Принимает ответы ученика, считает балл на сервере и сохраняет попытку.

    Порядок действий:
      1. находим опубликованный тест по коду;
      2. читаем из базы вопросы и варианты вместе с признаком правильности;
      3. проверяем, что присланные вопросы и варианты относятся К ЭТОМУ тесту;
      4. считаем балл по данным базы (присланному «верно/неверно» не верим —
         его нам и не присылают);
      5. одной транзакцией пишем попытку и ответы;
      6. возвращаем результат без указания правильных вариантов.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row = load_published_test(conn, code)
        test_id = test_row["id"]

        question_rows = conn.execute(
            """
            SELECT id, position
            FROM questions
            WHERE test_id = %s
            ORDER BY position, id
            """,
            (test_id,),
        ).fetchall()

        if not question_rows:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="В этой контрольной нет вопросов — обратитесь к учителю.",
            )

        question_ids = [row["id"] for row in question_rows]

        # Здесь is_correct нужен для подсчёта — но он останется внутри функции.
        option_rows = conn.execute(
            """
            SELECT id, question_id, is_correct
            FROM options
            WHERE question_id = ANY(%s)
            """,
            (question_ids,),
        ).fetchall()

        # Справочники для быстрой проверки: вариант -> вопрос, вариант -> верный ли.
        question_of_option = {row["id"]: row["question_id"] for row in option_rows}
        correct_options = {row["id"] for row in option_rows if row["is_correct"]}
        allowed_questions = set(question_ids)

        # --- Проверка присланных ответов ---------------------------------
        for question_id, option_id in payload.answers.items():
            if question_id not in allowed_questions:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Вопрос с id={question_id} не относится к этой контрольной. "
                        "Обновите страницу и попробуйте снова."
                    ),
                )
            if question_of_option.get(option_id) != question_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Вариант с id={option_id} не относится к вопросу "
                        f"с id={question_id}. Обновите страницу и попробуйте снова."
                    ),
                )

        # --- Подсчёт балла ------------------------------------------------
        # max_score — по одному баллу за вопрос.
        max_score = len(question_rows)
        score = 0
        results = []
        # Строки для таблицы answers: пишем ВСЕ вопросы, у пропущенных option_id = NULL.
        answer_rows: list[tuple[int, int, int | None]] = []

        for question in question_rows:
            question_id = question["id"]
            chosen_option = payload.answers.get(question_id)  # None = вопрос пропущен
            is_correct = chosen_option in correct_options

            if is_correct:
                score += 1

            results.append(
                {
                    "question_id": question_id,
                    "position": question["position"],
                    "answered": chosen_option is not None,
                    "is_correct": is_correct,
                }
            )
            # attempt_id подставим ниже, когда попытка получит id.
            answer_rows.append((question_id, chosen_option))  # type: ignore[arg-type]

        # --- Сохранение в одной транзакции --------------------------------
        try:
            with conn.transaction():
                attempt_row = conn.execute(
                    """
                    INSERT INTO attempts (
                        test_id, student_name, student_class,
                        score, max_score, finished_at
                    )
                    VALUES (%s, %s, %s, %s, %s, now())
                    RETURNING id
                    """,
                    (
                        test_id,
                        payload.student_name,
                        payload.student_class,
                        score,
                        max_score,
                    ),
                ).fetchone()
                attempt_id = attempt_row["id"]

                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO answers (attempt_id, question_id, option_id)
                        VALUES (%s, %s, %s)
                        """,
                        [
                            (attempt_id, question_id, option_id)
                            for question_id, option_id in answer_rows
                        ],
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось сохранить работу ученика")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось сохранить работу. Попробуйте сдать ещё раз.",
            ) from exc

    logger.info(
        "Сдана работа: тест=%s, ученик=%s (%s), балл=%s/%s",
        code,
        payload.student_name,
        payload.student_class,
        score,
        max_score,
    )

    return AttemptResult(
        attempt_id=attempt_id,
        student_name=payload.student_name,
        student_class=payload.student_class,
        score=score,
        max_score=max_score,
        results=results,
    )
