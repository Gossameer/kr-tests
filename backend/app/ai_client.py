"""
Запросы к ИИ-сервису (AITUNNEL, OpenAI-совместимый API).

    POST {AI_API_BASE_URL}/chat/completions
    Authorization: Bearer {AI_API_KEY}

Почему стандартный http.client, а не httpx/openai: запрос ровно один и простой,
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
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

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
# Модели, которые отвергли «reasoning: {effort: none}» (отключение размышлений).
_no_thinking_off_models: set[str] = set()

# Как AITUNNEL отключает размышления модели (единый параметр для всех моделей,
# см. https://aitunnel.ru/docs/reasoning). У qwen3.7-plus размышления по умолчанию
# включены: модель сначала «думает» сотни токенов и только потом пишет ответ.
THINKING_OFF = {"effort": "none"}

KIND_NAMES = {"generate": "генерация", "check": "самопроверка"}
_no_reasoning_lock = threading.Lock()


class AIError(Exception):
    """
    Ошибка обращения к ИИ.

    message   — текст для учителя, без технических подробностей и без ключа;
    fatal     — повторять бессмысленно (ключ не принят, нет денег, нет модели):
                генерацию надо остановить целиком, а не мучить каждый вариант;
    retryable — временный сбой (обрыв связи, таймаут, 429, 5xx): запрос стоит
                повторить после паузы;
    short     — причина в двух-трёх словах, для подписи в таблице заданий
                («нет связи с ИИ-сервисом»);
    reason    — подробность для журнала и лога («обрыв при установке соединения:
                SSLEOFError за 5.0 с»). Ключа и текста запроса в ней нет.
    """

    def __init__(
        self,
        message: str,
        *,
        fatal: bool = False,
        retryable: bool = False,
        short: str = "",
        reason: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fatal = fatal
        self.retryable = retryable
        self.short = short or message
        self.reason = reason or message
        # Сервер попросил подождать столько секунд (заголовок Retry-After при 429).
        self.retry_after: float | None = None
        # Все повторы исчерпаны — выше по коду повторять тот же запрос незачем.
        self.exhausted = False
        # Ответ оборван по лимиту токенов (обычно всё ушло в размышления).
        self.truncated = False


class AIUnavailable(AIError):
    """Сервис не ответил: таймаут, обрыв, соединение не установилось."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(
            "ИИ-сервис недоступен. Попробуйте позже.",
            retryable=True,
            short="нет связи с ИИ-сервисом",
            reason=reason or "ИИ-сервис недоступен",
        )


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    # Сколько секунд занял запрос (вместе с повторами без отвергнутых параметров).
    seconds: float = 0.0
    # Из них токены размышлений — по ним видно, отключились ли размышления.
    reasoning_tokens: int = 0


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
        return AIError(
            "ИИ-сервис перегружен запросами. Попробуйте чуть позже.",
            retryable=True,
            short="ИИ-сервис перегружен запросами",
            reason=f"429 — слишком много запросов: {provider_text}",
        )
    if status_code >= 500:
        return AIError(
            f"ИИ-сервис ответил ошибкой {status_code}. Попробуйте позже.",
            retryable=True,
            short=f"ошибка ИИ-сервиса ({status_code})",
            reason=f"ошибка сервера {status_code}: {provider_text}",
        )
    # 400/404/422: модель не найдена, неверный параметр и т. п. — повтор не поможет.
    return AIError(f"ИИ-сервис отклонил запрос: {provider_text}", fatal=True)


# ---------------------------------------------------------------------
# Соединения
#
# Соединение с ИИ-сервисом держим открытым и используем повторно. Причина не
# в скорости: на практике НОВОЕ соединение с api.aitunnel.ru обрывается ещё на
# рукопожатии примерно в половине случаев (SSL: UNEXPECTED_EOF, через 5 секунд),
# а уже установленное работает без сбоев. Пока клиент открывал соединение на
# каждый запрос, половина запросов падала «сервис недоступен».
# ---------------------------------------------------------------------

# Сколько секунд ждать установки соединения (TCP + TLS). Ответа модели ждём
# дольше — AI_TIMEOUT_SECONDS.
CONNECT_TIMEOUT = 15

