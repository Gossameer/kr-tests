"""
Качество генерации: автоисправление заданий, два уровня пометок, решение без
черновиков, размышления модели. ИИ — заглушка; ошибки — настоящие, из тестовой
работы по логарифмам (qwen3.7-plus с выключенными размышлениями).
"""

import json
import threading
import unittest
from unittest import mock

from app import ai_client
from app import ai_generation as gen
from app.ai_client import RequestContext
from app.config import get_settings
from tests.support import DROP, add_user, ok, setup_database, stub

REQUEST = {
    "subject": "алгебра",
    "topic": "логарифмы",
    "grade": "10",
    "skills": [{"title": "Решать уравнения, сводящиеся к квадратным", "tasks_per_variant": 1,
                "answer_format": "input"}],
    "variants_count": 1,
}

# Из жизни: «дано 36, верно 30» — и задание ушло ученикам.
WRONG = {
    "skill_index": 1,
    "text": "Найдите сумму корней уравнения $\\log_3^2 x - 4\\log_3 x + 3 = 0$. Ответ запишите целым числом.",
    "answer_format": "input",
    "options": [],
    "correct": None,
    "accepted_answers": ["36"],
    "solution": "$t = \\log_3 x$, $t^2 - 4t + 3 = 0$, $t = 1$ или $t = 3$, $x = 3$ или $x = 27$, сумма 36.",
}
FIXED = {
    "text": WRONG["text"],
    "format": "input",
    "answers": ["30"],
    "solution": "$t = \\log_3 x$, $t^2 - 4t + 3 = 0$, $t_1 = 1$, $t_2 = 3$; $x = 3$ и $x = 27$; сумма 30.",
}


