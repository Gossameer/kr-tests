"""
Формулы: разбор школьного LaTeX, проверка ответов с дробями, пометка битых формул.
База и ИИ-сервис здесь не нужны.

    .venv\\Scripts\\python -m unittest tests.test_formulas -v
"""

import json
import unittest

from app import ai_generation as gen
from app import formulas
from app.answers import answers_match, check_input_answer


def plain(source: str) -> str:
    return formulas.to_plain(formulas.parse(source))


class Parse(unittest.TestCase):
    def test_school_notation(self) -> None:
        self.assertEqual(plain(r"\frac{3}{5}"), "3/5")
        self.assertEqual(plain(r"2\frac{1}{3} + 1{,}5"), "2 1/3+1,5")
        self.assertEqual(plain(r"3 \cdot 4 : 2"), "3·4:2")
        self.assertEqual(plain(r"\frac{a+b}{2}"), "(a+b)/2")
        self.assertEqual(plain(r"2^3"), "2^3")
        self.assertEqual(plain(r"x^{n+1}"), "x^(n+1)")
        self.assertEqual(plain(r"a_1^2"), "a_1^2")
        self.assertEqual(plain(r"\sqrt{16}"), "√16")
        self.assertEqual(plain(r"\sqrt[3]{x+1}"), "∛(x+1)")
        self.assertEqual(plain(r"90^\circ"), "90°")
        self.assertEqual(plain(r"x \le 5,\ y \ne \pi"), "x≤5, y≠π")
        self.assertEqual(plain(r"\left(\frac{1}{2}\right)^2"), "(1/2)^2")
        self.assertEqual(plain(r"5 \text{ см}^2"), "5 см^2")
        self.assertEqual(plain(r"\begin{cases} x+y=5 \\ x-y=1 \end{cases}"), "x+y=5; x−y=1".replace("−", "-"))

    def test_errors(self) -> None:
        for broken in (r"\frac{1}{", r"\frac{1}", r"\foo", "x^", "a}", r"\left( x", "", r"\begin{cases} x"):
            with self.assertRaises(formulas.FormulaError, msg=broken):
                formulas.parse(broken)

    def test_split(self) -> None:
        self.assertEqual(
            formulas.split_formulas(r"Найдите $\frac{1}{2}$ от 10. $$x^2$$ Цена 5\$."),
            [
                ("text", "Найдите "),
                ("inline", r"\frac{1}{2}"),
                ("text", " от 10. "),
                ("display", "x^2"),
                ("text", " Цена 5$."),
            ],
        )
        # Незакрытый знак — остаток остаётся текстом.
        self.assertEqual(formulas.split_formulas("a $b"), [("text", "a $b")])

    def test_formula_problem(self) -> None:
        self.assertEqual(formulas.formula_problem("Найдите 25 % от 100."), "")
        self.assertEqual(formulas.formula_problem(r"Вычислите $2\frac{1}{3} + 1{,}5$."), "")
        self.assertEqual(formulas.formula_problem(r"Цена 5\$ за штуку"), "")
        self.assertEqual(formulas.formula_problem(r"Вычислите $\frac{1}{2}"), "незакрытый знак $")
        self.assertEqual(formulas.formula_problem(r"Вычислите $\frac{1}{2$"), "не закрыта скобка {")
        self.assertIn("неизвестная команда", formulas.formula_problem(r"$\foo{1}$"))
        self.assertIn("вне знаков $", formulas.formula_problem(r"Вычислите \frac{1}{2}"))
        self.assertEqual(formulas.formula_problem("Формула $ $ пустая"), "пустая формула")


class Answers(unittest.TestCase):
    def test_fraction_markup_equals_plain(self) -> None:
        self.assertTrue(answers_match("3/5", r"$\frac{3}{5}$"))
        self.assertTrue(answers_match(r"\frac{3}{5}", "3/5"))
        self.assertTrue(answers_match(" 3 / 5 ", r"\frac{3}{5}"))
        self.assertTrue(answers_match("2,5", "$2{,}5$"))
        self.assertTrue(answers_match("2.50", "$2{,}5$"))
        self.assertTrue(answers_match("−4", "-4"))
        self.assertFalse(answers_match("5/3", r"\frac{3}{5}"))

    def test_mixed_number_is_not_glued(self) -> None:
        self.assertTrue(answers_match("2 1/3", r"$2\frac{1}{3}$"))
        self.assertFalse(answers_match("21/3", "2 1/3"))
        # Разделитель разрядов по-прежнему не мешает.
        self.assertTrue(answers_match("1 000", "1000"))

    def test_plain_answers_as_before(self) -> None:
        self.assertTrue(check_input_answer("Париж", ["париж"]))
        self.assertTrue(check_input_answer("0,50", ["0.5"]))
        self.assertFalse(check_input_answer("", ["0.5"]))


