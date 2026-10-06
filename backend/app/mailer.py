"""
Письма: приглашение учителя и сброс пароля.

Как устроено:
  * письмо собирается сразу (простой текст + HTML, без картинок), но уходит
    в фоне: администратор, создавший 40 учёток, не ждёт 40 соединений с почтой;
  * между письмами пауза SEND_INTERVAL: почтовые сервисы режут тех, кто шлёт
    пачками, а нам спешить некуда;
  * каждое письмо записано в журнал mail_log: кому, какое, когда и чем
    кончилось. Текста и ссылки в журнале нет — там был бы готовый вход в учётку;
  * SMTP_HOST пуст → тестовый режим: письмо никуда не уходит, а целиком
    печатается в лог сервера (со ссылкой — иначе его не проверить), в журнале
    статус 'test'. Ссылки при этом администратор копирует из админки.

Пароль SMTP не попадает ни в лог, ни в журнал, ни в тексты ошибок.
"""

import base64
import html
import logging
import queue
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app import db
from app.config import get_settings
from app.tokens import INVITE

logger = logging.getLogger(__name__)

# Не чаще одного письма в секунду — лимиты почтовых сервисов.
SEND_INTERVAL = 1.0
MAX_ERROR_LEN = 300

SERVICE_NAME = "Проверочные работы"
SCHOOL_NAME = "Школа № 2090"


@dataclass
class Pending:
    """Письмо, записанное в журнал и ждущее отправки."""

    log_id: int
    kind: str
    to_email: str
    message: EmailMessage
    # Простой текст — для лога в тестовом режиме.
    text: str


_queue: "queue.Queue[Pending]" = queue.Queue()
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


# =====================================================================
# Текст писем
# =====================================================================


def link_for(kind: str, token: str) -> str:
    """Ссылка для письма: {PUBLIC_BASE_URL}/invite/<token> или /reset/<token>."""
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/{'invite' if kind == INVITE else 'reset'}/{token}"


def format_deadline(expires_at: datetime) -> str:
    """«до 11.10.2026 14:30» — по часам сервера (на сервере TZ=Europe/Moscow)."""
    return expires_at.astimezone().strftime("%d.%m.%Y %H:%M")


def compose(
    kind: str,
    *,
    full_name: str,
    link: str,
    expires_at: datetime,
    invited_by: str = "",
) -> tuple[str, str, str]:
    """Возвращает (тему, простой текст, HTML)."""
    deadline = format_deadline(expires_at)

    if kind == INVITE:
        subject = f"Приглашение в сервис «{SERVICE_NAME}» — {SCHOOL_NAME}"
        who = f"{invited_by} приглашает вас" if invited_by else "Вас приглашают"
        intro = (
            f"{who} в сервис «{SERVICE_NAME}» ({SCHOOL_NAME}). В нём учителя "
            "составляют проверочные работы по умениям, ученики решают их по ссылке, "
            "а результаты видны сразу — по каждому ученику и каждому умению."
        )
        action = "Чтобы начать, задайте пароль"
        button = "Задать пароль"
        valid = f"Ссылка действует 7 дней — до {deadline} — и срабатывает один раз."
        ignore = "Если вы не ждали этого письма — просто проигнорируйте его."
    else:
        subject = f"Сброс пароля — «{SERVICE_NAME}», {SCHOOL_NAME}"
        intro = (
            f"Кто-то (скорее всего, вы) попросил сбросить пароль в сервисе "
            f"«{SERVICE_NAME}» ({SCHOOL_NAME})."
        )
        action = "Чтобы задать новый пароль, откройте ссылку"
        button = "Задать новый пароль"
        valid = f"Ссылка действует 1 час — до {deadline} — и срабатывает один раз."
        ignore = (
            "Если вы не просили сбросить пароль — просто проигнорируйте письмо: "
            "старый пароль продолжит работать."
        )

    text = (
        f"Здравствуйте, {full_name}!\n\n"
        f"{intro}\n\n"
        f"{action}:\n{link}\n\n"
        f"{valid}\n\n"
        f"{ignore}\n\n"
        f"— {SERVICE_NAME}, {SCHOOL_NAME}\n"
    )

    safe_link = html.escape(link, quote=True)
    body = f"""<!doctype html>
<html lang="ru">
  <body style="margin:0;padding:24px;background:#f4f5f8;font-family:Arial,Helvetica,sans-serif;color:#1b1f27;">
    <div style="max-width:560px;margin:0 auto;padding:24px;background:#ffffff;border-radius:12px;">
      <p style="margin:0 0 16px;font-size:16px;">Здравствуйте, {html.escape(full_name)}!</p>
      <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">{html.escape(intro)}</p>
      <p style="margin:0 0 12px;font-size:16px;">{html.escape(action)}:</p>
      <p style="margin:0 0 16px;">
        <a href="{safe_link}" style="display:inline-block;padding:12px 20px;background:#2f56c8;color:#ffffff;text-decoration:none;border-radius:8px;font-size:16px;">{html.escape(button)}</a>
      </p>
      <p style="margin:0 0 16px;font-size:14px;line-height:1.5;color:#5b6472;">
        Если кнопка не нажимается, скопируйте ссылку в адресную строку:<br>
        <a href="{safe_link}" style="color:#2f56c8;word-break:break-all;">{safe_link}</a>
      </p>
      <p style="margin:0 0 16px;font-size:14px;line-height:1.5;">{html.escape(valid)}</p>
      <p style="margin:0 0 16px;font-size:14px;line-height:1.5;color:#5b6472;">{html.escape(ignore)}</p>
      <p style="margin:0;font-size:13px;color:#5b6472;">— {html.escape(SERVICE_NAME)}, {html.escape(SCHOOL_NAME)}</p>
    </div>
  </body>
</html>
"""
    return subject, text, body


