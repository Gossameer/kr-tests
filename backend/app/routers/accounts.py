"""
Приглашения учителей, установка пароля по ссылке и «забыли пароль».

Для всех (без входа):
    GET  /api/auth/options          — можно ли регистрироваться самому
    POST /api/auth/forgot           — «забыли пароль?»: письмо со ссылкой
    GET  /api/auth/tokens/{token}   — чья это ссылка и годна ли она
    POST /api/auth/tokens/{token}   — задать пароль по ссылке и сразу войти

Для администратора:
    POST /api/admin/teachers/import/preview — проверить вставленный список
    POST /api/admin/teachers/import         — создать учётки и приглашения
    POST /api/admin/teachers/{id}/invite    — новое приглашение (письмом или ссылкой)
    POST /api/admin/teachers/invite-pending — ещё раз всем, кто не активировал
    GET  /api/admin/mail                    — настроена ли почта и журнал писем

Токены — в app/tokens.py, письма — в app/mailer.py.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from psycopg import errors as pg_errors
from pydantic import BaseModel, Field, field_validator

from app import db, mailer, tokens
from app.auth import client_ip, create_session, db_unavailable, require_admin, set_session_cookie
from app.config import get_settings
from app.names import normalize_full_name
from app.schemas import MAX_TITLE_LEN, UserOut
from app.security import hash_password, normalize_email, password_problem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

# Сколько строк принимаем за один раз: школа — это десятки учителей, не тысячи.
MAX_IMPORT_LINES = 300
MAX_IMPORT_CHARS = 60_000

# «Забыли пароль?»: не больше стольких запросов в час.
RESET_WINDOW = timedelta(hours=1)
MAX_RESETS_PER_EMAIL = 3
MAX_RESETS_PER_IP = 10

# Один и тот же ответ на любой адрес: по нему нельзя узнать, есть ли учётка.
FORGOT_MESSAGE = (
    "Если такой адрес есть в сервисе, мы отправили на него письмо со ссылкой. "
    "Проверьте почту — и папку «Спам» тоже. Ссылка действует один час."
)

# Простая проверка адреса: что-то@что-то.зона, без пробелов. Письма уходят
# на этот адрес, поэтому явную ерунду лучше поймать до создания учётки.
EMAIL_RE = re.compile(r"^[^@\s,;<>()\[\]\\\"]+@[^@\s,;<>()\[\]\\\"]+\.[^@\s,;<>()\[\]\\\".]{2,}$")


def migration_missing() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Приглашения пока не подключены на сервере. Сообщите администратору.",
    )


def get_pool():
    try:
        return db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc


def self_registration_allowed(conn) -> bool:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'allow_self_registration'"
    ).fetchone()
    return row is not None and row["value"] == "true"


# =====================================================================
# Разбор списка учителей
# =====================================================================


def parse_teacher_list(text: str) -> list[dict]:
    """
    Разбирает вставленный список: строка = «ФИО <таб | ; | ,> email».

    Так вставляются два столбца из Excel. Порядок столбцов не важен: почтой
    считаем ту часть, где есть «@». Пустые строки пропускаем.

    Статусы строк: 'new' | 'duplicate' (повтор внутри списка) | 'invalid'.
    Про 'exists' (такой email уже в базе) знает только вызывающий.
    """
    rows: list[dict] = []
    seen: dict[str, int] = {}

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts: list[str] = []
        for part in re.split(r"[\t;,]", line):
            part = part.strip()
            if not part:
                continue
            # «Иванова Анна ivanova@school.ru» — без разделителя, только пробел:
            # отделяем адрес от имени сами.
            if "@" in part and " " in part:
                parts.extend(part.split())
            else:
                parts.append(part)
        email_parts = [part for part in parts if "@" in part]
        # Excel иногда оборачивает адрес: «<ivanova@school.ru>» или mailto:.
        email = normalize_email(email_parts[-1]) if email_parts else ""
        email = email.removeprefix("mailto:").strip("<>\"' ")
        full_name = normalize_full_name(" ".join(part for part in parts if "@" not in part))

        row = {"line": number, "full_name": full_name, "email": email, "status": "new", "message": ""}

        if not email_parts:
            row["status"] = "invalid"
            row["message"] = "нет email — допишите адрес после ФИО"
            row["email"] = ""
        elif len(email_parts) > 1:
            row["status"] = "invalid"
            row["message"] = "в строке несколько адресов — оставьте один"
        elif not EMAIL_RE.match(email) or len(email) > 200:
            row["status"] = "invalid"
            row["message"] = "некорректный email — проверьте адрес"
        elif not full_name:
            row["status"] = "invalid"
            row["message"] = "нет ФИО — допишите его перед адресом"
        elif len(full_name) > MAX_TITLE_LEN:
            row["status"] = "invalid"
            row["message"] = "слишком длинное ФИО"
        elif email in seen:
            row["status"] = "duplicate"
            row["message"] = f"этот адрес уже есть в строке {seen[email]}"
        else:
            seen[email] = number

        rows.append(row)

    return rows


def check_list(conn, text: str) -> list[dict]:
    """Разбор списка + сверка с базой: какие адреса уже заняты."""
    rows = parse_teacher_list(text)
    if len(rows) > MAX_IMPORT_LINES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"В списке {len(rows)} строк, а за один раз можно добавить не больше "
                f"{MAX_IMPORT_LINES}. Разбейте список на части."
            ),
        )

    emails = [row["email"] for row in rows if row["status"] == "new"]
    if emails:
        existing = {
            found["email"]: found["full_name"]
            for found in conn.execute(
                "SELECT email, full_name FROM users WHERE email = ANY(%s)", (emails,)
            ).fetchall()
        }
        for row in rows:
            if row["status"] == "new" and row["email"] in existing:
                row["status"] = "exists"
                row["message"] = f"учётка с таким адресом уже есть: {existing[row['email']]}"
    return rows


def summary(rows: list[dict]) -> dict:
    return {
        name: sum(1 for row in rows if row["status"] == name)
        for name in ("new", "exists", "duplicate", "invalid")
    }


# =====================================================================
# Схемы
# =====================================================================


class ImportIn(BaseModel):
    text: str = Field(max_length=MAX_IMPORT_CHARS)


class InviteIn(BaseModel):
    # true — отправить письмо; false — только выдать ссылку (администратор
    # передаст её сам: в мессенджере или лично).
    send: bool = True


class ForgotIn(BaseModel):
    email: str = Field(max_length=200)


class SetPasswordIn(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        # Те же требования, что при регистрации (app/security.py).
        problem = password_problem(value)
        if problem is not None:
            raise ValueError(problem)
        return value


# =====================================================================
# Без входа: настройки входа, «забыли пароль», установка пароля по ссылке
# =====================================================================


@router.get("/options", summary="Что доступно на странице входа")
def auth_options() -> dict:
    """Странице входа: показывать ли ссылку «Зарегистрируйтесь»."""
    pool = get_pool()
    try:
        with pool.connection() as conn:
            return {"self_registration": self_registration_allowed(conn)}
    except pg_errors.UndefinedTable:
        return {"self_registration": False}


@router.post("/forgot", summary="Забыли пароль: письмо со ссылкой")
def forgot_password(payload: ForgotIn, request: Request) -> dict:
    """
    Ответ ВСЕГДА одинаковый — и для существующего адреса, и для чужого:
    иначе по ответу можно было бы собирать список учителей школы.

    Ограничение частоты считается по запрошенному адресу и по адресу клиента,
    независимо от того, есть ли такая учётка, — по нему тоже ничего не узнать.
    """
    pool = get_pool()
    email = normalize_email(payload.email)
    ip = client_ip(request)
    pending: list[mailer.Pending] = []

    try:
        with pool.connection() as conn:
            since = datetime.now(timezone.utc) - RESET_WINDOW
            counts = conn.execute(
                """
                SELECT count(*) FILTER (WHERE email = %s)           AS by_email,
                       count(*) FILTER (WHERE ip = %s AND ip <> '') AS by_ip
                FROM reset_requests
                WHERE created_at > %s
                """,
                (email, ip, since),
            ).fetchone()
            if counts["by_email"] >= MAX_RESETS_PER_EMAIL or counts["by_ip"] >= MAX_RESETS_PER_IP:
                logger.warning("Слишком частый сброс пароля: %s с адреса %s", email, ip or "?")
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "Слишком много запросов на сброс пароля. Подождите час — "
                        "или проверьте почту: письмо могло уже прийти."
                    ),
                )

            with conn.transaction():
                conn.execute(
                    "INSERT INTO reset_requests (email, ip) VALUES (%s, %s)", (email, ip)
                )
                user = conn.execute(
                    "SELECT id, full_name, email, is_active FROM users WHERE email = %s",
                    (email,),
                ).fetchone()

                # Отключённым учёткам писем не шлём — ответ при этом тот же.
                if user is not None and user["is_active"]:
                    token, expires_at = tokens.issue(conn, user["id"], tokens.RESET)
                    pending.append(
                        mailer.prepare(
                            conn,
                            tokens.RESET,
                            user_id=user["id"],
                            full_name=user["full_name"],
                            to_email=user["email"],
                            token=token,
                            expires_at=expires_at,
                        )
                    )
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    mailer.dispatch(pending)
    return {"message": FORGOT_MESSAGE}


def bad_token(kind: str, problem: str) -> HTTPException:
    """410 Gone: ссылка была, но больше не годится. Текст — что делать дальше."""
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=tokens.problem_text(kind, problem),
    )


@router.get("/tokens/{token}", summary="Чья это ссылка и годна ли она")
def token_info(token: str = Path(min_length=10, max_length=200)) -> dict:
    """Странице «Задайте пароль»: ФИО и email показываем, чтобы человек видел, чья учётка."""
    pool = get_pool()
    try:
        with pool.connection() as conn:
            row, problem = tokens.find(conn, token)
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    if problem:
        raise bad_token(row["kind"] if row else tokens.INVITE, problem)
    return {
        "kind": row["kind"],
        "full_name": row["full_name"],
        "email": row["email"],
        "expires_at": row["expires_at"],
    }


@router.post("/tokens/{token}", response_model=UserOut, summary="Задать пароль по ссылке")
def set_password_by_token(
    payload: SetPasswordIn,
    response: Response,
    token: str = Path(min_length=10, max_length=200),
) -> UserOut:
    """
    Задаёт пароль, делает учётку активной и сразу входит.

    Ссылка гаснет: повторно по ней пароль не сменить. Заодно гасим остальные
    ссылки этого человека и обрываем его старые сессии — если паролем или
    почтой пользовался кто-то ещё, доступ у него пропадает.
    """
    pool = get_pool()
    try:
        with pool.connection() as conn:
            with conn.transaction():
                row, problem = tokens.find(conn, token, lock=True)
                if problem:
                    raise bad_token(row["kind"] if row else tokens.INVITE, problem)

                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = %s, status = 'active', last_login_at = now()
                    WHERE id = %s
                    """,
                    (hash_password(payload.password), row["user_id"]),
                )
                conn.execute("UPDATE auth_tokens SET used_at = now() WHERE id = %s", (row["id"],))
                conn.execute(
                    """
                    UPDATE auth_tokens SET revoked_at = now()
                    WHERE user_id = %s AND used_at IS NULL AND revoked_at IS NULL
                    """,
                    (row["user_id"],),
                )
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (row["user_id"],))
                session_token, _ = create_session(conn, row["user_id"])
                user = conn.execute(
                    """
                    SELECT id, full_name, email, role, is_active, created_at, last_login_at
                    FROM users WHERE id = %s
                    """,
                    (row["user_id"],),
                ).fetchone()
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    set_session_cookie(response, session_token)
    logger.info(
        "Пароль задан по ссылке (%s): %s",
        "приглашение" if row["kind"] == tokens.INVITE else "сброс",
        user["email"],
    )
    return UserOut(**user)