class Generation(unittest.TestCase):
    def test_single_backslash_in_json_survives(self) -> None:
        # Модель забыла удвоить черту: «\f» в JSON — перевод страницы, «\c» — ошибка.
        raw = '{"text": "Вычислите $\\frac{1}{2} \\cdot 4 \\times 2$,\\nответ — число", "n": "a\\nb"}'
        data = gen.load_json_object(raw)
        self.assertEqual(data["text"], "Вычислите $\\frac{1}{2} \\cdot 4 \\times 2$,\nответ — число")
        self.assertEqual(data["n"], "a\nb")
        # Правильно удвоенную черту не трогаем.
        doubled = json.dumps({"text": r"$\frac{1}{2} \ne \tau$"})
        self.assertEqual(gen.load_json_object(doubled)["text"], r"$\frac{1}{2} \ne \tau$")

    def test_read_task(self) -> None:
        task = gen.read_task(
            {
                "text": r"Вычислите $3*4 + \frac{1}{2}$ и 2 * 3",
                "format": "input",
                "answers": [r"$12\frac{1}{2}$", "12,5"],
                "solution": "x",
            },
            1,
            "input",
        )
        self.assertEqual(task["text"], r"Вычислите $3 \cdot 4 + \frac{1}{2}$ и 2 · 3")
        self.assertEqual(task["accepted_answers"], ["12 1/2", "12,5"])

    def test_broken_formula_needs_review(self) -> None:
        review = {"status": "ok", "generated": "", "checked": ""}
        base = {
            "skill_index": 1,
            "answer_format": "choice",
            "correct": 0,
            "accepted_answers": [],
            "solution": "",
        }
        good = {**base, "text": r"Чему равно $\frac{1}{2} + \frac{1}{2}$?", "options": ["$1$", "$2$"]}
        self.assertFalse(gen.attach_review(good, review)["needs_review"])

        unclosed = {**base, "text": r"Чему равно $\frac{1}{2} + 1?", "options": ["1", "2"]}
        checked = gen.attach_review(unclosed, review)
        self.assertTrue(checked["needs_review"])
        self.assertEqual(checked["review"]["warning"], "ошибка в формуле: незакрытый знак $")

        option = {**base, "text": "Выберите дробь", "options": [r"$\frac{1}{2$", "1"]}
        self.assertIn("ошибка в формуле", gen.attach_review(option, review)["review"]["warning"])

    def test_prompt_asks_for_dollar_formulas(self) -> None:
        skill = {"title": "Складывать дроби", "tasks_per_variant": 1, "answer_format": "input"}
        request = {"subject": "Математика", "topic": "", "grade": "6", "skills": [skill], "variants_count": 1}
        prompt = gen.skill_messages(request, 1, [1], [], "")[1]["content"]
        for piece in ("$...$", r"\cdot", r"$\frac{3}{5}$", r"$2\frac{1}{3}$", "{,}", "как его наберёт"):
            self.assertIn(piece, prompt)
        single = {**request, "skill_index": 1, "variant_no": 1}
        self.assertIn(r"$2\frac{1}{3}$", gen.task_messages(single, "")[1]["content"])

    def test_self_check_accepts_fraction_in_any_form(self) -> None:
        task = {"answer_format": "input", "accepted_answers": ["3/5"], "options": [], "correct": None}
        review = {"status": "unchecked", "generated": "", "checked": ""}
        self.assertEqual(gen._compare(task, r"Ответ: $\frac{3}{5}$", dict(review))["status"], "ok")
        self.assertEqual(gen._compare(task, "3/5.", dict(review))["status"], "ok")
        self.assertEqual(gen._compare(task, "5/3", dict(review))["status"], "mismatch")


if __name__ == "__main__":
    unittest.main()