def build_message(to_name: str, to_email: str, subject: str, text: str, body_html: str) -> EmailMessage:
    settings = get_settings()
    # policy.SMTP: переводы строк и длина строк — как требует почтовый протокол,
    # а кириллица в заголовках (тема, имя отправителя и получателя) кодируется
    # по RFC 2047 — на провод уходит только ASCII, это понимает любой сервер.
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = subject
    # В тестовом режиме отправителя может не быть — подставляем заглушку,
    # письмо всё равно никуда не уйдёт.
    sender = settings.mail_from or "noreply@localhost"
    message["From"] = formataddr((settings.smtp_from_name, sender))
    message["To"] = formataddr((to_name, to_email))
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or "localhost")
    # Тело — UTF-8 в base64: читается везде и не зависит от того, умеет ли
    # сервер принимать 8-битный текст.
    message.set_content(text, charset="utf-8", cte="base64")
    message.add_alternative(body_html, subtype="html", charset="utf-8", cte="base64")
    return message


# =====================================================================
# Журнал и очередь
# =====================================================================


def prepare(
    conn,
    kind: str,
    *,
    user_id: int,
    full_name: str,
    to_email: str,
    token: str,
    expires_at: datetime,
    invited_by: str = "",
) -> Pending:
    """
    Собирает письмо и записывает его в журнал (в текущей транзакции).

    Само письмо в очередь НЕ кладёт: это делает dispatch() уже после того,
    как транзакция закоммичена. Иначе фоновый поток мог бы взяться за письмо,
    строки которого в базе ещё не видно, или отправить приглашение в учётку,
    создание которой потом откатилось.
    """
    subject, text, body_html = compose(
        kind,
        full_name=full_name,
        link=link_for(kind, token),
        expires_at=expires_at,
        invited_by=invited_by,
    )
    row = conn.execute(
        """
        INSERT INTO mail_log (user_id, to_email, kind, status)
        VALUES (%s, %s, %s, 'queued')
        RETURNING id
        """,
        (user_id, to_email, kind),
    ).fetchone()
    return Pending(
        log_id=row["id"],
        kind=kind,
        to_email=to_email,
        message=build_message(full_name, to_email, subject, text, body_html),
        text=text,
    )


def dispatch(pending: list[Pending]) -> None:
    """Ставит письма в очередь на отправку. Вызывать ПОСЛЕ коммита."""
    if not pending:
        return
    _ensure_worker()
    for item in pending:
        _queue.put(item)


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="mailer", daemon=True)
            _worker.start()


