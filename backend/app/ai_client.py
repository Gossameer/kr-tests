"""
Запросы к ИИ-сервису (AITUNNEL, OpenAI-совместимый API).

    POST {AI_API_BASE_URL}/chat/completions
    Authorization: Bearer {AI_API_KEY}

Почему стандартный urllib, а не httpx/openai: запрос ровно один и простой,
а лишняя зависимость — лишнее, что надо ставить и обновлять на сервере.

Правила, которые тут зашиты:
  * max_tokens уходит в КАЖДОМ запросе: по нему провайдер резервирует стоимость;
  * ключ не попадает ни в логи, ни в тексты ошибок, ни в журнал запросов;
  * каждый запрос — удачный или нет — записывается в ai_requests (без текстов);
  * если провайдер отверг reasoning_effort — повторяем без него и больше
    его этой модели не шлём (до перезапуска сервера).
"""

import http.client
import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

from app import db
from app.config import get_settings

logger = logging.getLogger(__name__)

# Зеркало на случай, когда основной адрес недоступен из сети школы.
MIRROR_HINT = "https://ru-api.aitunnel.ru/v1"

# Сколько символов ошибки провайдера сохраняем: хватает, чтобы понять суть.
MAX_ERROR_LEN = 300

# Модели, которые уже отвергли reasoning_effort: второй раз не пробуем.
_no_reasoning_models: set[str] = set()
# Модели, которые принимают лимит только как max_completion_tokens.
_completion_tokens_models: set[str] = set()
_no_reasoning_lock = threading.Lock()


