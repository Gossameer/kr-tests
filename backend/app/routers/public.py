"""
Публичный роутер — то, что открывает УЧЕНИК по ссылке.

    GET  /api/public/tests/{code}                        — название и классы (экран «Начать»)
    POST /api/public/tests/{code}/start                  — начать: выдать вариант
    POST /api/public/tests/{code}/attempts/{id}/submit   — сдать работу
    POST /api/public/tests/{code}/attempts/{id}/check    — действует ли ещё попытка

Код в ссылке бывает двух видов:
  * код КЛАССА (test_classes.code) — у работы своя ссылка на каждый класс; класс
    задан ссылкой, ученик вводит только ФИО;
  * общий код работы (tests.share_token) — так устроены работы, созданные до
    ссылок по классам: ученик сам выбирает класс из списка.

Защита от пересдачи:
  * один ученик (ФИО без учёта регистра и «ё») — одна попытка в классе;
  * одно устройство — одна сданная работа в рамках ОДНОЙ классовой ссылки.
    Другой класс по своей ссылке на тех же компьютерах проходит работу свободно.
Попытки, которые учитель аннулировал («Разрешить пересдачу»), не считаются.

Главное правило файла: правильные ответы и решения не покидают сервер.
Поэтому задания ученику отдаются без accepted_answers, без solution и без
признака правильности у вариантов ответа. Проверка — только здесь.
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Path, status
from psycopg import errors as pg_errors

from app import db
from app.answers import check_input_answer
from app.names import name_key
from app.schemas import (
    AttemptCheckIn,
    AttemptResultOut,
    PublicTestInfo,
    StartAttemptIn,
    StartAttemptOut,
    SubmitAttemptIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public/tests", tags=["public"])


def db_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Сервис временно недоступен. Подождите минуту и откройте ссылку ещё раз.",
    )


def test_not_found(code: str) -> HTTPException:
    """404 и для «нет такого кода», и для неопубликованной проверочной работы."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"Проверочная работа по ссылке «{code}» не найдена или ещё не опубликована. "
            "Проверьте ссылку у учителя."
        ),
    )


def closed() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Приём работ закрыт — учитель больше не принимает ответы.",
    )


TEST_COLUMNS = "id, title, teacher_name, classes, shuffle, is_open, variants_count, links_by_class"


def load_published_test(conn, code: str) -> tuple[dict, dict | None]:
    """
    По коду из ссылки находит опубликованную работу и (если это ссылка класса)
    сам класс. Не нашлось — 404.

    Закрытый приём здесь НЕ отсекается: ученику нужно показать сообщение
    «приём работ закрыт», а не «страница не найдена».
    """
    class_row = conn.execute(
        "SELECT id, test_id, class_name, is_open FROM test_classes WHERE code = %s",
        (code,),
    ).fetchone()

    if class_row is not None:
        test_row = conn.execute(
            f"SELECT {TEST_COLUMNS} FROM tests WHERE id = %s AND is_published = TRUE",
            (class_row["test_id"],),
        ).fetchone()
        if test_row is None:
            raise test_not_found(code)
        return test_row, class_row

    test_row = conn.execute(
        f"SELECT {TEST_COLUMNS} FROM tests WHERE share_token = %s AND is_published = TRUE",
        (code,),
    ).fetchone()
    if test_row is None:
        raise test_not_found(code)

    if test_row["links_by_class"]:
        # У новых работ общей ссылки нет: иначе защита «одно устройство — одна
        # попытка по ссылке класса» обходилась бы выбором класса из списка.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "У этой работы для каждого класса своя ссылка. "
                "Попросите у учителя ссылку для вашего класса."
            ),
        )
    return test_row, None


def link_is_open(test_row: dict, class_row: dict | None) -> bool:
    """Открыт ли приём по этой ссылке: у класса свой переключатель."""
    return class_row["is_open"] if class_row is not None else test_row["is_open"]