def _set_status(log_id: int, status: str, error: str = "") -> None:
    try:
        with db.get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE mail_log
                SET status = %s, error = %s,
                    sent_at = CASE WHEN %s IN ('sent', 'test') THEN now() ELSE NULL END
                WHERE id = %s
                """,
                (status, error.replace("\x00", "")[:MAX_ERROR_LEN], status, log_id),
            )
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить журнал писем (запись %s)", log_id, exc_info=True)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _login(client: smtplib.SMTP, user: str, password: str) -> None:
    """
    Вход на почтовый сервер.

    smtplib.login кодирует логин и пароль в ASCII и на любой не-латинской букве
    падает с UnicodeEncodeError — ещё до обращения к серверу. Сам протокол
    (RFC 4616) передаёт их в UTF-8, поэтому такой пароль отправляем сами:
    base64 от UTF-8. Обычные латинские логин и пароль идут штатным путём.
    """
    if user.isascii() and password.isascii():
        client.login(user, password)
        return

    client.ehlo_or_helo_if_needed()
    methods = client.esmtp_features.get("auth", "").upper().split()
    if "PLAIN" in methods:
        code, response = client.docmd("AUTH", "PLAIN " + _b64(f"\0{user}\0{password}"))
    elif "LOGIN" in methods:
        code, response = client.docmd("AUTH", "LOGIN " + _b64(user))
        if code == 334:
            code, response = client.docmd(_b64(password))
    else:
        raise smtplib.SMTPNotSupportedError(
            "почтовый сервер не предлагает вход по паролю (AUTH PLAIN / LOGIN)"
        )
    if code not in (235, 503):
        raise smtplib.SMTPAuthenticationError(code, response)


def _send(message: EmailMessage) -> None:
    """Одно письмо через SMTP. Бросает исключение, если не ушло."""
    settings = get_settings()
    host = settings.smtp_host.strip()
    user = settings.smtp_user.strip()
    # Таймаут действует на каждый шаг: соединение, приветствие, вход, отправку.
    timeout = settings.smtp_timeout_seconds
    # Имя для EHLO задаём сами и только латиницей: иначе Python возьмёт имя
    # компьютера, и кириллическое («Учительская-ПК») уронит соединение.
    ehlo_name = settings.smtp_ehlo_name

    if settings.smtp_ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            host,
            settings.smtp_port,
            local_hostname=ehlo_name,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        client = smtplib.SMTP(host, settings.smtp_port, local_hostname=ehlo_name, timeout=timeout)

    with client:
        encrypted = settings.smtp_ssl
        if not encrypted:
            client.ehlo()
            if client.has_extn("starttls"):
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                encrypted = True
        if user:
            if not encrypted:
                # Пароль открытым текстом по сети не отправляем никогда.
                raise RuntimeError(
                    "сервер почты не поддерживает шифрование — пароль по открытому "
                    "каналу не отправляем. Проверьте SMTP_PORT и SMTP_SSL"
                )
            _login(client, user, settings.smtp_password)
        client.send_message(message)


def _describe(exc: Exception) -> str:
    """Причина сбоя для журнала — по-человечески и без секретов."""
    timeout = get_settings().smtp_timeout_seconds
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "почтовый сервер не принял логин или пароль (SMTP_USER / SMTP_PASSWORD)"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "почтовый сервер не принял адрес получателя"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "почтовый сервер не принял адрес отправителя (SMTP_FROM)"
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return (
            f"почтовый сервер не ответил за {timeout:g} с (таймаут) — "
            "проверьте SMTP_HOST, SMTP_PORT и SMTP_SSL"
        )
    if isinstance(exc, UnicodeEncodeError):
        # Сюда попадать уже не должны; если попали — в настройках остался символ,
        # который почтовый протокол не передаёт. Сам символ в журнал не пишем.
        return (
            "в настройках почты есть не-латинский символ, который нельзя передать "
            "серверу — проверьте SMTP_USER и SMTP_FROM"
        )
    if isinstance(exc, ssl.SSLError):
        return f"не удалось установить защищённое соединение ({type(exc).__name__}) — проверьте SMTP_PORT и SMTP_SSL"
    if isinstance(exc, (ConnectionError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return f"нет связи с почтовым сервером ({type(exc).__name__})"
    return f"{type(exc).__name__}: {exc}"


def _run() -> None:
    """Фоновый поток: по одному письму, с паузой между отправками."""
    last_sent = 0.0
    while True:
        item = _queue.get()
        try:
            settings = get_settings()
            if not settings.mail_enabled:
                # Тестовый режим: письмо целиком в лог — так его можно прочитать
                # и взять ссылку, пока настоящей почты нет.
                logger.info(
                    "ПОЧТА НЕ НАСТРОЕНА — письмо не отправлено, вот его текст.\n"
                    "Кому: %s\nТема: %s\n\n%s",
                    item.to_email,
                    item.message["Subject"],
                    item.text,
                )
                _set_status(item.log_id, "test")
                continue

            wait = SEND_INTERVAL - (time.monotonic() - last_sent)
            if wait > 0:
                time.sleep(wait)
            try:
                _send(item.message)
                last_sent = time.monotonic()
                _set_status(item.log_id, "sent")
                logger.info("Письмо отправлено: %s (%s)", item.to_email, item.kind)
            except Exception as exc:  # noqa: BLE001
                last_sent = time.monotonic()
                reason = _describe(exc)
                _set_status(item.log_id, "failed", reason)
                logger.warning("Письмо не ушло: %s (%s) — %s", item.to_email, item.kind, reason)
        except Exception:  # noqa: BLE001
            logger.exception("Сбой в потоке отправки писем")
        finally:
            _queue.task_done()


def recover_interrupted() -> None:
    """
    При старте сервера: письма, которые ждали в очереди в момент остановки,
    уже не уйдут — очередь жила в памяти. Отмечаем это в журнале честно,
    чтобы администратор увидел и нажал «Отправить ещё раз».
    """
    try:
        with db.get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE mail_log
                SET status = 'failed',
                    error = 'сервер перезапустили до отправки — отправьте ещё раз'
                WHERE status = 'queued'
                """
            )
    except Exception:  # noqa: BLE001
        # Например, миграция 009 ещё не накачена — это не повод не стартовать.
        logger.warning("Не удалось проверить очередь писем при старте", exc_info=True)
