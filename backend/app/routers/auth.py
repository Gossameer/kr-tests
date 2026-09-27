"""
Регистрация, вход и выход.

    POST /api/auth/register — учитель заводит себе учётку по школьному коду
    POST /api/auth/login    — вход по email и паролю
    POST /api/auth/logout   — выход
    GET  /api/auth/me       — кто сейчас вошёл (нужно фронтенду при загрузке)

Администратором через регистрацию стать нельзя: роль здесь всегда 'teacher',
а первого админа создаёт команда `python -m app.create_admin`.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg import errors as pg_errors

from app import db
from app.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    current_user,
    db_unavailable,
    require_user,
    set_session_cookie,
)
from app.schemas import LoginIn, RegisterIn, UserOut
from app.security import (
    LOGIN_WINDOW_MINUTES,
    MAX_FAILED_LOGINS,
    hash_password,
    normalize_email,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else default


def too_many_attempts(conn, email: str) -> bool:
    """
    Не слишком ли часто пробуют этот email.

    Считаем неудачные попытки за последние LOGIN_WINDOW_MINUTES минут.
    Успешный вход журналируется тоже — по нему видно, что подбор удался.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_WINDOW_MINUTES)
    row = conn.execute(
        """
        SELECT count(*) AS n
        FROM login_attempts
        WHERE email = %s AND success = FALSE AND created_at > %s
        """,
        (email, since),
    ).fetchone()
    return row["n"] >= MAX_FAILED_LOGINS


def log_attempt(conn, email: str, success: bool) -> None:
    conn.execute(
        "INSERT INTO login_attempts (email, success) VALUES (%s, %s)",
        (email, success),
    )


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Зарегистрировать учителя по школьному коду",
)
def register(payload: RegisterIn, response: Response) -> UserOut:
    """
    Заводит учётку учителя. Роль всегда 'teacher'.

    Школьный код — единственная защита от посторонних: сервис доступен по сети,
    и без кода зарегистрироваться сможет кто угодно.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    email = normalize_email(payload.email)

    with pool.connection() as conn:
        expected_code = get_setting(conn, "school_code")

        # Сравниваем без учёта регистра и пробелов: код диктуют голосом.
        if payload.school_code.strip().casefold() != expected_code.strip().casefold():
            logger.warning("Регистрация с неверным школьным кодом: %s", email)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Неверный школьный код. Его выдаёт администратор школы.",
            )

        try:
            with conn.transaction():
                user_row = conn.execute(
                    """
                    INSERT INTO users (full_name, email, password_hash, role)
                    VALUES (%s, %s, %s, 'teacher')
                    RETURNING id, full_name, email, role, is_active,
                              created_at, last_login_at
                    """,
                    (payload.full_name, email, hash_password(payload.password)),
                ).fetchone()

                # Сразу входим: заставлять вводить пароль ещё раз незачем.
                token, _ = create_session(conn, user_row["id"])
        except pg_errors.UniqueViolation as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Учётная запись с таким email уже есть. Попробуйте войти.",
            ) from exc

    set_session_cookie(response, token)
    logger.info("Зарегистрирован учитель %s", email)
    return UserOut(**user_row)


@router.post("/login", response_model=UserOut, summary="Вход по email и паролю")
def login(payload: LoginIn, response: Response) -> UserOut:
    """
    Проверяет пароль и создаёт сессию.

    Текст ошибки одинаковый и для неверного пароля, и для несуществующего
    email: иначе по ответу можно было бы собирать список живых адресов.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    email = normalize_email(payload.email)

    with pool.connection() as conn:
        if too_many_attempts(conn, email):
            logger.warning("Слишком много попыток входа: %s", email)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Слишком много неудачных попыток. Подождите "
                    f"{LOGIN_WINDOW_MINUTES} минут и попробуйте снова."
                ),
            )

        user_row = conn.execute(
            """
            SELECT id, full_name, email, password_hash, role, is_active,
                   created_at, last_login_at
            FROM users
            WHERE email = %s
            """,
            (email,),
        ).fetchone()

        if user_row is None or not verify_password(payload.password, user_row["password_hash"]):
            log_attempt(conn, email, success=False)
            # commit обязателен: дальше летит исключение, а выход из блока
            # `with pool.connection()` по исключению откатывает транзакцию —
            # и запись о неудачной попытке пропала бы вместе с ней.
            # Тогда ограничение на подбор пароля просто не работало бы.
            conn.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный email или пароль.",
            )

        if not user_row["is_active"]:
            log_attempt(conn, email, success=False)
            conn.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Учётная запись заблокирована. Обратитесь к администратору.",
            )

        with conn.transaction():
            log_attempt(conn, email, success=True)
            token, _ = create_session(conn, user_row["id"])
            conn.execute(
                "UPDATE users SET last_login_at = now() WHERE id = %s",
                (user_row["id"],),
            )

    set_session_cookie(response, token)
    logger.info("Вход: %s (%s)", email, user_row["role"])
    return UserOut(
        id=user_row["id"],
        full_name=user_row["full_name"],
        email=user_row["email"],
        role=user_row["role"],
        is_active=user_row["is_active"],
        created_at=user_row["created_at"],
        last_login_at=user_row["last_login_at"],
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Выйти")
def logout(request: Request, response: Response) -> Response:
    """Удаляет сессию из базы и стирает cookie."""
    token = request.cookies.get(SESSION_COOKIE)

    if token:
        try:
            pool = db.get_pool()
            with pool.connection() as conn:
                conn.execute("DELETE FROM sessions WHERE token = %s", (token,))
        except Exception:  # noqa: BLE001
            # База недоступна — cookie всё равно снимем, вход на этом устройстве
            # перестанет работать.
            logger.warning("Не удалось удалить сессию из базы при выходе")

    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut | None, summary="Кто сейчас вошёл")
def me(user: dict | None = Depends(current_user)) -> UserOut | None:
    """
    Фронтенд спрашивает это при загрузке страницы.

    Отвечаем null, а не ошибкой: «никто не вошёл» — это нормальное состояние,
    а не сбой.
    """
    return UserOut(**user) if user is not None else None


@router.get("/whoami", response_model=UserOut, summary="Проверка входа (для отладки)")
def whoami(user: dict = Depends(require_user)) -> UserOut:
    """То же самое, но без входа отвечает 401 — удобно проверять права curl-ом."""
    return UserOut(**user)
