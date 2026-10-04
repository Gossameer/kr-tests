"""
Настройки приложения.

Все значения берутся из переменных окружения, а локально — из файла .env
(рядом с папкой backend). На сервере файла .env нет: переменные приходят из
env/kr.env, который подключает docker compose. Так секреты не лежат в образе
и не попадают в git.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Путь к backend/.env — вычисляем от текущего файла, чтобы не зависеть от того,
# из какой папки запущен uvicorn. В контейнере этого файла просто нет.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Типизированные настройки. Имена полей = имена переменных окружения (регистр не важен)."""

    # Строка подключения к PostgreSQL.
    database_url: str = "postgresql://postgres:postgres@localhost:5432/kr_tests"

    # Адреса, которым разрешено обращаться к API из браузера (CORS).
    # В продакшене фронтенд и API живут на одном домене, и CORS не нужен вовсе,
    # но список оставляем: он пригодится, если фронт запустят отдельно.
    cors_origins: str = "http://localhost:5174,http://127.0.0.1:5174"

    # Ставить ли у cookie сессии флаг Secure.
    #
    # Secure означает «передавать только по https». На школьном сервере за Caddy
    # это обязательно: иначе cookie с входом можно перехватить в сети школы.
    # Локально сервис работает по http, поэтому по умолчанию выключено —
    # иначе браузер просто не сохранит cookie и вход не сработает.
    cookie_secure: bool = False

    # Доверять ли заголовку X-Forwarded-For при определении адреса клиента.
    #
    # За обратным прокси адрес в запросе — это адрес прокси, а настоящий приходит
    # в X-Forwarded-For. Верить заголовку можно ТОЛЬКО когда до сервиса нельзя
    # достучаться в обход прокси: иначе любой подставит чужой адрес и обойдёт
    # ограничение на подбор пароля. В контейнере это так: порты наружу не
    # проброшены, и дотянуться до бэкенда может только edge-caddy.
    #
    # Важно: это не единственный слой. Сам uvicorn тоже разбирает эти заголовки
    # (--proxy-headers включён по умолчанию и доверяет 127.0.0.1) и подменяет
    # адрес клиента ещё до нашего кода. Полностью отключить такое поведение
    # можно флагом --no-proxy-headers при запуске.
    trust_proxy: bool = False

    # Показывать ли автодокументацию /docs и /redoc.
    # На школьном сервере её лучше выключить: она перечисляет все эндпоинты.
    docs_enabled: bool = True

    # --- Генерация заданий через ИИ (AITUNNEL, OpenAI-совместимый API) ---
    #
    # Ключ живёт ТОЛЬКО на сервере: на фронт он не уходит и в логи не пишется.
    # Пустой ключ = встроенная генерация выключена, учителю остаётся ручной путь
    # (скопировать промт во внешний чат и вставить ответ).
    ai_api_base_url: str = "https://api.aitunnel.ru/v1"
    ai_api_key: str = ""
    # Модель, которая составляет задания.
    ai_model: str = "qwen3.7-plus"
    # Модель самопроверки: решает каждое задание заново. Пусто → ai_model.
    ai_check_model: str = "gpt-5-mini"
    # Уходит в запросы самопроверки как reasoning_effort. Пусто → не передаём.
    ai_check_reasoning_effort: str = "low"
    # max_tokens передаётся в КАЖДОМ запросе: по нему провайдер резервирует
    # стоимость, без него резерв считается по максимуму модели.
    ai_gen_max_tokens: int = 4000
    ai_check_max_tokens: int = 1500
    ai_timeout_seconds: float = 120

    # --- Почта: приглашения учителям и сброс пароля ---
    #
    # SMTP_HOST пуст = тестовый режим: письма никуда не уходят, а пишутся в лог
    # сервера и в журнал писем; ссылки администратор копирует из админки вручную.
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    # Пароль SMTP не пишется ни в логи, ни в журнал писем, ни в ответы API.
    smtp_password: str = ""
    # Адрес отправителя. Пусто → берётся SMTP_USER.
    smtp_from: str = ""
    smtp_from_name: str = "Проверочные работы · Школа 2090"
    # true — шифрование с первой секунды (порт 465). false — обычное соединение
    # с переходом на шифрование командой STARTTLS (порт 587).
    smtp_ssl: bool = True
    # Адрес сайта, как его видит учитель: из него собираются ссылки в письмах.
    public_base_url: str = "http://127.0.0.1:5174"

    # Название и версия — попадают в автодокументацию.
    app_name: str = "Проверочные работы — сервис школы №2090"
    app_version: str = "1.0.0"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # лишние переменные окружения не ломают запуск
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Превращает "a,b" в ["a", "b"] — в таком виде это нужно FastAPI."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def mail_enabled(self) -> bool:
        """Настроена ли отправка писем. Без SMTP_HOST работаем в тестовом режиме."""
        return bool(self.smtp_host.strip())

    @property
    def mail_from(self) -> str:
        return self.smtp_from.strip() or self.smtp_user.strip()

    @property
    def ai_enabled(self) -> bool:
        """Настроена ли встроенная генерация: без ключа запросы делать нечем."""
        return bool(self.ai_api_key.strip())

    @property
    def ai_check_model_name(self) -> str:
        """Модель самопроверки с учётом правила «пусто → основная модель»."""
        return self.ai_check_model.strip() or self.ai_model


@lru_cache
def get_settings() -> Settings:
    """
    Настройки читаются один раз за время жизни процесса.
    lru_cache = «запомни результат первого вызова и возвращай его дальше».
    """
    return Settings()
