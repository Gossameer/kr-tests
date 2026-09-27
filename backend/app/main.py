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

from app import db
from app.config import get_settings
from app.errors import validation_error_handler
from app.routers import admin, auth, health, public, results, tests

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
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось подключиться к базе: %s", exc)
        logger.error("Проверь, что PostgreSQL запущен и DATABASE_URL в backend/.env верный")

    yield  # здесь приложение работает и обрабатывает запросы

    db.close_pool()
    logger.info("Соединения с базой закрыты")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API сервиса контрольных работ: тесты с выбором ответа.",
    lifespan=lifespan,
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
app.include_router(admin.router)
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