def review_json(ok_: bool, answer: str, problem: str = "") -> str:
    return json.dumps({"solution_ok": ok_, "solution_problem": problem, "facts_ok": True,
                       "facts_problem": "", "correct_answer": answer}, ensure_ascii=False)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        setup_database()
        cls.ctx = RequestContext(teacher_id=add_user("Исправлялкин Иван", f"autofix{id(cls)}@example.org"))

    def setUp(self) -> None:
        for name, value in (("ai_autofix_attempts", 2), ("ai_gen_thinking", "on")):
            patcher = mock.patch.object(get_settings(), name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ai_client.time, "sleep", lambda _: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_fix(self, task: dict, script) -> dict:
        stub.reset(script)
        return gen.check_and_fix(self.ctx, REQUEST, 1, task, threading.Event(), "тест")


class AutoFix(Base):
    def test_wrong_answer_is_fixed_with_checker_hint(self) -> None:
        def script(payload, kind):
            prompt = payload["messages"][1]["content"]
            if kind == "check":
                return ok("30")
            if kind == "review":
                return ok(review_json("«36»" not in prompt and "Правильный ответ: 30" in prompt, "30",
                                      "сумма 3 + 27 = 30, а не 36"))
            return ok(json.dumps(FIXED, ensure_ascii=False))

        result = self.run_fix(WRONG, script)
        self.assertFalse(result["needs_review"], result["review"])
        self.assertEqual(result["accepted_answers"], ["30"])
        self.assertEqual(result["review"]["autofix"]["attempts"], 1)
        self.assertTrue(result["review"]["autofix"]["fixed"])
        self.assertIn("исправлено автоматически", result["review"]["note"])
        self.assertEqual(result["review"]["warning"], "")

        kinds = [kind for kind, _ in stub.requests]
        self.assertEqual(kinds, ["check", "review", "generate", "check", "review"])
        fix_prompt = stub.requests[2][1]["messages"][1]["content"]
        self.assertIn("не прошло проверку", fix_prompt)
        self.assertIn("получили «30», а в задании указан ответ «36»", fix_prompt)
        self.assertIn("Две независимые проверки решили это задание и обе получили ответ «30»", fix_prompt)
        self.assertIn('"answers": ["36"]', fix_prompt)
        # Размышления включены — у генерации запас токенов, параметр «не размышлять» не уходит.
        payload = stub.requests[2][1]
        self.assertEqual(payload["max_tokens"], get_settings().ai_gen_max_tokens + get_settings().ai_gen_thinking_tokens)
        self.assertNotIn("reasoning", payload)

    def test_not_fixed_stays_yellow_and_keeps_best(self) -> None:
        def script(payload, kind):
            if kind == "check":
                return ok("30")
            if kind == "review":
                return ok(review_json(False, "30", "не сходится"))
            return ok(json.dumps({**FIXED, "answers": ["36"]}, ensure_ascii=False))

        result = self.run_fix(WRONG, script)
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["review"]["autofix"], {
            "attempts": 2, "fixed": False, "reason": result["review"]["autofix"]["reason"]})
        self.assertNotIn("исправлено автоматически", result["review"].get("note", ""))
        self.assertIn("решение не сходится", result["review"]["warning"])
        self.assertEqual([k for k, _ in stub.requests].count("generate"), 2)

    def test_draft_solution_triggers_fix_even_if_answer_matches(self) -> None:
        # Из жизни: «Ошибка в условии предыдущего шага… Исправляю задачу на более простую…»
        drafty = {**WRONG, "accepted_answers": ["30"],
                  "solution": "Ошибка в условии предыдущего шага. Исправляю задачу на более простую: … сумма 30."}

        def script(payload, kind):
            if kind == "check":
                return ok("30")
            if kind == "review":
                return ok(review_json(True, "30"))
            return ok(json.dumps(FIXED, ensure_ascii=False))

        result = self.run_fix(drafty, script)
        self.assertFalse(result["needs_review"])
        self.assertTrue(result["review"]["autofix"]["fixed"])
        self.assertIn("черновые рассуждения", result["review"]["autofix"]["reason"])
        self.assertNotIn("Исправляю", result["solution"])

    def test_reviewer_disagreeing_is_serious_even_if_solver_agreed(self) -> None:
        # Первый шаг случайно «согласился» с ошибкой, проверка решения посчитала сама.
        def script(payload, kind):
            if kind == "check":
                return ok("36")
            if kind == "review":
                return ok(review_json(True, "30"))
            return ok(json.dumps(FIXED, ensure_ascii=False))

        with mock.patch.object(get_settings(), "ai_autofix_attempts", 0):
            result = self.run_fix(WRONG, script)
        self.assertTrue(result["needs_review"])
        self.assertIn("проверка решения дала другой ответ: 30", result["review"]["warning"])

    def test_no_fix_when_check_failed(self) -> None:
        result = self.run_fix(WRONG, lambda payload, kind: DROP)
        self.assertEqual(result["review"]["status"], "unchecked")
        self.assertTrue(result["needs_review"])
        self.assertNotIn("autofix", result["review"])
        self.assertNotIn("generate", [kind for kind, _ in stub.requests])

    def test_choice_hint(self) -> None:
        # Из жизни: «какое выражение наибольшее» — отмечен log_10 100, верно log_2 8.
        task = {"skill_index": 1, "text": "Какое выражение наибольшее?", "answer_format": "choice",
                "options": ["$\\log_2 8$", "$\\log_3 9$", "$\\log_5 25$", "$\\log_{10} 100$"], "correct": 3,
                "accepted_answers": [], "solution": "Все равны 2… наибольшее $\\log_{10} 100$."}
        checked = gen.attach_review(task, {"status": "mismatch", "generated": "4) $\\log_{10} 100$",
                                           "checked": "1) $\\log_2 8$", "reviewer_answer": "1"}, "алгебра")
        self.assertIn("обе получили ответ «1»", gen.checker_hint(checked))


class Duplicates(unittest.TestCase):
    def test_choice_with_same_question_but_other_options_is_not_a_repeat(self) -> None:
        # Из жизни: третий вариант «какое выражение наибольшее» отбрасывался как повтор.
        raw = json.dumps({"variants": [
            {"variant": n, "tasks": [{"text": "Какое выражение наибольшее?", "format": "choice",
                                      "options": [f"$\\log_2 {n + 4}$", "$\\log_3 8$", "$\\log_4 15$"],
                                      "correct": 0, "solution": "сравним с 2"}]}
            for n in (1, 2, 3)
        ]}, ensure_ascii=False)
        skill = {"title": "Какое выражение наибольшее", "tasks_per_variant": 1, "answer_format": "choice"}
        ready, problems = gen.parse_skill_answer(raw, skill, 1, [1, 2, 3], set())
        self.assertEqual((sorted(ready), problems), ([1, 2, 3], {}))
        # А совсем одинаковое задание — повтор.
        same = raw.replace("log_2 5", "log_2 6")
        ready, problems = gen.parse_skill_answer(same, skill, 1, [1, 2, 3], set())
        self.assertIn("повторяет", " ".join(problems.values()))