class AIError(Exception):
    """
    Ошибка обращения к ИИ.

    message — текст для учителя, без технических подробностей и без ключа.
    fatal   — повторять бессмысленно (ключ не принят, нет денег, нет модели):
              генерацию надо остановить целиком, а не мучить каждый вариант.
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.fatal = fatal


class AIUnavailable(AIError):
    """Сервис не ответил: таймаут, обрыв, соединение не установилось."""

    def __init__(self) -> None:
        super().__init__("ИИ-сервис недоступен. Попробуйте позже.")


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str


@dataclass
class RequestContext:
    """Кому записать запрос в журнал: учитель и (если есть) фоновое задание."""

    teacher_id: int
    job_id: int | None = None


def record_request(
    ctx: RequestContext,
    *,
    model: str,
    kind: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    success: bool,
    error: str = "",
) -> None:
    """
    Пишет строку в журнал запросов.

    Сбой записи не должен ронять генерацию: учителю важнее получить задания,
    чем нам — идеальный журнал. Поэтому ошибку только логируем.
    """
    try:
        with db.get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_requests (
                    teacher_id, job_id, model, kind,
                    prompt_tokens, completion_tokens, success, error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    ctx.teacher_id,
                    ctx.job_id,
                    model,
                    kind,
                    prompt_tokens,
                    completion_tokens,
                    success,
                    error.replace("\x00", "")[:MAX_ERROR_LEN],
                ),
            )
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось записать запрос к ИИ в журнал", exc_info=True)


def _provider_message(body: bytes) -> str | None:
    """
    Достаёт текст ошибки из JSON-ответа провайдера.
    None — тело не JSON (значит, отвечал не сам сервис, а что-то по пути).
    """
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None

    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:MAX_ERROR_LEN]
        if isinstance(error, str):
            return error[:MAX_ERROR_LEN]
        if isinstance(data.get("message"), str):
            return data["message"][:MAX_ERROR_LEN]
        if isinstance(data.get("detail"), str):
            return data["detail"][:MAX_ERROR_LEN]
    return json.dumps(data, ensure_ascii=False)[:MAX_ERROR_LEN]


def _error_for_status(status_code: int, provider_text: str) -> AIError:
    """Переводит HTTP-ошибку провайдера в понятный учителю текст."""
    if status_code in (401, 403):
        return AIError(
            "ИИ-сервис не принял ключ доступа. Сообщите администратору.", fatal=True
        )
    if status_code == 402:
        return AIError(
            "На счёте ИИ-сервиса закончились средства. Сообщите администратору.",
            fatal=True,
        )
    if status_code == 429:
        return AIError("ИИ-сервис перегружен запросами. Попробуйте чуть позже.")
    if status_code >= 500:
        return AIError(f"ИИ-сервис ответил ошибкой {status_code}. Попробуйте позже.")
    # 400/404/422: модель не найдена, неверный параметр и т. п. — повтор не поможет.
    return AIError(f"ИИ-сервис отклонил запрос: {provider_text}", fatal=True)


def _post(payload: dict) -> dict:
    """
    Один HTTP-запрос. Возвращает разобранный JSON или бросает AIError.

    Сетевые сбои без ответа (таймаут, обрыв, отказ в соединении) → AIUnavailable
    и подсказка про зеркало в логе. Ключ в лог не пишем: только адрес и модель.
    """
    settings = get_settings()
    base_url = settings.ai_api_base_url.rstrip("/")
    url = f"{base_url}/chat/completions"

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.ai_api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=settings.ai_timeout_seconds) as response:
            body = response.read()
    except (UnicodeEncodeError, ValueError) as exc:
        # Сюда попадают ошибки ДО отправки: http.client не пропускает в заголовке
        # перевод строки и не-латинские символы (частая беда скопированного ключа),
        # а urllib — кривой адрес. Сам ключ в лог не пишем, только признаки.
        if isinstance(exc, UnicodeEncodeError) or "header" in str(exc).lower():
            key = settings.ai_api_key.strip()
            logger.error(
                "AI_API_KEY не годится для заголовка: длина %s, только ASCII: %s, "
                "есть пробелы/переводы строк внутри: %s. Проверьте ключ в .env",
                len(key),
                key.isascii(),
                any(char.isspace() for char in key),
            )
            raise AIError(
                "Ключ ИИ-сервиса на сервере записан с ошибкой. Сообщите администратору.",
                fatal=True,
            ) from None
        logger.error("Неверный адрес AI_API_BASE_URL=%s: %s", base_url, exc)
        raise AIError(
            "Адрес ИИ-сервиса на сервере указан неверно. Сообщите администратору.",
            fatal=True,
        ) from None
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        provider_text = _provider_message(body)
        if provider_text is None:
            # Ответ без JSON: это не сам сервис, а прокси/балансировщик по пути.
            logger.warning(
                "ИИ-сервис %s вернул %s без JSON. Если повторяется — попробуйте "
                "зеркало AI_API_BASE_URL=%s",
                base_url,
                exc.code,
                MIRROR_HINT,
            )
            raise AIUnavailable() from None
        logger.warning(
            "ИИ-сервис вернул ошибку %s (модель %s): %s",
            exc.code,
            payload.get("model"),
            provider_text,
        )
        error = _error_for_status(exc.code, provider_text)
        error.status_code = exc.code  # type: ignore[attr-defined]
        error.provider_text = provider_text  # type: ignore[attr-defined]
        raise error from None
    except (
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        ConnectionError,
        http.client.HTTPException,
        OSError,
    ) as exc:
        reason = getattr(exc, "reason", exc)
        logger.warning(
            "ИИ-сервис %s недоступен (%s: %s). Если так продолжается — попробуйте "
            "зеркало: AI_API_BASE_URL=%s",
            base_url,
            type(exc).__name__,
            reason,
            MIRROR_HINT,
        )
        raise AIUnavailable() from None

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        logger.warning(
            "ИИ-сервис %s ответил не JSON. Если повторяется — попробуйте зеркало %s",
            base_url,
            MIRROR_HINT,
        )
        raise AIUnavailable() from None

    if not isinstance(data, dict):
        raise AIError("ИИ-сервис вернул ответ непонятного вида.")
    return data


def _is_bad_request(error: AIError) -> bool:
    return getattr(error, "status_code", 0) in (400, 422)


def _wants_max_completion_tokens(error: AIError) -> bool:
    """
    Модели OpenAI серии o1/gpt-5 не принимают max_tokens и прямо просят
    max_completion_tokens. Лимит мы при этом всё равно передаём — только под
    тем именем, которое понимает модель.
    """
    text = str(getattr(error, "provider_text", "")).lower()
    return _is_bad_request(error) and "max_completion_tokens" in text


def _reasoning_rejected(error: AIError) -> bool:
    """Похоже ли, что провайдеру не понравился именно параметр reasoning_effort."""
    # Провайдеры формулируют по-разному: «Unsupported parameter: 'reasoning_effort'»,
    # «unknown field», «invalid request». Поэтому текст почти не разбираем: любая
    # 400/422 на запрос с этим параметром — повод попробовать без него. Исключение —
    # когда ошибка явно про max_tokens: тогда reasoning_effort ни при чём.
    return _is_bad_request(error) and not _wants_max_completion_tokens(error)


def chat(
    ctx: RequestContext,
    *,
    kind: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    reasoning_effort: str = "",
    temperature: float | None = None,
) -> ChatResult:
    """
    Запрос к /chat/completions. Каждая попытка пишется в журнал отдельно.

    kind — 'generate' или 'check', нужен только журналу.
    """
    with _no_reasoning_lock:
        limit_field = (
            "max_completion_tokens" if model in _completion_tokens_models else "max_tokens"
        )
        effort = "" if model in _no_reasoning_models else reasoning_effort.strip()

    # Лимит токенов уходит в КАЖДОМ запросе — меняется только имя поля.
    payload: dict = {"model": model, "messages": messages, limit_field: max_tokens}
    if temperature is not None:
        payload["temperature"] = temperature
    if effort:
        payload["reasoning_effort"] = effort

    # Не больше двух исправлений запроса: имя поля лимита и reasoning_effort.
    for _ in range(3):
        try:
            data = _post(payload)
            break
        except AIError as error:
            record_request(ctx, model=model, kind=kind, success=False, error=error.message)

            if "max_tokens" in payload and _wants_max_completion_tokens(error):
                logger.info(
                    "Модель %s просит max_completion_tokens вместо max_tokens — повторяем",
                    model,
                )
                with _no_reasoning_lock:
                    _completion_tokens_models.add(model)
                payload["max_completion_tokens"] = payload.pop("max_tokens")
                continue

            if "reasoning_effort" in payload and _reasoning_rejected(error):
                logger.info(
                    "Модель %s не приняла reasoning_effort — повторяем запрос без него", model
                )
                with _no_reasoning_lock:
                    _no_reasoning_models.add(model)
                payload.pop("reasoning_effort", None)
                continue

            raise

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = _as_count(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _as_count(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )

    text, finish_reason, problem = _read_answer(data, max_tokens)

    record_request(
        ctx,
        model=model,
        kind=kind,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        success=problem is None,
        error=problem or "",
    )

    if problem is not None:
        # Кратко и без содержимого: какие поля пришли, чем закончилась генерация.
        logger.warning(
            "Неожиданный ответ ИИ (модель %s, %s): %s. Структура: %s",
            model,
            kind,
            problem,
            _describe_shape(data),
        )
        raise AIError(problem)

    return ChatResult(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finish_reason=finish_reason,
    )


def _as_count(value: object) -> int:
    """Число токенов из usage. Всё странное (None, строка, объект) — ноль."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _content_text(content: object) -> str:
    """
    Текст ответа из поля content.

    Обычно это строка, но OpenAI-совместимые сервисы присылают и список частей
    [{"type": "text", "text": "..."}], а у «думающих» моделей content бывает null,
    когда всё ушло в рассуждения.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type", "text") in ("text", "output_text"):
                value = part.get("text")
                if isinstance(value, dict):
                    value = value.get("value")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return ""


def _read_answer(data: dict, max_tokens: int) -> tuple[str, str, str | None]:
    """
    (текст, finish_reason, проблема). Проблема — текст для учителя или None.

    Ничего не бросает: любой неожиданный вид ответа превращается в понятную
    проблему, а не во «внутреннюю ошибку сервера».
    """
    error = data.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else error
        return "", "", f"ИИ-сервис вернул ошибку: {str(message)[:MAX_ERROR_LEN]}"

    choices = data.get("choices")
    if isinstance(choices, dict):
        choices = [choices]
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", "", "ИИ-сервис вернул ответ без вариантов (choices)"

    choice = choices[0]
    finish_reason = str(choice.get("finish_reason") or "")
    message = choice.get("message")
    if not isinstance(message, dict):
        # Старый формат completions: текст прямо в choices[0].text.
        message = {"content": choice.get("text")}

    text = _content_text(message.get("content"))
    if text.strip():
        return text, finish_reason, None

    has_reasoning = any(
        message.get(field) for field in ("reasoning_content", "reasoning", "thinking")
    )
    if finish_reason == "length":
        return "", finish_reason, (
            f"ИИ не успел дописать ответ: закончился лимит max_tokens ({max_tokens})"
            + (", модель потратила его на рассуждения" if has_reasoning else "")
        )
    if message.get("refusal"):
        return "", finish_reason, "ИИ отказался отвечать на запрос"
    return "", finish_reason, "ИИ-сервис вернул пустой ответ"


def _describe_shape(data: object, depth: int = 0) -> str:
    """
    Схема ответа без содержимого: {choices: [{finish_reason: 'length',
    message: {content: null, reasoning_content: str(5321)}}], usage: {...}}.
    Годится для лога: ни текста заданий, ни ключа в ней нет.
    """
    if depth > 5:
        return "…"
    if isinstance(data, dict):
        items = []
        for key, value in list(data.items())[:15]:
            if key == "finish_reason":
                items.append(f"{key}: {value!r}")
            else:
                items.append(f"{key}: {_describe_shape(value, depth + 1)}")
        return "{" + ", ".join(items) + "}"
    if isinstance(data, list):
        inner = _describe_shape(data[0], depth + 1) if data else ""
        return f"[{inner}]" + (f"×{len(data)}" if len(data) > 1 else "")
    if isinstance(data, str):
        return f"str({len(data)})"
    if data is None:
        return "null"
    return type(data).__name__