def already_done() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Вы уже сдали эту работу. Если нужно пройти её заново, "
            "попросите учителя разрешить пересдачу."
        ),
    )


def device_used(student_name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"С этого устройства работу по этой ссылке уже сдали ({student_name}). "
            "Второй раз пройти её здесь нельзя. Если это ошибка, попросите учителя "
            "разрешить пересдачу."
        ),
    )


def device_attempt(conn, class_id: int, device_id: str, except_id: int = 0) -> dict | None:
    """Сданная (и не аннулированная) работа с этого устройства по этой ссылке класса."""
    if not device_id:
        return None
    return conn.execute(
        """
        SELECT id, student_name FROM attempts
        WHERE class_id = %s AND device_id = %s AND id <> %s
          AND finished_at IS NOT NULL AND annulled_at IS NULL
        ORDER BY id LIMIT 1
        """,
        (class_id, device_id, except_id),
    ).fetchone()


def load_variant_tasks(conn, test_id: int, variant_no: int) -> list[dict]:
    """
    Задания одного варианта в виде, пригодном для ученика.

    В SELECT специально нет accepted_answers и solution: их неоткуда будет
    случайно подставить в ответ.
    """
    task_rows = conn.execute(
        """
        SELECT id, position, text, answer_format
        FROM tasks
        WHERE test_id = %s AND variant_no = %s
        ORDER BY position, id
        """,
        (test_id, variant_no),
    ).fetchall()

    task_ids = [row["id"] for row in task_rows]
    option_rows: list[dict] = []
    if task_ids:
        option_rows = conn.execute(
            """
            SELECT id, task_id, text
            FROM task_options
            WHERE task_id = ANY(%s)
            ORDER BY position, id
            """,
            (task_ids,),
        ).fetchall()

    options_by_task: dict[int, list[dict]] = {}
    for option in option_rows:
        options_by_task.setdefault(option["task_id"], []).append(option)

    return [
        {
            "id": task["id"],
            "position": task["position"],
            "text": task["text"],
            "answer_format": task["answer_format"],
            "options": options_by_task.get(task["id"], []),
        }
        for task in task_rows
    ]


