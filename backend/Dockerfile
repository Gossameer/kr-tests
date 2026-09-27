# =====================================================================
# Образ бэкенда: FastAPI под uvicorn.
#
# Сборка:  docker build -t kr-backend ./backend
# Запуск:  docker run --env-file env/kr.env -p 8000:8000 kr-backend
#
# Настроек внутри образа нет: DATABASE_URL, COOKIE_SECURE и остальное
# приходят переменными окружения. Один и тот же образ годится и для проверки,
# и для боевого сервера.
# =====================================================================

FROM python:3.12-slim

# Переменные для Python:
#   DONTWRITEBYTECODE — не плодить .pyc в контейнере;
#   UNBUFFERED — логи идут в docker logs сразу, а не после накопления буфера.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости ставим отдельным слоем: пока requirements.txt не менялся,
# при пересборке этот слой берётся из кеша и установка не повторяется.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения и миграции. Миграции нужны в образе, чтобы накатывать их
# командой прямо в контейнере (см. deploy/DEPLOY.md).
COPY app ./app
COPY migrations ./migrations

# Работаем не от root: если кто-то найдёт дыру в приложении, у него не будет
# прав менять систему внутри контейнера.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Проверка живости: Caddy и compose по ней понимают, что сервис поднялся.
# Используем сам Python — curl в slim-образе нет, а ставить его незачем.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Без --reload: перезагрузчик нужен только при разработке, в продакшене он
# лишний процесс и лишний риск.
# --proxy-headers + --forwarded-allow-ips: за Caddy настоящий адрес клиента
# приходит в X-Forwarded-For, и uvicorn должен его прочитать — иначе все ученики
# выглядели бы как один адрес прокси и ограничение входа било бы по всей школе.
#
# Звёздочка = доверять заголовку от любого отправителя. Здесь это безопасно
# ровно потому, что порт 8000 наружу не проброшен: обратиться к контейнеру
# может только edge-caddy из внутренней docker-сети. Если когда-нибудь решите
# пробросить порт наружу — уберите эту строку, иначе адрес можно будет подделать.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-server-header"]
