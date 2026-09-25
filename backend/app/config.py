"""
Настройки приложения.

Все значения берутся из переменных окружения, а локально — из файла .env
(рядом с папкой backend). Так секреты и адрес базы не попадают в git.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Путь к backend/.env — вычисляем от текущего файла, чтобы не зависеть от того,
# из какой папки запущен uvicorn.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Типизированные настройки. Имена полей = имена переменных в .env (регистр не важен)."""

    # Строка подключения к PostgreSQL.
    database_url: str = "postgresql://postgres:postgres@localhost:5432/kr_tests"

    # Список адресов фронтенда, которым разрешено обращаться к API (CORS).
    # В .env пишется одной строкой через запятую.
    cors_origins: str = "http://localhost:5174,http://127.0.0.1:5174"

    # Название и версия — попадают в автодокументацию на /docs.
    app_name: str = "КР — сервис контрольных работ"
    app_version: str = "0.1.0"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # лишние переменные в .env не ломают запуск
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Превращает "a,b" в ["a", "b"] — в таком виде это нужно FastAPI."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Настройки читаются один раз за время жизни процесса.
    lru_cache = «запомни результат первого вызова и возвращай его дальше».
    """
    return Settings()