# Сбой на соединении, взятом из запаса: сервер мог закрыть его, пока оно лежало.
# Это не ошибка сервиса — молча повторяем на свежем соединении.
_STALE_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    http.client.ResponseNotReady,
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    ssl.SSLEOFError,
    ssl.SSLZeroReturnError,
)


class _Pool:
    """Запас открытых соединений с одним адресом. Одно соединение — один поток за раз."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._idle: list[http.client.HTTPConnection] = []
        self._context = ssl.create_default_context()

    def take(self, scheme: str, host: str, port: int) -> tuple[http.client.HTTPConnection, bool]:
        """Возвращает (соединение, было ли оно уже открыто)."""
        key = (scheme, host, port)
        with self._lock:
            if key != self._key:
                # Адрес сменили (другой AI_API_BASE_URL) — старые соединения не нужны.
                stale, self._idle, self._key = self._idle, [], key
            else:
                stale = []
                if self._idle:
                    return self._idle.pop(), True
        for connection in stale:
            connection.close()

        if scheme == "https":
            return (
                http.client.HTTPSConnection(
                    host, port, timeout=CONNECT_TIMEOUT, context=self._context
                ),
                False,
            )
        return http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT), False

    def give_back(self, connection: http.client.HTTPConnection) -> None:
        with self._lock:
            if len(self._idle) < 16:
                self._idle.append(connection)
                return
        connection.close()


_pool = _Pool()


def _one_request(payload: dict) -> tuple[int, bytes, str]:
    """
    Один HTTP-запрос. Возвращает (код ответа, тело, Retry-After).
    Сетевой сбой → AIUnavailable с причиной: на каком шаге и что случилось.
    """
    settings = get_settings()
    base = urlsplit(settings.ai_api_base_url.strip())
    scheme = base.scheme or "https"
    host = base.hostname or ""
    port = base.port or (443 if scheme == "https" else 80)
    path = base.path.rstrip("/") + "/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key.strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connection": "keep-alive",
    }
    if not host or scheme not in ("http", "https"):
        raise ValueError("нет адреса сервера или неизвестный протокол")

    for fresh_attempt in (False, True):
        connection, reused = _pool.take(scheme, host, port)
        step = "при установке соединения"
        started = time.monotonic()
        try:
            if connection.sock is None:
                connection.connect()
            # Соединение есть — дальше ждём уже ответ модели, это дольше.
            connection.sock.settimeout(settings.ai_timeout_seconds)
            step = "при отправке запроса"
            connection.request("POST", path, body=body, headers=headers)
            step = "при ожидании ответа"
            response = connection.getresponse()
            data = response.read()
        except (UnicodeEncodeError, ValueError):
            connection.close()
            raise
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            if reused and not fresh_attempt and isinstance(exc, _STALE_ERRORS):
                # Сервер закрыл простаивавшее соединение — повторяем на новом.
                continue
            took = time.monotonic() - started
            if isinstance(exc, TimeoutError):
                what = f"таймаут {step} ({took:.0f} с)"
            else:
                what = f"обрыв {step}: {type(exc).__name__} за {took:.1f} с"
            raise AIUnavailable(what) from None

        if response.will_close:
            connection.close()
        else:
            _pool.give_back(connection)
        return response.status, data, response.headers.get("Retry-After", "")

    raise AIUnavailable("обрыв соединения")  # сюда не доходим: второй проход всегда выходит сам


def _post(payload: dict) -> dict:
    """
    Один запрос к /chat/completions. Возвращает разобранный JSON или бросает AIError.

    Сетевые сбои без ответа (таймаут, обрыв, отказ в соединении) → AIUnavailable.
    Ключ в лог не пишем: только адрес и модель.
    """
    settings = get_settings()
    base_url = settings.ai_api_base_url.rstrip("/")

    try:
        status_code, body, retry_after = _one_request(payload)
    except (UnicodeEncodeError, ValueError) as exc:
        # Сюда попадают ошибки ДО отправки: http.client не пропускает в заголовке
        # перевод строки и не-латинские символы (частая беда скопированного ключа),
        # а разбор адреса — кривой AI_API_BASE_URL. Сам ключ в лог не пишем.
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

    if status_code >= 400:
        provider_text = _provider_message(body)
        if provider_text is None:
            # Ответ без JSON: это не сам сервис, а прокси/балансировщик по пути.
            raise AIUnavailable(f"ответ {status_code} без JSON (отвечал не сам сервис)")
        error = _error_for_status(status_code, provider_text)
        error.status_code = status_code  # type: ignore[attr-defined]
        error.provider_text = provider_text  # type: ignore[attr-defined]
        if retry_after.strip().isdigit():
            error.retry_after = float(retry_after.strip())
        raise error

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        raise AIUnavailable("ответ 200, но не JSON") from None

    if not isinstance(data, dict):
        raise AIError("ИИ-сервис вернул ответ непонятного вида.", short="непонятный ответ ИИ")
    return data


def retry_pauses() -> list[float]:
    """Паузы перед повторами временных сбоев: «2,5,10» → [2, 5, 10]."""
    pauses: list[float] = []
    for part in get_settings().ai_retry_pauses.split(","):
        try:
            pauses.append(max(0.0, float(part)))
        except ValueError:
            continue
    return pauses


def _post_with_retries(
    payload: dict, ctx: "RequestContext", *, model: str, kind: str, label: str
) -> dict:
    """
    Запрос с повторами ВРЕМЕННЫХ сбоев: обрыв связи, таймаут, 429, 5xx.

    Такие сбои не говорят ничего плохого ни о запросе, ни об ответе модели —
    через несколько секунд тот же запрос обычно проходит. Поэтому повторяем
    его здесь, одинаково для генерации и самопроверки, с паузами
    AI_RETRY_PAUSES (2, 5 и 10 секунд). Каждая неудачная попытка пишется
    в журнал запросов с настоящей причиной.
    """
    pauses = retry_pauses()
    what = KIND_NAMES.get(kind, kind)

    for attempt in range(len(pauses) + 1):
        try:
            return _post(payload)
        except AIError as error:
            record_request(ctx, model=model, kind=kind, success=False, error=error.reason)
            if not error.retryable:
                raise
            if attempt == len(pauses):
                error.exhausted = True
                logger.warning(
                    "ИИ: %s · %s · %s: %s — повторы исчерпаны (%s). Если так постоянно — "
                    "проверьте связь с %s (зеркало: %s)",
                    what,
                    model,
                    label or "—",
                    error.reason,
                    len(pauses),
                    get_settings().ai_api_base_url,
                    MIRROR_HINT,
                )
                raise
            # При 429 сервер может сам сказать, сколько ждать, — но не дольше 30 с.
            pause = pauses[attempt]
            if error.retry_after is not None:
                pause = min(max(pause, error.retry_after), 30.0)
            logger.warning(
                "ИИ: %s · %s · %s: %s — повтор %s из %s через %g с",
                what,
                model,
                label or "—",
                error.reason,
                attempt + 1,
                len(pauses),
                pause,
            )
            time.sleep(pause)

    raise AIUnavailable()  # недостижимо: цикл всегда выходит через return или raise


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
    """Похоже ли, что провайдеру не понравился параметр размышлений."""
    # Провайдеры формулируют по-разному: «Unsupported parameter: 'reasoning_effort'»,
    # «unknown field», «invalid request». Поэтому текст почти не разбираем: любая
    # 400/422 на запрос с этим параметром — повод попробовать без него. Исключение —
    # когда ошибка явно про max_tokens: тогда параметры размышлений ни при чём.
    return _is_bad_request(error) and not _wants_max_completion_tokens(error)


def chat(
    ctx: RequestContext,
    *,
    kind: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    reasoning_effort: str = "",
    thinking: bool | None = None,
    label: str = "",
    temperature: float | None = None,
) -> ChatResult:
    """
    Запрос к /chat/completions. Каждая попытка пишется в журнал отдельно.

    kind      — 'generate' или 'check', нужен журналу и логу;
    thinking  — False: попросить модель не размышлять (быстрее и дешевле),
                None: не передавать ничего (как решит провайдер);
    label     — что это за запрос («умение 2, варианты 1–4») — только для лога.

    В лог пишется время КАЖДОГО запроса: модель, метка, секунды, токены.
    По нему видно, где генерация тратит время.
    """
    with _no_reasoning_lock:
        limit_field = (
            "max_completion_tokens" if model in _completion_tokens_models else "max_tokens"
        )
        effort = "" if model in _no_reasoning_models else reasoning_effort.strip()
        thinking_off = thinking is False and model not in _no_thinking_off_models

    # Лимит токенов уходит в КАЖДОМ запросе — меняется только имя поля.
    payload: dict = {"model": model, "messages": messages, limit_field: max_tokens}
    if temperature is not None:
        payload["temperature"] = temperature
    if effort:
        payload["reasoning_effort"] = effort
    if thinking_off:
        payload["reasoning"] = dict(THINKING_OFF)

    started = time.monotonic()
    what = KIND_NAMES.get(kind, kind)

    # Не больше трёх исправлений запроса: имя поля лимита и два параметра размышлений.
    for _ in range(4):
        try:
            data = _post_with_retries(payload, ctx, model=model, kind=kind, label=label)
            break
        except AIError as error:
            # Неудачные попытки уже записаны в журнал — здесь только решаем,
            # можно ли починить сам запрос (убрать параметр, который не приняли).

            if "max_tokens" in payload and _wants_max_completion_tokens(error):
                logger.info(
                    "Модель %s просит max_completion_tokens вместо max_tokens — повторяем",
                    model,
                )
                with _no_reasoning_lock:
                    _completion_tokens_models.add(model)
                payload["max_completion_tokens"] = payload.pop("max_tokens")
                continue

            if "reasoning" in payload and _reasoning_rejected(error):
                logger.warning(
                    "Модель %s не приняла отключение размышлений (reasoning.effort=none) — "
                    "повторяем без него; генерация будет медленнее",
                    model,
                )
                with _no_reasoning_lock:
                    _no_thinking_off_models.add(model)
                payload.pop("reasoning", None)
                continue

            if "reasoning_effort" in payload and _reasoning_rejected(error):
                logger.info(
                    "Модель %s не приняла reasoning_effort — повторяем запрос без него", model
                )
                with _no_reasoning_lock:
                    _no_reasoning_models.add(model)
                payload.pop("reasoning_effort", None)
                continue

            logger.info(
                "ИИ: %s · %s · %s · %.1f с · ошибка: %s",
                what,
                model,
                label or "—",
                time.monotonic() - started,
                error.reason,
            )
            raise

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = _as_count(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _as_count(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )

    details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        _as_count(details.get("reasoning_tokens")) if isinstance(details, dict) else 0
    )

    text, finish_reason, problem = _read_answer(data, max_tokens)
    seconds = time.monotonic() - started

    # Замер: одна строка на запрос. По этим строкам видно, где уходит время.
    logger.info(
        "ИИ: %s · %s · %s · %.1f с · токены %s→%s%s%s",
        what,
        model,
        label or "—",
        seconds,
        prompt_tokens,
        completion_tokens,
        f" (из них размышления {reasoning_tokens})" if reasoning_tokens else "",
        "" if problem is None else f" · {problem}",
    )

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
        error = AIError(problem, short=_short_problem(problem, finish_reason))
        error.truncated = finish_reason == "length"
        # Сколько токенов ушло: по этому вызывающий решает, поможет ли лимит побольше.
        error.completion_tokens = completion_tokens  # type: ignore[attr-defined]
        error.reasoning_tokens = reasoning_tokens  # type: ignore[attr-defined]
        raise error

    return ChatResult(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finish_reason=finish_reason,
        seconds=seconds,
        reasoning_tokens=reasoning_tokens,
    )


def _short_problem(problem: str, finish_reason: str) -> str:
    """Причина плохого ответа в двух-трёх словах — для подписи в таблице."""
    if finish_reason == "length":
        return "лимит токенов ушёл на размышления"
    if "отказался" in problem:
        return "ИИ отказался отвечать"
    if "пустой" in problem:
        return "пустой ответ ИИ"
    return "непонятный ответ ИИ"


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
