"""
Точка входа приложения.

Запуск (из папки backend):
    uvicorn app.main:app --reload

Автодокументация после запуска: http://127.0.0.1:8000/docs
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app import ai_generation, db, mailer
from app.config import get_settings
from app.errors import validation_error_handler
from app.routers import accounts, admin, ai, auth, health, public, results, tests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Код до `yield` выполняется при старте приложения,
    код после `yield` — при остановке.

    Важно: если база недоступна, приложение всё равно поднимется,
    но /health честно ответит "database": "down". Так удобнее разрабатывать:
    фронтенд сразу увидит понятную ошибку вместо «сервер не отвечает».
    """
    try:
        db.init_pool()
        logger.info("Подключение к PostgreSQL установлено")
        # Генерации, оборванные прошлой остановкой сервера, помечаем
        # «не удалось», чтобы учитель мог их повторить.
        ai_generation.recover_interrupted_jobs()
        # Письма, ждавшие в очереди при остановке, уже не уйдут — отметим в журнале.
        mailer.recover_interrupted()
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось подключиться к базе: %s", exc)
        logger.error("Проверь, что PostgreSQL запущен и DATABASE_URL в backend/.env верный")

    if settings.ai_enabled:
        # Только модели — ключ в лог не пишем никогда.
        logger.info(
            "Генерация через ИИ включена: модель %s, самопроверка %s",
            settings.ai_model,
            settings.ai_check_model_name,
        )
        key = settings.ai_api_key.strip()
        if not key.isascii() or any(char.isspace() for char in key):
            # Частая ошибка: в .env осталась заглушка по-русски или ключ скопировался
            # с переводом строки. Такой ключ не уйдёт даже в заголовок запроса.
            logger.error(
                "AI_API_KEY записан недопустимыми символами (не латиница или пробелы "
                "внутри) — генерация работать не будет. Вставьте настоящий ключ AITUNNEL."
            )
    else:
        logger.info("Генерация через ИИ выключена (AI_API_KEY пуст) — доступен ручной путь")

    if settings.mail_enabled:
        # Только адрес сервера и отправителя — пароль SMTP в лог не пишем никогда.
        logger.info(
            "Почта включена: %s:%s, отправитель %s",
            settings.smtp_host,
            settings.smtp_port,
            settings.mail_from or "не задан (SMTP_FROM)",
        )
        if not settings.smtp_password.isascii():
            # Не ошибка сама по себе (пароль уйдёт в UTF-8), но чаще всего это
            # заглушка или буквы, набранные в русской раскладке. Сам пароль не пишем.
            logger.warning(
                "В SMTP_PASSWORD есть не-латинские символы. Если это не опечатка — всё "
                "в порядке; иначе почтовый сервер ответит «не принял логин или пароль»."
            )
    else:
        logger.info(
            "Почта не настроена (SMTP_HOST пуст) — тестовый режим: письма пишутся в лог"
        )

    yield  # здесь приложение работает и обрабатывает запросы

    db.close_pool()
    logger.info("Соединения с базой закрыты")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API сервиса проверочных работ по умениям.",
    lifespan=lifespan,
    # На школьном сервере автодокументацию выключаем (DOCS_ENABLED=false):
    # она перечисляет все эндпоинты, а пользы посетителям не приносит.
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# CORS: браузер по умолчанию запрещает странице с localhost:5174 (фронтенд)
# обращаться к localhost:8000 (бэкенд). Этот блок разрешает такие запросы.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ошибки валидации отдаём понятным текстом на русском вместо стандартного
# англоязычного списка Pydantic (см. app/errors.py).
app.add_exception_handler(RequestValidationError, validation_error_handler)

# Роутеры — способ разложить эндпоинты по файлам вместо одного длинного main.py.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(accounts.admin_router)
app.include_router(admin.router)
app.include_router(ai.admin_router)
app.include_router(ai.router)
app.include_router(tests.router)
app.include_router(tests.my_router)
app.include_router(public.router)
app.include_router(results.router)


@app.get("/", tags=["service"])
def root() -> dict[str, str]:
    """Корневой адрес — просто подсказка, куда идти дальше."""
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }
