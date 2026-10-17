"""
Этап «правила генерации и проверки»: промт, автопроверки после генерации
и проверка решения при самопроверке. ИИ — заглушка (tests/support.py).

Примеры взяты из настоящей работы по логарифмам (10 класс, 30 вариантов).
"""

import json
import threading
import unittest
from unittest import mock

from app import ai_client
from app import ai_generation as gen
from app.ai_client import RequestContext
from tests.support import add_user, ok, setup_database, stub

OK_REVIEW = {"status": "ok", "generated": "", "checked": ""}


def task(text: str, answers=None, *, options=None, correct=0, solution="решение") -> dict:
    is_choice = options is not None
    return {
        "skill_index": 1,
        "text": text,
        "answer_format": "choice" if is_choice else "input",
        "options": options or [],
        "correct": correct if is_choice else None,
        "accepted_answers": [] if is_choice else answers,
        "solution": solution,
    }


def warning(item: dict, subject: str = "алгебра") -> str:
    return gen.attach_review(item, OK_REVIEW, subject)["review"]["warning"]


class AutoChecks(unittest.TestCase):
    def test_infinite_answer_in_input_is_flagged(self) -> None:
        bad = task(
            "Найдите длину промежутка области определения функции $y = \\log_9(5 - x)$.",
            ["бесконечность", "∞"],
        )
        self.assertIn("ученик не поймёт, как записать ответ", warning(bad))
        self.assertIn("не указано, как записать ответ", warning(bad))
        interval = task("Найдите область определения. Ответ запишите промежутком.", ["(-∞; 5)"])
        self.assertIn("ученик не поймёт, как записать ответ", warning(interval, "история"))

    def test_math_answer_must_be_one_number(self) -> None:
        good = task("Найдите наибольшее целое $x$ из области определения $y = \\log_3(7 - x)$. "
                    "Ответ запишите целым числом.", ["6"])
        self.assertEqual(warning(good), "")
        self.assertEqual(warning(task("Вычислите. Ответ запишите десятичной дробью.", ["2,5", "2.5"])), "")
        self.assertEqual(warning(task("Вычислите. Ответ запишите числом.", ["−4"])), "")
        self.assertIn("ученик не поймёт", warning(task("Решите. Ответ запишите числом.", ["x = 4"])))
        self.assertIn("ученик не поймёт", warning(task("Решите. Ответ запишите числом.", ["3 и 4"])))
        # Обыкновенная дробь — только если о ней сказано в условии.
        self.assertIn("ученик не поймёт", warning(task("Вычислите. Ответ запишите числом.", ["3/5"])))
        self.assertEqual(warning(task("Вычислите. Ответ запишите обыкновенной дробью.", ["3/5"])), "")
        # Не математика: слово — нормальный ответ.
        self.assertEqual(
            warning(task("Как называется столица Франции? Ответ запишите одним словом.", ["Париж"]), "география"),
            "",
        )
        self.assertEqual(warning(task("В каком году крещение Руси? Ответ запишите годом из четырёх цифр.",
                                      ["988"]), "история"), "")

    def test_long_answer_and_opinion(self) -> None:
        long = task("Кто написал роман? Ответ запишите словами.", ["Лев Николаевич Толстой великий писатель"])
        self.assertIn("ответ длиннее нескольких слов", warning(long, "литература"))
        opinion = task("Как вы понимаете слова героя? Ответ запишите одним словом.", ["честь"])
        self.assertIn("вопрос на мнение", warning(opinion, "литература"))

    def test_missing_picture(self) -> None:
        for text in (
            "На рисунке изображён график функции. Найдите нули. Ответ запишите числом.",
            "По графику определите значение. Ответ запишите числом.",
            "Используя данные таблицы, найдите сумму. Ответ запишите числом.",
            "Рассмотрите карту (см. рис. 3). Ответ запишите одним словом.",
            "Прочитайте текст и найдите метафору. Ответ запишите одним словом.",
        ):
            self.assertIn("ссылка на рисунок, которого нет", warning(task(text, ["1"]), "алгебра"), text)
        self.assertNotIn("рисунок", warning(task("Найдите значение $2^3$. Ответ запишите числом.", ["8"])))
        # У выбора тоже.
        self.assertIn("ссылка на рисунок", warning(task("На схеме показан опыт. Что это?", options=["а", "б"])))

    def test_draft_solution(self) -> None:
        # Настоящий ответ модели при проверке этапа (вариант 1).
        drafty = task(
            "Найдите количество целых чисел в области определения $y = \\log_5(12 - 3x)$. "
            "Ответ запишите целым числом.",
            ["4"],
            solution="$x < 4$. Целые решения: 3, 2, 1, … — их бесконечно много. Переформулируем: "
            "найдите наибольшее целое. Давайте лучше так. Ответ: 3.",
        )
        self.assertIn("в решении черновые рассуждения", warning(drafty))

    def test_short_reason(self) -> None:
        text = "слово " * 60
        reason = gen.short_reason(text)
        self.assertTrue(reason.endswith("…") and len(reason) <= 161 and "слов…" not in reason)

    def test_choice_needs_no_format_hint(self) -> None:
        self.assertEqual(warning(task("Промежуток области определения $y=\\log_9(5-x)$?",
                                      options=["$(-\\infty; 5)$", "$(5; +\\infty)$"])), "")


