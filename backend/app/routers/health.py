"""
Служебный роутер: проверка, что сервис жив и видит базу.

Используется и фронтендом (страница-заглушка), и позже — мониторингом/деплоем.
"""

from fastapi import APIRouter

from app import db

# prefix="" — эндпоинт будет доступен как GET /health
# tags — группировка в автодокументации /docs
router = APIRouter(tags=["service"])


@router.get("/health")
def health() -> dict[str, str]:
    """
    Отвечает {"status": "ok"}.

    Дополнительно показывает состояние базы: "up" или "down".
    Сам эндпоинт отвечает 200 даже при недоступной базе — так фронтенд
    может отличить «бэкенд не запущен» от «бэкенд есть, база лежит».
    """
    return {
        "status": "ok",
        "database": "up" if db.check_connection() else "down",
    }
