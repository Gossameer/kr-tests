"""
Пароли и сессии.

Правила, которые тут зашиты:
  * пароль хранится только как хеш bcrypt — восстановить его нельзя,
    можно лишь выдать новый;
  * сравнение пароля идёт через bcrypt.checkpw, а не через ==: функция
    сама достаёт соль из хеша и защищена от подбора по времени ответа;
  * токен сессии — 32 случайных байта, его невозможно угадать.
"""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

# Минимальная длина пароля. Требование задаётся в одном месте, чтобы
# регистрация, смена и сброс пароля проверяли одно и то же.
MIN_PASSWORD_LENGTH = 8

# Сколько живёт вход. 30 дней — учитель работает с сервисом изредка,
# каждую неделю вводить пароль заново утомительно.
SESSION_DAYS = 30

# Ограничение подбора пароля.
# По одному email — строже: обычный человек столько раз не ошибается.
MAX_FAILED_LOGINS = 5
# По одному адресу — мягче: из школы все выходят через один внешний IP,
# и общий счётчик не должен запирать целый кабинет информатики.
MAX_FAILED_LOGINS_PER_IP = 30
LOGIN_WINDOW_MINUTES = 10

# bcrypt ограничен 72 байтами: всё, что длиннее, он молча обрежет.
# Обрезаем сами и осознанно, чтобы не было иллюзии «очень длинного пароля».
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """Возвращает хеш пароля в виде строки (её и кладём в базу)."""
    raw = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Проверяет пароль. Любая ошибка разбора хеша считается «не подошёл»."""
    # У приглашённого учителя пароля ещё нет — войти по паролю он не может.
    if not password_hash:
        return False
    try:
        raw = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    """
    Возвращает текст проблемы с паролем или None, если пароль годится.
    Сообщения короткие и без нравоучений — их видит учитель при регистрации.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов."
    if password.strip() == "":
        return "Пароль не может состоять из одних пробелов."
    return None


def new_session_token() -> str:
    """Случайный токен сессии для cookie."""
    return secrets.token_urlsafe(32)


def session_expires_at() -> datetime:
    """Когда истечёт новая сессия."""
    return datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)


def temporary_password() -> str:
    """
    Временный пароль при сбросе.

    Берём буквы и цифры без похожих символов (0/O/1/l/I): пароль придётся
    продиктовать учителю голосом или переписать с экрана.
    """
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def normalize_email(raw: str) -> str:
    """Email как логин: без пробелов и в нижнем регистре."""
    return raw.strip().lower()