class Prompts(unittest.TestCase):
    def request(self, answer_format: str, hint: str = "") -> dict:
        skill = {"title": "Находить область определения логарифмической функции",
                 "tasks_per_variant": 1, "answer_format": answer_format}
        if hint:
            skill["ai_hint"] = hint
        return {"subject": "алгебра", "topic": "логарифмы", "grade": "10", "skills": [skill], "variants_count": 2}

    def test_rules_in_both_prompts(self) -> None:
        request = self.request("input")
        prompts = [
            gen.skill_messages(request, 1, [1, 2], [], "")[1]["content"],
            gen.task_messages({**request, "skill_index": 1, "variant_no": 1}, "")[1]["content"],
        ]
        for prompt in prompts:
            for piece in (
                "ОДНО конечное число",
                "бесконечность, промежутки, множества",
                "Ответ запишите целым числом.",
                "одним словом в именительном падеже",
                "не ссылайся на рисунки",
                "приведи фрагмент прямо в задании",
                "вопросов на мнение",
                "общеизвестные школьные",
                "без черновых рассуждений",
                "перечисли в \"answers\" ВСЕ",
            ):
                self.assertIn(piece, prompt)

    def test_teacher_hint_goes_into_prompt(self) -> None:
        request = self.request("input", "без логарифмов по основанию 10")
        self.assertIn("Уточнение учителя к этому умению (выполни обязательно): без логарифмов по основанию 10",
                      gen.skill_messages(request, 1, [1], [], "")[1]["content"])
        self.assertIn("без логарифмов по основанию 10",
                      gen.task_messages({**request, "skill_index": 1, "variant_no": 1}, "")[1]["content"])


class SolutionCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        setup_database()
        cls.ctx = RequestContext(teacher_id=add_user("Проверкин Пётр", "quality@example.org"))

    def setUp(self) -> None:
        patcher = mock.patch.object(ai_client.time, "sleep", lambda _: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_check(self, item: dict, solve: str, review) -> dict:
        def script(payload, kind):
            if kind == "check":
                return ok(solve)
            return ok(review if isinstance(review, str) else json.dumps(review, ensure_ascii=False))

        stub.reset(script)
        request = {"subject": "алгебра", "topic": "логарифмы", "grade": "10"}
        reviewed = gen.self_check(self.ctx, request, item, threading.Event(), "тест")
        return gen.attach_review(item, reviewed, "алгебра")

    def test_solution_contradicting_answer_is_flagged(self) -> None:
        # Из жизни: в решении «их бесконечно много», а ответ — 3.
        item = task(
            "Сколько целых отрицательных чисел входит в область определения $y = \\log_2(x + 4)$? "
            "Ответ запишите целым числом.",
            ["3"],
            solution="$x > -4$, целые отрицательные: −3, −2, −1 … их бесконечно много.",
        )
        checked = self.run_check(item, "3", {
            "solution_ok": False, "solution_problem": "в решении «бесконечно много», а ответ 3",
            "facts_ok": True, "facts_problem": "",
        })
        self.assertTrue(checked["needs_review"])
        self.assertEqual(checked["review"]["status"], "ok")  # ответ-то сошёлся
        self.assertIn("решение не сходится с ответом: в решении «бесконечно много», а ответ 3",
                      checked["review"]["warning"])
        kinds = [kind for kind, _ in stub.requests]
        self.assertEqual(kinds, ["check", "review"])
        # Во втором запросе есть и ответ, и решение; в первом — нет ни того, ни другого.
        solve_prompt = stub.requests[0][1]["messages"][1]["content"]
        review_prompt = stub.requests[1][1]["messages"][1]["content"]
        self.assertNotIn("бесконечно много", solve_prompt)
        self.assertIn("бесконечно много", review_prompt)
        self.assertIn("Правильный ответ: 3", review_prompt)

    def test_doubtful_fact(self) -> None:
        item = task("В каком году основан Санкт-Петербург? Ответ запишите годом из четырёх цифр.", ["1705"])
        checked = self.run_check(item, "1703", {
            "solution_ok": False, "solution_problem": "год основания — 1703",
            "facts_ok": False, "facts_problem": "Санкт-Петербург основан в 1703 году",
        })
        warning_text = checked["review"]["warning"]
        self.assertIn("сомнительный факт: Санкт-Петербург основан в 1703 году", warning_text)
        self.assertEqual(checked["review"]["status"], "mismatch")

    def test_good_task_stays_green(self) -> None:
        item = task("Найдите наибольшее целое $x$ из области определения $y=\\log_3(7-x)$. "
                    "Ответ запишите целым числом.", ["6"], solution="$7 - x > 0$, $x < 7$, наибольшее целое 6.")
        checked = self.run_check(item, "6", '```json\n{"solution_ok": true, "solution_problem": "", '
                                            '"facts_ok": true, "facts_problem": ""}\n```')
        self.assertFalse(checked["needs_review"], checked["review"])

    def test_broken_review_reply_does_not_flag(self) -> None:
        item = task("Найдите $2^3$. Ответ запишите целым числом.", ["8"])
        self.assertFalse(self.run_check(item, "8", "не знаю")["needs_review"])

    def test_switch_off(self) -> None:
        from app.config import get_settings

        item = task("Найдите $2^3$. Ответ запишите целым числом.", ["8"])
        with mock.patch.object(type(get_settings()), "ai_check_solution_on", property(lambda self: False)):
            self.run_check(item, "8", {"solution_ok": False})
        self.assertEqual([kind for kind, _ in stub.requests], ["check"])


if __name__ == "__main__":
    unittest.main()
