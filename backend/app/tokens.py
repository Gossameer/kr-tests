"""
Одноразовые ссылки: приглашение учителя и сброс пароля.

Правила:
  * токен — secrets.token_urlsafe(32): угадать его нельзя;
  * в базе лежит только SHA-256 от токена. Сам токен существует в письме и
    в ссылке — с копией базы по чужой ссылке не войти;
  * ссылка одноразовая и с коротким сроком: приглашение — 7 дней,
    сброс пароля — 1 час;
  * новая ссылка гасит все прежние того же вида: работает только последняя.

Почему простой SHA-256, а не bcrypt, как у паролей: токен — 256 случайных бит,
перебирать его бессмысленно, а медленный хеш только затормозил бы каждый
переход по ссылке.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

INVITE = "invite"
RESET = "reset"

# Сколько живёт ссылка.
LIFETIME = {
    INVITE: timedelta(days=7),
    RESET: timedelta(hours=1),
}

# Что написать человеку, если ссылка не годится. Про приглашение и про сброс —
# разные слова, но смысл один: что случилось и что делать.
PROBLEM_TEXT = {
    INVITE: {
        "unknown": "Ссылка не подходит. Попросите администратора выслать приглашение заново.",
        "used": (
            "По этой ссылке пароль уже задан. Войдите с ним, а если забыли — "
            "нажмите «Забыли пароль?» на странице входа."
        ),
        "expired": (
            "Срок действия приглашения истёк. "
            "Попросите администратора выслать приглашение заново."
        ),
        "revoked": (
            "Это приглашение заменено более новым — откройте ссылку из последнего письма "
            "или попросите администратора выслать приглашение заново."
        ),
        "disabled": "Учётная запись отключена. Обратитесь к администратору школы.",
    },
    RESET: {
        "unknown": "Ссылка не подходит. Запросите сброс пароля ещё раз на странице входа.",
        "used": "По этой ссылке пароль уже сменили. Если нужно — запросите сброс ещё раз.",
        "expired": (
            "Ссылка для сброса пароля действует один час, и он прошёл. "
            "Запросите сброс ещё раз на странице входа."
        ),
        "revoked": "Эта ссылка заменена более новой — откройте ссылку из последнего письма.",
        "disabled": "Учётная запись отключена. Обратитесь к администратору школы.",
    },
}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(conn, user_id: int, kind: str, created_by: int | None = None) -> tuple[str, datetime]:
    """
    Выдаёт новую ссылку и гасит прежние того же вида.

    Возвращает сам токен (его нужно сразу вставить в письмо или показать
    администратору — прочитать его из базы потом нельзя) и срок действия.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + LIFETIME[kind]

    conn.execute(
        """
        UPDATE auth_tokens SET revoked_at = now()
        WHERE user_id = %s AND kind = %s AND used_at IS NULL AND revoked_at IS NULL
        """,
        (user_id, kind),
    )
    conn.execute(
        """
        INSERT INTO auth_tokens (user_id, kind, token_hash, expires_at, created_by)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, kind, hash_token(token), expires_at, created_by),
    )
    return token, expires_at


def find(conn, token: str, *, lock: bool = False) -> tuple[dict | None, str]:
    """
    Ищет ссылку. Возвращает (строка, проблема).

    Проблема — '' (ссылка годна) или 'unknown' / 'used' / 'revoked' /
    'expired' / 'disabled'. Строка есть всегда, кроме 'unknown': по ней
    вызывающий узнаёт вид ссылки и подбирает слова.

    lock=True блокирует строку до конца транзакции: два одновременных
    перехода по одной ссылке не смогут оба задать пароль.
    """
    row = conn.execute(
        """
        SELECT t.id, t.user_id, t.kind, t.expires_at, t.used_at, t.revoked_at,
               u.full_name, u.email, u.role, u.is_active, u.status,
               u.created_at, u.last_login_at
        FROM auth_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = %s
        """
        + (" FOR UPDATE OF t" if lock else ""),
        (hash_token(token),),
    ).fetchone()

    if row is None:
        return None, "unknown"
    if row["used_at"] is not None:
        return row, "used"
    if row["revoked_at"] is not None:
        return row, "revoked"
    if row["expires_at"] <= datetime.now(timezone.utc):
        return row, "expired"
    if not row["is_active"]:
        return row, "disabled"
    return row, ""


def problem_text(kind: str, problem: str) -> str:
    return PROBLEM_TEXT.get(kind, PROBLEM_TEXT[INVITE])[problem]