@router.get(
    "/{code}",
    response_model=PublicTestInfo,
    summary="Название и классы проверочной работы (экран «Начать»)",
)
def get_public_test(
    code: str = Path(min_length=4, max_length=32, description="Код из ссылки"),
) -> PublicTestInfo:
    """
    Отдаёт только шапку. Задания приходят позже, вместе с выданным вариантом:
    до нажатия «Начать» ученику незачем видеть тексты заданий.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row, class_row = load_published_test(conn, code)

        # Сколько заданий в варианте — считаем по первому варианту:
        # во всех вариантах их поровну, это проверяется при создании.
        tasks_count = conn.execute(
            "SELECT count(*) AS n FROM tasks WHERE test_id = %s AND variant_no = 1",
            (test_row["id"],),
        ).fetchone()["n"]

    return PublicTestInfo(
        code=code,
        title=test_row["title"],
        teacher_name=test_row["teacher_name"],
        # По ссылке класса выбирать нечего: класс один, и он задан ссылкой.
        classes=[class_row["class_name"]] if class_row is not None else test_row["classes"],
        class_name=class_row["class_name"] if class_row is not None else None,
        is_open=link_is_open(test_row, class_row),
        tasks_count=tasks_count,
    )


@router.post(
    "/{code}/start",
    response_model=StartAttemptOut,
    status_code=status.HTTP_201_CREATED,
    summary="Начать работу: получить вариант и задания",
)
def start_attempt(
    payload: StartAttemptIn,
    code: str = Path(min_length=4, max_length=32),
) -> StartAttemptOut:
    """
    Создаёт попытку и выдаёт вариант ПО ОЧЕРЕДИ: 1, 2, ..., N, снова 1.

    Очередь своя у каждого класса (ссылки по классам) — в одном классе соседям
    достаются разные варианты, даже если до них работу писал другой класс.

    Как сделано без гонок: номер берётся из счётчика в строке класса (или самой
    работы — для старой общей ссылки) командой UPDATE ... RETURNING. Она блокирует
    строку, поэтому два ученика, нажавшие «Начать» в один и тот же момент, получат
    разные номера. Подсчёт «сколько уже попыток» такой гарантии не даёт.

    Если этот ученик уже начинал работу, новую попытку не создаём:
      * работа не сдана  → продолжаем ту же, с тем же вариантом;
      * работа сдана     → 409, пересдача только через учителя.
    С устройства, с которого по этой ссылке класса уже сдали работу, начать
    новую нельзя (см. описание файла).
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row, class_row = load_published_test(conn, code)
        test_id = test_row["id"]
        class_id = class_row["id"] if class_row is not None else None

        if not link_is_open(test_row, class_row):
            raise closed()

        if class_row is not None:
            # Класс задан ссылкой — что бы ни прислал браузер.
            student_class = class_row["class_name"]
        else:
            student_class = payload.student_class
            allowed_classes: list[str] = test_row["classes"]
            if not student_class:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Выберите свой класс из списка.",
                )
            if allowed_classes and student_class not in allowed_classes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Класса «{student_class}» нет в списке этой работы. "
                        f"Выберите свой класс из списка: {', '.join(allowed_classes)}."
                    ),
                )

        key = name_key(payload.student_name)
        device_id = payload.device_id if class_id is not None else ""

        def find_own():
            return conn.execute(
                """
                SELECT id, attempt_token, variant_no, finished_at, student_name
                FROM attempts
                WHERE test_id = %s AND student_class = %s AND name_key = %s
                  AND annulled_at IS NULL
                """,
                (test_id, student_class, key),
            ).fetchone()

        # --- Ученик уже начинал? ------------------------------------------
        existing = find_own()

        if existing is not None:
            if existing["finished_at"] is not None:
                raise already_done()

            logger.info(
                "Продолжение работы: тест=%s, ученик=%s (%s), вариант %s",
                code,
                existing["student_name"],
                student_class,
                existing["variant_no"],
            )
            return StartAttemptOut(
                attempt_id=existing["id"],
                attempt_token=existing["attempt_token"],
                variant_no=existing["variant_no"],
                student_name=existing["student_name"],
                student_class=student_class,
                title=test_row["title"],
                shuffle=test_row["shuffle"],
                tasks=load_variant_tasks(conn, test_id, existing["variant_no"]),
            )

        # --- Новая попытка -------------------------------------------------
        try:
            with conn.transaction():
                if class_id is not None:
                    counter_row = conn.execute(
                        """
                        UPDATE test_classes
                        SET variant_counter = variant_counter + 1
                        WHERE id = %s
                        RETURNING variant_counter
                        """,
                        (class_id,),
                    ).fetchone()
                    # Строка класса теперь заблокирована до конца транзакции, так что
                    # проверка устройства и вставка попытки идут строго по одному.
                    other = device_attempt(conn, class_id, device_id)
                    if other is not None:
                        raise device_used(other["student_name"])
                else:
                    counter_row = conn.execute(
                        """
                        UPDATE tests
                        SET variant_counter = variant_counter + 1
                        WHERE id = %s
                        RETURNING variant_counter
                        """,
                        (test_id,),
                    ).fetchone()

                variants_count = max(1, test_row["variants_count"])
                # Счётчик растёт вверх, номер варианта ходит по кругу.
                variant_no = ((counter_row["variant_counter"] - 1) % variants_count) + 1

                tasks = load_variant_tasks(conn, test_id, variant_no)
                attempt_token = secrets.token_urlsafe(24)

                attempt_row = conn.execute(
                    """
                    INSERT INTO attempts (
                        test_id, student_name, student_class, name_key,
                        class_id, device_id,
                        variant_no, attempt_token, max_score
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        test_id,
                        payload.student_name,
                        student_class,
                        key,
                        class_id,
                        device_id,
                        variant_no,
                        attempt_token,
                        len(tasks),
                    ),
                ).fetchone()

        except pg_errors.UniqueViolation:
            # Тот же ученик нажал «Начать» дважды почти одновременно:
            # первая попытка уже создана — отдаём её.
            again = find_own()

            if again is None:
                raise
            if again["finished_at"] is not None:
                raise already_done() from None

            return StartAttemptOut(
                attempt_id=again["id"],
                attempt_token=again["attempt_token"],
                variant_no=again["variant_no"],
                student_name=again["student_name"],
                student_class=student_class,
                title=test_row["title"],
                shuffle=test_row["shuffle"],
                tasks=load_variant_tasks(conn, test_id, again["variant_no"]),
            )

    logger.info(
        "Начата работа: тест=%s, ученик=%s (%s), выдан вариант %s",
        code,
        payload.student_name,
        student_class,
        variant_no,
    )
    return StartAttemptOut(
        attempt_id=attempt_row["id"],
        attempt_token=attempt_token,
        variant_no=variant_no,
        student_name=payload.student_name,
        student_class=student_class,
        title=test_row["title"],
        shuffle=test_row["shuffle"],
        tasks=tasks,
    )


@router.post(
    "/{code}/attempts/{attempt_id}/submit",
    response_model=AttemptResultOut,
    summary="Сдать работу и получить результат",
)
def submit_attempt(
    payload: SubmitAttemptIn,
    code: str = Path(min_length=4, max_length=32),
    attempt_id: int = Path(ge=1),
) -> AttemptResultOut:
    """
    Принимает ответы, проверяет их НА СЕРВЕРЕ и сохраняет работу.

    Браузер присылает только выбранные варианты и введённый текст.
    Правильность считается по данным базы, поэтому подделать оценку нельзя.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        test_row, class_row = load_published_test(conn, code)
        test_id = test_row["id"]

        if not link_is_open(test_row, class_row):
            raise closed()

        # Токен попытки обязателен: по одному лишь id сдать работу нельзя.
        # Попытка должна быть начата по ЭТОЙ ЖЕ ссылке: иначе закрытый приём
        # класса обходился бы сдачей через ссылку другого класса.
        attempt_row = conn.execute(
            """
            SELECT id, student_name, student_class, variant_no, finished_at,
                   annulled_at, class_id, device_id
            FROM attempts
            WHERE id = %s AND test_id = %s AND attempt_token = %s
              AND class_id IS NOT DISTINCT FROM %s
            """,
            (
                attempt_id,
                test_id,
                payload.attempt_token,
                class_row["id"] if class_row is not None else None,
            ),
        ).fetchone()

        if attempt_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Работа не найдена. Откройте ссылку заново и начните сначала.",
            )

        if attempt_row["annulled_at"] is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Учитель аннулировал эту попытку. Обновите страницу и начните работу заново."
                ),
            )

        if attempt_row["finished_at"] is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Эта работа уже сдана.",
            )

        # Двое начали работу на одном устройстве в разных вкладках — сдаст только один.
        if attempt_row["class_id"] is not None:
            other = device_attempt(
                conn, attempt_row["class_id"], attempt_row["device_id"], except_id=attempt_id
            )
            if other is not None:
                raise device_used(other["student_name"])

        variant_no = attempt_row["variant_no"]

        # Задания варианта — здесь уже с правильными ответами: они нужны
        # для проверки и остаются внутри функции.
        task_rows = conn.execute(
            """
            SELECT id, position, answer_format, accepted_answers
            FROM tasks
            WHERE test_id = %s AND variant_no = %s
            ORDER BY position, id
            """,
            (test_id, variant_no),
        ).fetchall()

        if not task_rows:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="В этом варианте нет заданий — обратитесь к учителю.",
            )

        task_ids = [row["id"] for row in task_rows]
        option_rows = conn.execute(
            """
            SELECT id, task_id, is_correct
            FROM task_options
            WHERE task_id = ANY(%s)
            """,
            (task_ids,),
        ).fetchall()

        task_of_option = {row["id"]: row["task_id"] for row in option_rows}
        correct_options = {row["id"] for row in option_rows if row["is_correct"]}
        allowed_tasks = set(task_ids)

        # --- Проверяем, что присланное относится к этому варианту -----------
        for task_id in list(payload.choices) + list(payload.inputs):
            if task_id not in allowed_tasks:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Задание с id={task_id} не входит в ваш вариант. "
                        "Обновите страницу и попробуйте снова."
                    ),
                )

        for task_id, option_id in payload.choices.items():
            if task_of_option.get(option_id) != task_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Вариант ответа с id={option_id} не относится к заданию "
                        f"с id={task_id}. Обновите страницу и попробуйте снова."
                    ),
                )

        # --- Проверка ответов ------------------------------------------------
        score = 0
        results = []
        answer_rows: list[tuple[int, int | None, str | None, bool]] = []

        for task in task_rows:
            task_id = task["id"]
            chosen_option: int | None = None
            typed_text: str | None = None
            is_correct = False

            if task["answer_format"] == "choice":
                chosen_option = payload.choices.get(task_id)
                is_correct = chosen_option in correct_options
                answered = chosen_option is not None
            else:
                raw = payload.inputs.get(task_id)
                typed_text = raw.strip() if raw is not None else None
                answered = bool(typed_text)
                if answered and typed_text is not None:
                    is_correct = check_input_answer(typed_text, task["accepted_answers"])

            if is_correct:
                score += 1

            results.append(
                {
                    "task_id": task_id,
                    "position": task["position"],
                    "answered": answered,
                    "is_correct": is_correct,
                }
            )
            answer_rows.append((task_id, chosen_option, typed_text, is_correct))

        # --- Сохранение в одной транзакции ------------------------------------
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO answers
                            (attempt_id, task_id, option_id, answer_text, is_correct)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            (attempt_id, task_id, option_id, text, correct)
                            for task_id, option_id, text, correct in answer_rows
                        ],
                    )

                conn.execute(
                    """
                    UPDATE attempts
                    SET score = %s, max_score = %s, finished_at = now()
                    WHERE id = %s
                    """,
                    (score, len(task_rows), attempt_id),
                )
        except pg_errors.UniqueViolation as exc:
            # Ответы за эту попытку уже записаны — работу успели сдать.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Эта работа уже сдана.",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Не удалось сохранить работу ученика")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось сохранить работу. Попробуйте сдать ещё раз.",
            ) from exc

    logger.info(
        "Сдана работа: тест=%s, ученик=%s (%s), вариант %s, балл %s/%s",
        code,
        attempt_row["student_name"],
        attempt_row["student_class"],
        variant_no,
        score,
        len(task_rows),
    )

    return AttemptResultOut(
        attempt_id=attempt_id,
        variant_no=variant_no,
        student_name=attempt_row["student_name"],
        student_class=attempt_row["student_class"],
        score=score,
        max_score=len(task_rows),
        results=results,
    )


@router.post(
    "/{code}/attempts/{attempt_id}/check",
    summary="Действует ли ещё сохранённая в браузере попытка",
)
def check_attempt(
    payload: AttemptCheckIn,
    code: str = Path(min_length=4, max_length=32),
    attempt_id: int = Path(ge=1),
) -> dict:
    """
    Браузер ученика помнит сданную работу и при повторном открытии ссылки сразу
    показывает результат. Если учитель разрешил пересдачу (или удалил попытку),
    эта память устарела: страница спрашивает здесь и, получив «gone», забывает
    старый результат и показывает экран «Начать».
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT annulled_at FROM attempts WHERE id = %s AND attempt_token = %s",
            (attempt_id, payload.attempt_token),
        ).fetchone()

    return {"state": "active" if row is not None and row["annulled_at"] is None else "gone"}