# =====================================================================
# Администратор: список, приглашения, журнал писем
# =====================================================================


@admin_router.post("/teachers/import/preview", summary="Проверить список учителей")
def import_preview(payload: ImportIn, admin: dict = Depends(require_admin)) -> dict:
    """Только проверка: ничего не создаётся и не отправляется."""
    pool = get_pool()
    with pool.connection() as conn:
        rows = check_list(conn, payload.text)
    return {"rows": rows, "summary": summary(rows)}


def _invite(conn, user: dict, admin: dict, *, send: bool) -> tuple[dict, mailer.Pending | None]:
    """Новое приглашение одному учителю (в текущей транзакции)."""
    token, expires_at = tokens.issue(conn, user["id"], tokens.INVITE, created_by=admin["id"])
    pending = None
    if send:
        pending = mailer.prepare(
            conn,
            tokens.INVITE,
            user_id=user["id"],
            full_name=user["full_name"],
            to_email=user["email"],
            token=token,
            expires_at=expires_at,
            invited_by=admin["full_name"],
        )
    return (
        {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "invite_url": mailer.link_for(tokens.INVITE, token),
            "expires_at": expires_at,
        },
        pending,
    )


@admin_router.post(
    "/teachers/import",
    status_code=status.HTTP_201_CREATED,
    summary="Создать учётки по списку и отправить приглашения",
)
def import_teachers(payload: ImportIn, admin: dict = Depends(require_admin)) -> dict:
    """
    Список проверяется заново на сервере: между «Проверить» и «Создать» кто-то
    мог занять адрес. Создаются только строки со статусом «новый», остальные
    возвращаются в ответе как пропущенные.

    Учётка создаётся без пароля (status = 'invited'): войти в неё нельзя, пока
    учитель не перейдёт по ссылке из приглашения и не задаст пароль сам.
    """
    pool = get_pool()
    created: list[dict] = []
    pending: list[mailer.Pending] = []

    try:
        with pool.connection() as conn, conn.transaction():
            rows = check_list(conn, payload.text)
            for row in rows:
                if row["status"] != "new":
                    continue
                user = conn.execute(
                    """
                    INSERT INTO users (full_name, email, password_hash, role, status, invited_by)
                    VALUES (%s, %s, NULL, 'teacher', 'invited', %s)
                    ON CONFLICT (email) DO NOTHING
                    RETURNING id, full_name, email
                    """,
                    (row["full_name"], row["email"], admin["id"]),
                ).fetchone()
                if user is None:
                    # Адрес заняли за мгновение до нас — считаем «уже есть».
                    row["status"] = "exists"
                    row["message"] = "учётка с таким адресом уже есть"
                    continue
                info, letter = _invite(conn, user, admin, send=True)
                created.append(info)
                if letter is not None:
                    pending.append(letter)
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc
    except pg_errors.UndefinedColumn as exc:
        raise migration_missing() from exc

    mailer.dispatch(pending)
    logger.info("Администратор %s добавил учителей списком: %s", admin["email"], len(created))
    return {
        "created": created,
        "skipped": [row for row in rows if row["status"] != "new"],
        "mail_configured": get_settings().mail_enabled,
    }


