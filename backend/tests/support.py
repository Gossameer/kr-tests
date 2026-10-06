"""
Общая обвязка тестов: временная база, заглушка ИИ-сервиса, свой сервер приложения.

Настоящие сервисы не трогаем:
  * база — временная: создаётся рядом с рабочей (тот же сервер и пользователь из
    DATABASE_URL), в неё накатываются все миграции, по окончании она удаляется;
  * ИИ — заглушка на 127.0.0.1, отвечает по сценарию теста;
  * почта выключена (SMTP_HOST пуст) — письма никуда не уходят;
  * приложение — свой uvicorn на свободном порту, не 8000.

Модуль настраивает окружение ПРИ ИМПОРТЕ, до первого импорта приложения:
настройки читаются один раз, и второй модуль тестов уже не смог бы их поменять.
"""

import atexit
import http.cookiejar
import json
import os
import pathlib
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = "Passw0rd!x"


# ---------------------------------------------------------------------
# Заглушка ИИ-сервиса
# ---------------------------------------------------------------------


def ok(text: str, finish: str = "stop", tokens: int = 50) -> tuple:
    return (
        200,
        {
            "choices": [{"finish_reason": finish, "message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": tokens},
        },
    )


DROP = ("drop",)


def http_error(status: int) -> tuple:
    return (status, {"error": {"message": f"stub {status}"}})


class Stub:
    """Сценарий заглушки: функция «запрос → действие» и счётчик запросов."""

    def __init__(self) -> None:
        self.script = lambda payload, kind: ok("")
        self.requests: list[tuple[str, dict]] = []
        self.lock = threading.Lock()

    def reset(self, script) -> None:
        with self.lock:
            self.script = script
            self.requests = []

    def count(self, kind: str) -> int:
        with self.lock:
            return sum(1 for item, _ in self.requests if item == kind)


stub = Stub()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = payload["messages"][0]["content"]
        kind = "check" if "решаешь" in system else "generate"
        with stub.lock:
            stub.requests.append((kind, payload))
            script = stub.script
        action = script(payload, kind)

        if action == DROP:
            # Обрыв без ответа — как SSL EOF или сброс соединения.
            self.close_connection = True
            self.connection.close()
            return
        status, body = action
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


ai_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=ai_server.serve_forever, daemon=True).start()


# ---------------------------------------------------------------------
# Временная база и настройки — ДО импорта приложения
# ---------------------------------------------------------------------


def _real_database_url() -> str:
    if os.environ.get("TEST_DATABASE_SERVER"):
        return os.environ["TEST_DATABASE_SERVER"]
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    return "postgresql://postgres:postgres@localhost:5432/kr_tests"


_base, _, _ = _real_database_url().rpartition("/")
TEMP_DB = f"kr_tests_tmp_{os.getpid()}"
DATABASE_URL = f"{_base}/{TEMP_DB}"

os.environ.update(
    DATABASE_URL=DATABASE_URL,
    AI_API_BASE_URL=f"http://127.0.0.1:{ai_server.server_address[1]}/v1",
    AI_API_KEY="stub-key",
    AI_MODEL="stub-gen",
    AI_CHECK_MODEL="stub-check",
    AI_RETRY_PAUSES="2,5,10",
    AI_CHECK_MAX_TOKENS="1500",
    AI_GEN_CHUNK_VARIANTS="4",
    SMTP_HOST="",
    COOKIE_SECURE="false",
    TRUST_PROXY="false",
)

_db_ready = False
_lock = threading.Lock()


def migrations(up_to: str = "") -> list[pathlib.Path]:
    """Файлы наката по порядку; up_to="009" — только до этой миграции включительно."""
    files = sorted(
        path for path in (BACKEND / "migrations").glob("*.sql") if not path.name.endswith(".down.sql")
    )
    return [path for path in files if not up_to or path.name[:3] <= up_to]


def setup_database() -> None:
    """Создаёт временную базу со всеми миграциями (один раз на запуск тестов)."""
    global _db_ready
    with _lock:
        if _db_ready:
            return
        with psycopg.connect(f"{_base}/postgres", autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{TEMP_DB}"')
        atexit.register(drop_database)
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            for path in migrations():
                conn.execute(path.read_text(encoding="utf-8"))
        _db_ready = True


def drop_database(name: str = TEMP_DB) -> None:
    try:
        from app import db

        if name == TEMP_DB and db.pool is not None:
            db.pool.close()
            db.pool = None
    except Exception:  # noqa: BLE001
        pass
    with psycopg.connect(f"{_base}/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def scratch_database(name: str) -> str:
    """Пустая база с заданным именем (для проверки самих миграций). Возвращает её адрес."""
    with psycopg.connect(f"{_base}/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    return f"{_base}/{name}"


def add_user(full_name: str, email: str, role: str = "teacher") -> int:
    """Учётная запись с паролем PASSWORD. Возвращает её id."""
    from app.security import hash_password

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO users (full_name, email, password_hash, role) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (full_name, email, _password_hash(hash_password), role),
        ).fetchone()[0]


_hash_cache: list[str] = []


def _password_hash(hash_password) -> str:
    # bcrypt нарочно медленный — считаем хеш один раз на все учётки тестов.
    if not _hash_cache:
        _hash_cache.append(hash_password(PASSWORD))
    return _hash_cache[0]


def query(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


# ---------------------------------------------------------------------
# Свой сервер приложения и клиент к нему
# ---------------------------------------------------------------------

_app_url = ""


def start_app() -> str:
    """Запускает приложение на свободном порту (один раз). Возвращает адрес."""
    global _app_url
    setup_database()
    with _lock:
        if _app_url:
            return _app_url
        import uvicorn

        from app.main import app

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("приложение не запустилось")
        atexit.register(lambda: setattr(server, "should_exit", True))
        _app_url = f"http://127.0.0.1:{port}"
        return _app_url


class Client:
    """Простой HTTP-клиент со своими cookie: один клиент — один браузер."""

    def __init__(self) -> None:
        self.base = start_app()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method: str, path: str, body=None, raw: bool = False):
        """Возвращает (код ответа, разобранный JSON или байты)."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
            error.close()
        if raw:
            return status, payload
        try:
            return status, json.loads(payload) if payload else None
        except ValueError:
            return status, payload

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body=None):
        return self.request("POST", path, {} if body is None else body)

    def login(self, email: str) -> "Client":
        status, data = self.post("/api/auth/login", {"email": email, "password": PASSWORD})
        assert status == 200, (status, data)
        return self
