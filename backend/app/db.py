"""
Подключение к PostgreSQL.

Почему psycopg 3 (пакет `psycopg`), а не что-то другое:

* psycopg2 — самый старый и популярный драйвер, но он в режиме поддержки:
  новых возможностей не будет, а на Windows иногда просит компилятор.
* asyncpg — очень быстрый, но только асинхронный и с непривычным API
  (например, плейсхолдеры $1 вместо %s). Новичку легко запутаться.
* SQLAlchemy — это ORM: мощно, но добавляет целый слой абстракций,
  а на старте проекта полезнее видеть обычный SQL.

psycopg 3 — прямой наследник psycopg2 от тех же авторов: привычный SQL,
готовые бинарные колёса под Windows (`psycopg[binary]`, компилятор не нужен),
встроенный пул соединений и возможность позже перейти на async без смены драйвера.

Пул соединений (ConnectionPool) держит несколько открытых коннектов и выдаёт их
запросам. Это быстрее, чем открывать новое соединение на каждый HTTP-запрос.
"""

import logging

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)

# Пул создаётся при старте приложения (см. app/main.py) и кладётся сюда.
# None означает «база недоступна / ещё не подключились».
pool: ConnectionPool | None = None


def init_pool() -> ConnectionPool:
    """
    Создаёт пул соединений и сразу проверяет, что база отвечает.

    Бросает исключение, если подключиться не удалось, — вызывающий код
    (app/main.py) решает, падать или продолжить работу без базы.
    """
    global pool

    settings = get_settings()
    new_pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=1,          # держим хотя бы одно готовое соединение
        max_size=10,         # больше десяти одновременно не открываем
        open=False,          # откроем вручную ниже, чтобы поймать ошибку
        kwargs={"row_factory": dict_row},  # строки приходят как словари: row["title"]
    )
    # timeout — сколько секунд ждать базу, прежде чем признать её недоступной.
    new_pool.open(wait=True, timeout=5)

    pool = new_pool
    return new_pool


def close_pool() -> None:
    """Аккуратно закрывает все соединения при остановке приложения."""
    global pool

    if pool is not None:
        pool.close()
        pool = None


def check_connection() -> bool:
    """
    Быстрая проверка живости базы: выполняем самый простой запрос.
    Используется эндпоинтом /health.
    """
    if pool is None:
        return False

    try:
        # `with pool.connection()` берёт соединение из пула и возвращает обратно.
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:  # noqa: BLE001 — для health-check любая ошибка = «база недоступна»
        logger.warning("Проверка соединения с базой не прошла", exc_info=True)
        return False