@admin_router.post("/teachers/invite-pending", summary="Ещё раз всем, кто не активировал")
def invite_pending(admin: dict = Depends(require_admin)) -> dict:
    """Новые приглашения всем включённым учёткам без пароля. Старые ссылки гаснут."""
    pool = get_pool()
    invited: list[dict] = []
    pending: list[mailer.Pending] = []

    try:
        with pool.connection() as conn, conn.transaction():
            users = conn.execute(
                """
                SELECT id, full_name, email FROM users
                WHERE status = 'invited' AND is_active
                ORDER BY full_name
                """
            ).fetchall()
            for user in users:
                info, letter = _invite(conn, user, admin, send=True)
                invited.append(info)
                if letter is not None:
                    pending.append(letter)
    except (pg_errors.UndefinedTable, pg_errors.UndefinedColumn) as exc:
        raise migration_missing() from exc

    mailer.dispatch(pending)
    logger.info("Администратор %s повторил приглашения: %s", admin["email"], len(invited))
    return {"invited": invited, "mail_configured": get_settings().mail_enabled}


@admin_router.post("/teachers/{user_id}/invite", summary="Новое приглашение учителю")
def invite_teacher(
    payload: InviteIn,
    user_id: int = Path(ge=1),
    admin: dict = Depends(require_admin),
) -> dict:
    """
    «Отправить ещё раз» (send=true) и «Скопировать ссылку» (send=false).
    В обоих случаях выдаётся НОВАЯ ссылка, а прежние перестают работать:
    сам токен в базе не хранится, показать старую ссылку ещё раз нельзя.
    """
    pool = get_pool()
    try:
        with pool.connection() as conn, conn.transaction():
            user = conn.execute(
                "SELECT id, full_name, email, status, is_active FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Такого учителя нет в списке."
                )
            if user["status"] != "invited":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Этот учитель уже задал пароль — приглашение ему не нужно. "
                        "Если он забыл пароль, пусть нажмёт «Забыли пароль?» на странице входа."
                    ),
                )
            if not user["is_active"]:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Учётная запись отключена. Сначала включите её.",
                )
            info, letter = _invite(conn, user, admin, send=payload.send)
    except (pg_errors.UndefinedTable, pg_errors.UndefinedColumn) as exc:
        raise migration_missing() from exc

    mailer.dispatch([letter] if letter is not None else [])
    logger.info(
        "Администратор %s выдал приглашение %s (%s)",
        admin["email"],
        user["email"],
        "письмом" if payload.send else "ссылкой",
    )
    return {**info, "sent": payload.send, "mail_configured": get_settings().mail_enabled}


@admin_router.get("/mail", summary="Почта: настроена ли и журнал писем")
def mail_overview(admin: dict = Depends(require_admin)) -> dict:
    """Адрес отправителя показываем, пароль — никогда."""
    settings = get_settings()
    pool = get_pool()
    try:
        with pool.connection() as conn:
            log = conn.execute(
                """
                SELECT m.id, m.to_email, m.kind, m.created_at, m.status, m.sent_at, m.error,
                       u.full_name
                FROM mail_log m
                LEFT JOIN users u ON u.id = m.user_id
                ORDER BY m.id DESC
                LIMIT 100
                """
            ).fetchall()
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    return {
        "configured": settings.mail_enabled,
        "from_address": settings.mail_from if settings.mail_enabled else "",
        "public_base_url": settings.public_base_url,
        "log": log,
    }