class Levels(unittest.TestCase):
    def check(self, task: dict, review=None) -> dict:
        return gen.attach_review(task, review or {"status": "ok", "generated": "", "checked": ""}, "алгебра")

    def test_serious_and_minor(self) -> None:
        base = {**WRONG, "accepted_answers": ["30"], "solution": "сумма 30"}
        self.assertFalse(self.check(base)["needs_review"])

        # Подпись о формате дописал сервер — серое замечание, не в счётчике.
        hinted = gen.add_format_hint({**base, "text": "Найдите сумму корней уравнения $\\log_3^2 x - 4\\log_3 x + 3 = 0$"})
        checked = self.check(hinted)
        self.assertFalse(checked["needs_review"])
        self.assertEqual(checked["review"]["note"], "подпись о формате ответа дописана сервером")

        for minor in ({"text": "Найдите корень."}, {"solution": "Пересчёт: 30."}):
            checked = self.check({**base, **minor})
            self.assertFalse(checked["needs_review"], checked["review"])
            self.assertNotEqual(checked["review"]["note"], "")

        for serious in (
            {"text": "Найдите корень. Ответ 30. Ответ запишите числом.", "accepted_answers": ["тридцать"]},
            {"text": "На рисунке график. Ответ запишите числом."},
            {"accepted_answers": ["(-∞; 5)"]},
        ):
            checked = self.check({**base, **serious})
            self.assertTrue(checked["needs_review"], serious)
            self.assertNotEqual(checked["review"]["warning"], "")

        mismatch = self.check(base, {"status": "mismatch", "generated": "30", "checked": "36"})
        self.assertTrue(mismatch["needs_review"])

    def test_prompt_demands_clean_solution(self) -> None:
        prompt = gen.skill_messages(REQUEST, 1, [1], [], "")[1]["content"]
        self.assertIn("решение в 1–3 строки", prompt)
        self.assertIn("«пересчёт», «ошибка», «исправляю», «проверим ещё раз»", prompt)
        for draft in ("Пересчёт: 30", "Ошибка в условии", "Исправляю задачу", "Проверим ещё раз: 30"):
            self.assertTrue(gen.DRAFT_SOLUTION_RE.search(draft), draft)

    def test_thinking_limits(self) -> None:
        settings = get_settings()
        with mock.patch.object(settings, "ai_gen_thinking", "off"):
            self.assertEqual(gen.gen_limits(), (settings.ai_gen_max_tokens, False))
        with mock.patch.object(settings, "ai_gen_thinking", "on"):
            self.assertEqual(gen.gen_limits(), (settings.ai_gen_max_tokens + settings.ai_gen_thinking_tokens, None))


class WholeJob(Base):
    def test_job_log_counts_autofix(self) -> None:
        import psycopg

        from app import db

        answers = {"n": 0}

        def script(payload, kind):
            prompt = payload["messages"][1]["content"]
            if kind == "check":
                return ok("30")
            if kind == "review":
                return ok(review_json("Правильный ответ: 30" in prompt, "30"))
            answers["n"] += 1
            if answers["n"] == 1:
                return ok(json.dumps({"variants": [{"variant": 1, "tasks": [
                    {"text": WRONG["text"], "format": "input", "answers": ["36"], "solution": WRONG["solution"]}]}]},
                    ensure_ascii=False))
            return ok(json.dumps(FIXED, ensure_ascii=False))

        stub.reset(script)
        with db.get_pool().connection() as conn:
            job_id = conn.execute(
                "INSERT INTO ai_jobs (teacher_id, kind, status, request, result) VALUES (%s, 'test', 'running', %s, %s) RETURNING id",
                (self.ctx.teacher_id, psycopg.types.json.Jsonb(REQUEST), psycopg.types.json.Jsonb(gen.initial_result(1, 1))),
            ).fetchone()["id"]
        with self.assertLogs("app.ai_generation", "INFO") as logs:
            gen.run_test_job(job_id, self.ctx.teacher_id)
        view = gen.job_view(gen.load_job_row(job_id))
        task = view["cells"][0]["tasks"][0]
        self.assertEqual(task["accepted_answers"], ["30"])
        self.assertFalse(task["needs_review"])
        self.assertEqual(view["needs_review"], 0)
        self.assertIn("исправлено автоматически 1 из 1, требуют проверки 0", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
