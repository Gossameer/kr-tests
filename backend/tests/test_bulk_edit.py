"""
Этап «правка сразу во всех вариантах»: фоновая правка умения ИИ (заглушка),
«Уточнение для ИИ» в умении, копия работы и изменение работы, которую ещё не писали.
"""

import json
import time
import unittest
from unittest import mock

import psycopg

from app import ai_client
from tests.support import (
    Client,
    add_user,
    drop_database,
    migrations,
    ok,
    query,
    scratch_database,
    start_app,
    stub,
)

HINT = "без логарифмов по основанию 10"


def task(number: int, text: str = "", answer: str = "4") -> dict:
    return {
        "skill_index": 1,
        "text": text or f"Вариант {number}: найдите наибольшее целое $x$, при котором $\\log_2(5 - x)$ "
        "определён. Ответ запишите целым числом.",
        "answer_format": "input",
        "accepted_answers": [answer],
        "solution": "$x < 5$",
    }


def body(classes=("10А", "10Б"), variants: int = 3) -> dict:
    return {
        "title": "Логарифмы",
        "subject": "алгебра",
        "classes": list(classes),
        "variants_count": variants,
        "shuffle": False,
        "skills": [
            {"title": "Область определения", "tasks_per_variant": 1, "answer_format": "input", "ai_hint": HINT},
            {"title": "Уравнения", "tasks_per_variant": 1, "answer_format": "choice"},
        ],
        "variants": [
            {
                "variant_no": number,
                "tasks": [
                    task(number),
                    {"skill_index": 2, "text": f"В{number}: корень $\\log_2 x = 3$", "answer_format": "choice",
                     "options": ["8", "6"], "correct": 0},
                ],
            }
            for number in range(1, variants + 1)
        ],
    }


def setUpModule() -> None:
    start_app()
    add_user("Правкина Нина", "bulk@example.org")
    add_user("Чужой Учитель", "bulk-other@example.org")


class CopyAndEdit(unittest.TestCase):
    def setUp(self) -> None:
        self.teacher = Client().login("bulk@example.org")
        status, self.created = self.teacher.post("/api/tests", body())
        self.assertEqual(status, 201, self.created)
        self.test_id = self.created["id"]

    def test_draft_has_hint_tasks_and_added_answers(self) -> None:
        # Правка после работы: добавленный ответ попадает и в копию.
        task_id = query("SELECT id FROM tasks WHERE test_id = %s AND variant_no = 1 AND position = 1",
                        (self.test_id,))[0][0]
        query("UPDATE tasks SET extra_answers = ARRAY['четыре'] WHERE id = %s", (task_id,))

        status, draft = self.teacher.get(f"/api/tests/{self.test_id}/draft")
        self.assertEqual(status, 200, draft)
        self.assertEqual(draft["skills"][0]["ai_hint"], HINT)
        self.assertEqual(len(draft["variants"]), 3)
        first = draft["variants"][0]["tasks"]
        self.assertEqual(first[0]["accepted_answers"], ["4", "четыре"])
        self.assertEqual((first[1]["options"], first[1]["correct"]), (["8", "6"], 0))
        self.assertFalse(draft["has_attempts"])

        # Копия публикуется как новая работа — с теми же умениями и уточнением.
        copy = {key: draft[key] for key in ("subject", "classes", "variants_count", "shuffle", "skills", "variants")}
        status, created = self.teacher.post("/api/tests", {**copy, "title": "Логарифмы (копия)"})
        self.assertEqual(status, 201, created)
        self.assertNotEqual(created["id"], self.test_id)
        self.assertEqual(query("SELECT ai_hint FROM skills WHERE test_id = %s AND position = 1",
                               (created["id"],))[0][0], HINT)

        self.assertEqual(Client().login("bulk-other@example.org").get(f"/api/tests/{self.test_id}/draft")[0], 403)

    def test_edit_until_written(self) -> None:
        links = {link["class_name"]: link["code"] for link in self.created["class_links"]}
        changed = body(classes=("10А", "10В"), variants=2)
        changed["variants"][0]["tasks"][0] = task(1, "Новое задание. Ответ запишите целым числом.", "7")

        status, updated = self.teacher.request("PUT", f"/api/tests/{self.test_id}", changed)
        self.assertEqual(status, 200, updated)
        new_links = {link["class_name"]: link["code"] for link in updated["class_links"]}
        self.assertEqual(new_links["10А"], links["10А"])  # ссылка класса сохранилась
        self.assertIn("10В", new_links)
        self.assertNotIn("10Б", new_links)
        draft = self.teacher.get(f"/api/tests/{self.test_id}/draft")[1]
        self.assertEqual(draft["variants_count"], 2)
        self.assertEqual(draft["variants"][0]["tasks"][0]["accepted_answers"], ["7"])

        # Ученик начал — менять нельзя, только копировать.
        student = Client()
        self.assertEqual(student.post(f"/api/public/tests/{new_links['10А']}/start",
                                      {"student_name": "Ученик Первый"})[0], 201)
        status, data = self.teacher.request("PUT", f"/api/tests/{self.test_id}", changed)
        self.assertEqual(status, 409)
        self.assertIn("Работу уже писали", data["detail"])
        self.assertIn("сделайте копию работы", data["detail"])
        self.assertTrue(self.teacher.get(f"/api/tests/{self.test_id}/draft")[1]["has_attempts"])
        other = Client().login("bulk-other@example.org")
        self.assertEqual(other.request("PUT", f"/api/tests/{self.test_id}", changed)[0], 403)


