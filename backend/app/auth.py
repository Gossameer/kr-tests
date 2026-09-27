"""
Кто сейчас работает с сервисом: чтение сессии и проверка прав.

Сессия живёт в httpOnly-cookie: JavaScript на странице её не видит, поэтому
даже найденная где-то уязвимость с чужим скриптом не утащит вход.

Три зависимости для роутеров:
    current_user     — пользователь или None (для «мягких» мест);
    require_user     — вход обязателен, иначе 401;
    require_admin    — нужен администратор, иначе 403.
"""

import logging
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response, status

from app import db
from app.config import get_settings
from app.security import session_expires_at, new_session_token

logger = logging.getLogger(__name__)

# Имя cookie с токеном сессии.
SESSION_COOKIE = "kr_session"


def db_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="База данных недоступна. Попробуйте позже.",
    )


def not_authenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Нужно войти в систему.",
    )


def forbidden(detail: str = "Недостаточно прав.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def create_session(conn, user_id: int) -> tuple[str, datetime]:
    """Создаёт сессию и возвращает токен с датой истечения."""
    token = new_session_token()
    expires = session_expires_at()
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
        (token, user_id, expires),
    )
    return token, expires


def set_session_cookie(response: Response, token: str) -> None:
    """
    Кладёт токен в cookie.

    httponly — cookie не видна из JavaScript;
    samesite="lax" — cookie не уходит на чужие сайты, но обычные переходы работают;
    secure — берётся из настройки COOKIE_SECURE: на сервере за https она true,
    локально false (иначе браузер не сохранит cookie на http, и вход не сработает).
    """
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def load_user_by_session(conn, token: str) -> dict | None:
    """
    Находит пользователя по токену сессии.

    Заодно отсекает истёкшие сессии и заблокированных учителей: блокировка
    начинает действовать сразу, а не после того, как человек выйдет сам.
    """
    row = conn.execute(
        """
        SELECT u.id, u.full_name, u.email, u.role, u.is_active,
               u.created_at, u.last_login_at, s.expires_at
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = %s
        """,
        (token,),
    ).fetchone()

    if row is None:
        return None

    if row["expires_at"] <= datetime.now(timezone.utc):
        conn.execute("DELETE FROM sessions WHERE token = %s", (token,))
        return None

    if not row["is_active"]:
        return None

    return row


def current_user(request: Request) -> dict | None:
    """Пользователь текущего запроса или None. Ошибку не бросает."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    try:
        pool = db.get_pool()
    except Exception:  # noqa: BLE001
        raise db_unavailable() from None

    with pool.connection() as conn:
        return load_user_by_session(conn, token)


def require_user(user: dict | None = Depends(current_user)) -> dict:
    """Вход обязателен."""
    if user is None:
        raise not_authenticated()
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    """Нужны права администратора."""
    if user["role"] != "admin":
        raise forbidden("Этот раздел доступен только администратору.")
    return user


def client_ip(request: Request) -> str:
    """
    Настоящий адрес клиента.

    За обратным прокси request.client — это сам прокси, а адрес посетителя
    приходит первым значением в X-Forwarded-For. Верить заголовку можно только
    когда сервис действительно закрыт прокси: иначе кто угодно подставит себе
    чужой адрес и обойдёт ограничение на подбор пароля. Поэтому чтение заголовка
    включается настройкой TRUST_PROXY.
    """
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # Формат: «клиент, прокси1, прокси2» — нужен первый адрес.
            return forwarded.split(",")[0].strip()[:64]

    return request.client.host if request.client else ""


def can_manage_test(user: dict, test_row: dict) -> bool:
    """
    Может ли пользователь смотреть и менять эту контрольную.

    Владелец — да. Администратор — да (он отвечает за школу целиком).
    Остальные учителя — нет, даже если знают адрес.
    """
    return user["role"] == "admin" or test_row["teacher_id"] == user["id"]
