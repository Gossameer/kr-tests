"""
Тесты генерации через ИИ: самопроверка, ответ в условии, ударения, сетевые обрывы.

Настоящий ИИ-сервис не трогаем: вместо него — заглушка на 127.0.0.1, которая
отвечает по сценарию теста (обрыв соединения, 429, 503, битый JSON, обрезка).
База — временная: создаётся рядом с рабочей (тот же сервер и пользователь из
DATABASE_URL), в неё накатываются миграции, в конце она удаляется.

Запуск (из папки backend):

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""

import json
import os
import pathlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import psycopg

BACKEND = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------
# Заглушка ИИ-сервиса
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# Временная база и настройки — ДО импорта приложения
# ---------------------------------------------------------------------

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def _real_database_url() -> str:
    if os.environ.get("TEST_DATABASE_SERVER"):
        return os.environ["TEST_DATABASE_SERVER"]
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    return "postgresql://postgres:postgres@localhost:5432/kr_tests"


_base, _, _ = _real_database_url().rpartition("/")
TEMP_DB = f"kr_tests_tmp_{os.getpid()}"

os.environ.update(
    DATABASE_URL=f"{_base}/{TEMP_DB}",
    AI_API_BASE_URL=f"http://127.0.0.1:{server.server_address[1]}/v1",
    AI_API_KEY="stub-key",
    AI_MODEL="stub-gen",
    AI_CHECK_MODEL="stub-check",
    AI_RETRY_PAUSES="2,5,10",
    AI_CHECK_MAX_TOKENS="1500",
    AI_GEN_CHUNK_VARIANTS="4",
)

from app import ai_client, ai_generation as gen, db  # noqa: E402
from app.ai_client import AIError, RequestContext  # noqa: E402

TEACHER_ID = 0


def setUpModule() -> None:
    global TEACHER_ID
    with psycopg.connect(f"{_base}/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{TEMP_DB}"')
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for path in sorted((BACKEND / "migrations").glob("*.sql")):
            if not path.name.endswith(".down.sql"):
                conn.execute(path.read_text(encoding="utf-8"))
        TEACHER_ID = conn.execute(
            "INSERT INTO users (full_name, email, password_hash) "
            "VALUES ('Тест', 'test@example.org', 'x') RETURNING id"
        ).fetchone()[0]


def tearDownModule() -> None:
    server.shutdown()
    if db.pool is not None:
        db.pool.close()
    with psycopg.connect(f"{_base}/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEMP_DB}" WITH (FORCE)')


def journal(kind: str) -> list[dict]:
    with db.get_pool().connection() as conn:
        return conn.execute(
            "SELECT success, error FROM ai_requests WHERE kind = %s ORDER BY id", (kind,)
        ).fetchall()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        # Паузы между повторами не ждём, а записываем: проверяем, что они 2/5/10.
        self.pauses: list[float] = []
        patcher = mock.patch.object(ai_client.time, "sleep", self.pauses.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.get_pool().connection() as conn:
            conn.execute("DELETE FROM ai_requests")
        # Каждый тест начинает с новых соединений: обрыв на соединении из запаса
        # клиент молча повторяет на свежем (см. test_stale_connection_...).
        for connection in ai_client._pool._idle:
            connection.close()
        ai_client._pool._idle.clear()
        self.ctx = RequestContext(teacher_id=TEACHER_ID)


def input_task(text: str, answers: list[str]) -> dict:
    return {
        "skill_index": 1,
        "text": text,
        "answer_format": "input",
        "options": [],
        "correct": None,
        "accepted_answers": answers,
        "solution": "решение",
    }


def choice_task(text: str, options: list[str], correct: int) -> dict:
    return {
        "skill_index": 1,
        "text": text,
        "answer_format": "choice",
        "options": options,
        "correct": correct,
        "accepted_answers": [],
        "solution": "решение",
    }


REQUEST = {"subject": "Математика", "topic": "Проценты", "grade": "6"}


def flaky(failures: list, then) -> callable:
    """Первые запросы получают сбои из списка, остальные — ответ then."""
    left = list(failures)
    lock = threading.Lock()

    def script(payload, kind):
        with lock:
            if left:
                return left.pop(0)
        return then(payload, kind) if callable(then) else then

    return script


# =====================================================================
# 3. Сетевые обрывы
# =====================================================================


class NetworkRetries(Base):
    def chat(self, kind: str = "generate"):
        system = "Ты внимательно решаешь" if kind == "check" else "Ты составляешь"
        return ai_client.chat(
            self.ctx,
            kind=kind,
            model="stub",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": "?"}],
            max_tokens=100,
        )

    def test_drops_are_retried_with_2_5_10(self) -> None:
        stub.reset(flaky([DROP, DROP, DROP], ok("готово")))
        with self.assertLogs("app.ai_client", "WARNING") as logs:
            result = self.chat()
        self.assertEqual(result.text, "готово")
        self.assertEqual(self.pauses, [2, 5, 10])
        self.assertIn("повтор 1 из 3 через 2 с", "\n".join(logs.output))
        rows = journal("generate")
        self.assertEqual([row["success"] for row in rows], [False, False, False, True])
        self.assertIn("обрыв", rows[0]["error"])

    def test_429_and_5xx_are_retried_for_check_too(self) -> None:
        stub.reset(flaky([http_error(429), http_error(503), http_error(500)], ok("25")))
        result = self.chat("check")
        self.assertEqual(result.text, "25")
        self.assertEqual(self.pauses, [2, 5, 10])
        self.assertIn("429", journal("check")[0]["error"])
        self.assertIn("503", journal("check")[1]["error"])

    def test_gives_up_only_after_three_retries(self) -> None:
        stub.reset(lambda payload, kind: DROP)
        with self.assertRaises(AIError) as caught, self.assertLogs("app.ai_client", "WARNING"):
            self.chat()
        self.assertEqual(self.pauses, [2, 5, 10])
        self.assertTrue(caught.exception.exhausted)
        self.assertEqual(caught.exception.short, "нет связи с ИИ-сервисом")
        self.assertEqual(len(journal("generate")), 4)

    def test_stale_connection_is_retried_silently(self) -> None:
        stub.reset(lambda payload, kind: ok("раз"))
        self.chat()  # соединение уходит в запас
        stub.reset(flaky([DROP], ok("два")))
        self.assertEqual(self.chat().text, "два")
        self.assertEqual(self.pauses, [])
        self.assertEqual([row["success"] for row in journal("generate")], [True, True])

    def test_bad_key_is_not_retried(self) -> None:
        stub.reset(lambda payload, kind: http_error(401))
        with self.assertRaises(AIError) as caught:
            self.chat()
        self.assertTrue(caught.exception.fatal)
        self.assertEqual(self.pauses, [])
        self.assertNotIn("stub-key", str(caught.exception))


# =====================================================================
# 1. Самопроверка
# =====================================================================


class SelfCheck(Base):
    task = input_task("Найдите 25 % от 100.", ["25"])

    def check(self, task: dict | None = None) -> dict:
        return gen.self_check(self.ctx, REQUEST, task or self.task, threading.Event(), "тест")

    def test_survives_ssl_drop(self) -> None:
        """Тот самый случай из лога: обрыв на самопроверке — раньше сразу «не удалось»."""
        stub.reset(flaky([DROP], ok("Ответ: 25.")))
        with self.assertLogs("app.ai_client", "WARNING"):
            review = self.check()
        self.assertEqual(review["status"], "ok")
        self.assertEqual(self.pauses, [2])

    def test_reason_is_short_and_clear_when_network_is_down(self) -> None:
        stub.reset(lambda payload, kind: DROP)
        with self.assertLogs("app", "WARNING"):
            review = self.check()
        self.assertEqual(review["status"], "unchecked")
        self.assertEqual(review["checked"], "нет связи с ИИ-сервисом")
        self.assertEqual(self.pauses, [2, 5, 10])
        self.assertTrue(gen.attach_review(self.task, review)["needs_review"])

    def test_reason_for_overload(self) -> None:
        stub.reset(lambda payload, kind: http_error(429))
        with self.assertLogs("app", "WARNING"):
            review = self.check()
        self.assertEqual(review["checked"], "ИИ-сервис перегружен запросами")

    def test_limit_spent_on_reasoning_is_retried_with_double_limit(self) -> None:
        def script(payload, kind):
            if payload["max_tokens"] < 3000:
                return ok("", finish="length", tokens=1500)
            return ok("25")

        stub.reset(script)
        with self.assertLogs("app", "WARNING"):
            review = self.check()
        self.assertEqual(review["status"], "ok")
        self.assertEqual(stub.requests[-1][1]["max_tokens"], 3000)

    def test_limit_spent_twice_gives_reason(self) -> None:
        stub.reset(lambda payload, kind: ok("", finish="length", tokens=1500))
        with self.assertLogs("app", "WARNING"):
            review = self.check()
        self.assertEqual(review["status"], "unchecked")
        self.assertEqual(review["checked"], "лимит токенов ушёл на размышления")

    def test_mismatch(self) -> None:
        stub.reset(lambda payload, kind: ok("20"))
        review = self.check()
        self.assertEqual(review["status"], "mismatch")
        self.assertEqual(review["checked"], "20")

    def test_prompt_has_no_answer(self) -> None:
        task = choice_task("Столица Франции?", ["Лион", "Париж", "Ницца", "Марсель"], 1)
        stub.reset(lambda payload, kind: ok("2"))
        self.assertEqual(self.check(task)["status"], "ok")
        self.assertNotIn("correct", json.dumps(stub.requests[-1][1], ensure_ascii=False))

    def test_stress_written_with_accent_mark_matches(self) -> None:
        task = input_task("Поставьте ударение в слове: звонит", ["звонИт"])
        stub.reset(lambda payload, kind: ok("звони́т"))
        self.assertEqual(self.check(task)["status"], "ok")
        stub.reset(lambda payload, kind: ok("звОнит"))
        self.assertEqual(self.check(task)["status"], "mismatch")


# =====================================================================
# 2. Задания, которые нельзя проверить вводом
# =====================================================================

OK_REVIEW = {"status": "ok", "generated": "", "checked": ""}


class AnswerInText(unittest.TestCase):
    def test_stress_answer_shown_in_text(self) -> None:
        task = input_task("Поставьте ударение в слове: звонИт", ["звонИт"])
        checked = gen.attach_review(task, OK_REVIEW)
        self.assertTrue(checked["needs_review"])
        self.assertIn("ответ виден в условии", checked["review"]["warning"])
        self.assertIn("переключите умение на «выбор»", checked["review"]["warning"])

    def test_stress_word_in_lowercase_is_not_the_answer(self) -> None:
        task = input_task("Поставьте ударение в слове: звонит", ["звонИт"])
        self.assertFalse(gen.answer_visible(task))
        # Но вводом такое всё равно не проверить: регистр не учитывается.
        self.assertTrue(gen.attach_review(task, OK_REVIEW)["needs_review"])

    def test_normalized_answer_in_text(self) -> None:
        task = input_task("Как называется ЁЖИК, если это ёжик?", ["  Ежик "])
        self.assertTrue(gen.answer_visible(task))

    def test_part_of_word_and_numbers_are_fine(self) -> None:
        self.assertFalse(gen.answer_visible(input_task("Назовите столицу России.", ["сто"])))
        self.assertFalse(gen.answer_visible(input_task("Найдите 25 % от 100.", ["25"])))
        clean = gen.attach_review(input_task("Столица Франции?", ["Париж"]), OK_REVIEW)
        self.assertFalse(clean["needs_review"])

    def test_choice(self) -> None:
        options = ["звОнит", "звонИт"]
        self.assertTrue(gen.answer_visible(choice_task("Где ударение: звонИт?", options, 1)))
        self.assertFalse(gen.answer_visible(choice_task("Где ударение: звонит?", options, 1)))
        # Все варианты перечислены в условии — это обычная формулировка.
        both = choice_task("Как правильно: звОнит или звонИт?", options, 1)
        self.assertFalse(gen.answer_visible(both))
        self.assertFalse(gen.attach_review(both, OK_REVIEW)["needs_review"])


class Prompts(unittest.TestCase):
    def request(self, title: str, answer_format: str) -> dict:
        skill = {"title": title, "tasks_per_variant": 1, "answer_format": answer_format}
        return {**REQUEST, "skills": [skill], "variants_count": 2}

    def test_never_put_answer_in_text(self) -> None:
        request = self.request("Находить процент от числа", "input")
        prompt = gen.skill_messages(request, 1, [1, 2], [], "")[1]["content"]
        self.assertIn("НИКОГДА не включай ответ в текст задания", prompt)
        self.assertNotIn("ударени", prompt.replace("ударной буквой", ""))
        single = {**request, "skill_index": 1, "variant_no": 1}
        self.assertIn("НИКОГДА не включай ответ", gen.task_messages(single, "")[1]["content"])

    def test_stress_choice_asks_for_one_word_with_different_stress(self) -> None:
        request = self.request("Ставить ударение в словах", "choice")
        prompt = gen.skill_messages(request, 1, [1, 2], [], "")[1]["content"]
        self.assertIn("ОДНО И ТО ЖЕ слово с ударением на разных слогах", prompt)
        self.assertIn("от 2 до 4 вариантов", prompt)
        self.assertNotIn("4 варианта ответа", prompt)

    def test_stress_skill_detection(self) -> None:
        self.assertTrue(gen.is_stress_skill("Ставить ударение"))
        self.assertTrue(gen.is_stress_skill("Орфоэпические нормы"))
        self.assertFalse(gen.is_stress_skill("Находить дискриминант"))


# =====================================================================
# Вся генерация на заглушке: повторы с причиной в логе, клетки, пометки
# =====================================================================


def variants(*items: tuple[int, list[dict]]) -> str:
    return json.dumps(
        {"variants": [{"variant": number, "tasks": tasks} for number, tasks in items]},
        ensure_ascii=False,
    )


def stress(word_lower: str, options: list[str], correct: int, text: str = "") -> dict:
    return {
        "text": text or f"Укажите верное ударение в слове «{word_lower}».",
        "format": "choice",
        "options": options,
        "correct": correct,
        "solution": "по словарю",
    }


class WholeJob(Base):
    def run_job(self, skills: list[dict], variants_count: int) -> dict:
        request = {**REQUEST, "skills": skills, "variants_count": variants_count}
        with db.get_pool().connection() as conn:
            job_id = conn.execute(
                "INSERT INTO ai_jobs (teacher_id, kind, status, request, result) "
                "VALUES (%s, 'test', 'running', %s, %s) RETURNING id",
                (
                    TEACHER_ID,
                    psycopg.types.json.Jsonb(request),
                    psycopg.types.json.Jsonb(gen.initial_result(variants_count, len(skills))),
                ),
            ).fetchone()["id"]
        gen.run_test_job(job_id, TEACHER_ID)
        return gen.job_view(gen.load_job_row(job_id))

    def test_retry_reasons_are_logged_and_job_completes(self) -> None:
        good_1 = stress("звонит", ["звОнит", "звонИт"], 1)
        good_2 = stress("торты", ["тОрты", "тортЫ"], 0)
        shown = stress("каталог", ["кАталог", "катАлог", "каталОг"], 2, "Ударение: каталОг?")
        answers = [
            ok("Вот задания: {\"variants\": [", tokens=20),  # битый JSON
            ok(variants((1, [good_1])), tokens=120),  # нет вариантов 2 и 3
            ok(variants((2, [good_2]), (3, [shown]))[:-40], finish="length", tokens=4000),
            DROP,  # обрыв на соединении из запаса — молча повторяется на свежем
            ok(variants((2, [good_2]))),
            ok(variants((3, [shown]))),
        ]

        def script(payload, kind):
            if kind == "generate":
                return answers.pop(0)
            # Самопроверка: модель «решает» — всегда выбирает последний вариант.
            return ok(str(payload["messages"][1]["content"].count(") ")))

        stub.reset(script)
        skills = [{"title": "Ставить ударение", "tasks_per_variant": 1, "answer_format": "choice"}]
        with self.assertLogs("app", "INFO") as logs:
            job = self.run_job(skills, 3)
        output = "\n".join(logs.output)

        self.assertEqual((job["status"], job["done"], job["failed"]), ("done", 3, 0))
        # Почему генерация уходила в повтор — каждая причина названа.
        self.assertIn("ответ не является корректным JSON", output)
        self.assertIn("Повтор 1 из 2", output)
        self.assertIn("в ответе нет варианта 2", output)
        self.assertIn("Ответ оборван по лимиту AI_GEN_MAX_TOKENS=4000 (4000 токенов)", output)
        self.assertIn("Просим варианты по частям", output)
        self.assertIn("делим запрос пополам", output)

        tasks = {cell["variant_no"]: cell["tasks"][0] for cell in job["cells"]}
        # Вариант 1: ответ «звонИт» — второй из двух, самопроверка сошлась.
        self.assertEqual(tasks[1]["review"]["status"], "ok")
        self.assertFalse(tasks[1]["needs_review"])
        # Вариант 2: самопроверка выбрала другой вариант.
        self.assertEqual(tasks[2]["review"]["status"], "mismatch")
        self.assertTrue(tasks[2]["needs_review"])
        # Вариант 3: самопроверка сошлась, но ответ написан прямо в условии.
        self.assertEqual(tasks[3]["review"]["status"], "ok")
        self.assertEqual(tasks[3]["review"]["warning"], "ответ виден в условии")
        self.assertTrue(tasks[3]["needs_review"])
        self.assertEqual(job["needs_review"], 2)

    def test_network_down_fails_cells_with_clear_reason(self) -> None:
        stub.reset(lambda payload, kind: DROP)
        skills = [{"title": "Проценты", "tasks_per_variant": 1, "answer_format": "input"}]
        with self.assertLogs("app", "WARNING") as logs:
            job = self.run_job(skills, 1)
        self.assertEqual(job["failed"], 1)
        self.assertEqual(job["cells"][0]["error"], "ИИ-сервис недоступен. Попробуйте позже.")
        # Один заход с тремя повторами связи, а не 3 × 4.
        self.assertEqual(self.pauses, [2, 5, 10])
        self.assertIn("Повторов больше не будет", "\n".join(logs.output))

    def test_check_down_keeps_tasks_with_reason(self) -> None:
        task = {"text": "Найдите 10 % от 50.", "format": "input", "answers": ["5"], "solution": "5"}
        stub.reset(
            lambda payload, kind: ok(variants((1, [task]))) if kind == "generate" else DROP
        )
        skills = [{"title": "Проценты", "tasks_per_variant": 1, "answer_format": "input"}]
        with self.assertLogs("app", "WARNING"):
            job = self.run_job(skills, 1)
        review = job["cells"][0]["tasks"][0]["review"]
        self.assertEqual(job["done"], 1)
        self.assertEqual(review["status"], "unchecked")
        self.assertEqual(review["checked"], "нет связи с ИИ-сервисом")


if __name__ == "__main__":
    unittest.main()