class BulkJob(unittest.TestCase):
    def setUp(self) -> None:
        self.teacher = Client().login("bulk@example.org")
        patcher = mock.patch.object(ai_client.time, "sleep", lambda _: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def payload(self, mode: str = "fix", order=None, variants=(1, 2, 3)) -> dict:
        source = body()
        return {
            "subject": "алгебра",
            "topic": "логарифмы",
            "grade": "10",
            "skills": source["skills"],
            "variants_count": 3,
            "skill_index": 1,
            "order": order,
            "mode": mode,
            "wish": "в задании 2 вариантов ответ получается бесконечным — нужен конечный",
            "current": [
                {"variant_no": number, "tasks": [
                    {k: v for k, v in task(number).items() if k != "skill_index"}
                ]}
                for number in variants
            ],
        }

    def wait(self, job_id: int) -> dict:
        for _ in range(200):
            status, job = self.teacher.get(f"/api/ai/jobs/{job_id}")
            self.assertEqual(status, 200, job)
            if job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("правка не закончилась")

    def script(self, texts: dict[int, str], review_ok: bool = True):
        def answer(payload, kind):
            if kind == "check":
                return ok("6")
            if kind == "review":
                return ok(json.dumps({"solution_ok": review_ok, "solution_problem": "" if review_ok else "не сходится",
                                      "facts_ok": True, "facts_problem": ""}))
            prompt = payload["messages"][1]["content"]
            asked = [number for number in texts if f"вариант {number}, задание 1:" in prompt]
            return ok(json.dumps({"variants": [
                {"variant": number, "tasks": [{"text": texts[number], "format": "input", "answers": ["6"],
                                               "solution": "$x < 7$, наибольшее целое 6."}]}
                for number in asked
            ]}, ensure_ascii=False))
        return answer

    def test_fix_all_variants(self) -> None:
        texts = {n: f"Вариант {n}: найдите наибольшее целое $x$, при котором $\\log_3(7 - x)$ определён. "
                    "Ответ запишите целым числом." for n in (1, 2, 3)}
        stub.reset(self.script(texts))
        status, started = self.teacher.post("/api/ai/bulk-jobs", self.payload())
        self.assertEqual(status, 202, started)
        job = self.wait(started["job_id"])

        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["kind"], "bulk")
        items = sorted(job["items"], key=lambda item: item["variant_no"])
        self.assertEqual([item["status"] for item in items], ["ok"] * 3)
        self.assertEqual(items[0]["before"]["accepted_answers"], ["4"])
        self.assertEqual(items[0]["after"]["text"], texts[1])
        self.assertEqual(items[0]["order"], 0)
        self.assertFalse(items[0]["after"]["needs_review"], items[0]["after"]["review"])

        prompt = next(p for k, p in stub.requests if k == "generate")["messages"][1]["content"]
        self.assertIn("Поправь эти задания по замечанию учителя", prompt)
        self.assertIn("Замечание учителя: в задании 2 вариантов ответ получается бесконечным", prompt)
        self.assertIn(f"Уточнение учителя к этому умению (выполни обязательно): {HINT}", prompt)
        self.assertIn("вариант 2, задание 1: Вариант 2: найдите наибольшее целое", prompt)
        self.assertIn("ОДНО конечное число", prompt)
        # Самопроверка и проверка решения — у каждого задания.
        kinds = [kind for kind, _ in stub.requests]
        self.assertEqual((kinds.count("check"), kinds.count("review")), (3, 3))

    def test_replace_one_position_flags_bad_and_retries_missing(self) -> None:
        # Первый ответ ИИ вернул только варианты 1 и 2 — вариант 3 повторяется отдельно.
        calls = {"n": 0}
        good = self.script({n: f"Новое задание {n}. Ответ запишите целым числом." for n in (1, 2, 3)},
                           review_ok=False)

        def script(payload, kind):
            if kind == "generate":
                calls["n"] += 1
                if calls["n"] == 1:
                    return ok(json.dumps({"variants": [
                        {"variant": n, "tasks": [{"text": f"Новое задание {n}. Ответ запишите целым числом.",
                                                  "format": "input", "answers": ["6"], "solution": "6"}]}
                        for n in (1, 2)
                    ]}, ensure_ascii=False))
            return good(payload, kind)

        stub.reset(script)
        status, started = self.teacher.post("/api/ai/bulk-jobs", self.payload(mode="replace", order=0))
        self.assertEqual(status, 202)
        job = self.wait(started["job_id"])
        items = sorted(job["items"], key=lambda item: item["variant_no"])
        self.assertEqual([item["status"] for item in items], ["ok"] * 3)
        self.assertEqual(calls["n"], 2)
        retry = [p for k, p in stub.requests if k == "generate"][1]["messages"][1]["content"]
        self.assertIn("Прошлый ответ не подошёл: в ответе нет варианта 3", retry)
        self.assertIn("Составь НОВЫЕ задания на это умение", retry)
        # Проверка решения сказала «не сходится» — задание жёлтое.
        self.assertTrue(items[0]["after"]["needs_review"])
        self.assertIn("решение не сходится с ответом", items[0]["after"]["review"]["warning"])

    def test_failed_variant_and_validation(self) -> None:
        stub.reset(lambda payload, kind: ok("не JSON"))
        job = self.wait(self.teacher.post("/api/ai/bulk-jobs", self.payload(variants=(1,)))[1]["job_id"])
        self.assertEqual(job["items"][0]["status"], "failed")
        self.assertIn("JSON", job["items"][0]["error"])

        short = {**self.payload(), "wish": "  а "}
        self.assertEqual(self.teacher.post("/api/ai/bulk-jobs", short)[0], 422)
        empty = self.payload()
        for variant in empty["current"]:
            variant["tasks"][0]["text"] = ""
        self.assertEqual(self.teacher.post("/api/ai/bulk-jobs", empty)[0], 422)
        self.assertEqual(Client().post("/api/ai/bulk-jobs", self.payload())[0], 401)


class Migration012(unittest.TestCase):
    NAME = "kr_tests_tmp_migration012"

    def test_up_down_up(self) -> None:
        url = scratch_database(self.NAME)
        self.addCleanup(drop_database, self.NAME)
        up = next(path for path in migrations() if path.name.startswith("012"))
        down = up.with_name(up.name.replace(".sql", ".down.sql"))
        with psycopg.connect(url, autocommit=True) as conn:
            for path in migrations(up_to="011"):
                conn.execute(path.read_text(encoding="utf-8"))
            teacher = conn.execute(
                "INSERT INTO users (full_name, email, password_hash) VALUES ('У', 'u@e.org', 'x') RETURNING id"
            ).fetchone()[0]
            conn.execute(up.read_text(encoding="utf-8"))
            conn.execute(up.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO ai_jobs (teacher_id, kind, request) VALUES (%s, 'bulk', '{}')", (teacher,))
            conn.execute(down.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("SELECT count(*) FROM ai_jobs").fetchone()[0], 0)
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO ai_jobs (teacher_id, kind, request) VALUES (%s, 'bulk', '{}')", (teacher,))
            conn.execute(up.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
