"""
Этап 2: ссылки по классам, защита от пересдачи, аннулирование, статистика
учителя, права администратора и сама миграция 010 (накат и откат).

Запросы идут в настоящее приложение, поднятое на свободном порту с временной
базой (см. tests/support.py).
"""

import unittest

import psycopg

from tests.support import Client, add_user, drop_database, migrations, query, scratch_database, start_app

BACKEND_SKILLS = [
    {"title": "Складывать дроби", "tasks_per_variant": 1, "answer_format": "input"},
    {"title": "Сравнивать дроби", "tasks_per_variant": 1, "answer_format": "choice"},
]


def test_body(title: str, classes: list[str], variants: int = 3) -> dict:
    return {
        "title": title,
        "subject": "математика",
        "classes": classes,
        "variants_count": variants,
        "shuffle": False,
        "skills": BACKEND_SKILLS,
        "variants": [
            {
                "variant_no": number,
                "tasks": [
                    {
                        "skill_index": 1,
                        "text": f"Вариант {number}: сложите",
                        "answer_format": "input",
                        "accepted_answers": ["3/5"],
                    },
                    {
                        "skill_index": 2,
                        "text": f"Вариант {number}: что больше?",
                        "answer_format": "choice",
                        "options": ["да", "нет"],
                        "correct": 0,
                    },
                ],
            }
            for number in range(1, variants + 1)
        ],
    }


def setUpModule() -> None:
    start_app()
    global ADMIN_ID, ANNA_ID, BORIS_ID
    ADMIN_ID = add_user("Админов Админ", "admin@example.org", "admin")
    ANNA_ID = add_user("Иванова Анна", "anna@example.org")
    BORIS_ID = add_user("Петров Борис", "boris@example.org")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.admin = Client().login("admin@example.org")
        cls.anna = Client().login("anna@example.org")
        cls.boris = Client().login("boris@example.org")
        cls.student = Client()

    def create(self, title: str, classes: list[str], variants: int = 3, client=None) -> dict:
        status, data = (client or self.anna).post("/api/tests", test_body(title, classes, variants))
        self.assertEqual(status, 201, data)
        return data

    def links(self, created: dict) -> dict[str, dict]:
        return {link["class_name"]: link for link in created["class_links"]}

    def start(self, code: str, name: str, device: str = "", student_class: str = ""):
        return self.student.post(
            f"/api/public/tests/{code}/start",
            {"student_name": name, "device_id": device, "student_class": student_class},
        )

    def solve(self, code: str, started: dict, correct: bool = True):
        """Сдаёт работу: оба задания верно или оба неверно."""
        tasks = started["tasks"]
        choice = next(task for task in tasks if task["answer_format"] == "choice")
        typed = next(task for task in tasks if task["answer_format"] == "input")
        right, wrong = (option["id"] for option in choice["options"])
        return self.student.post(
            f"/api/public/tests/{code}/attempts/{started['attempt_id']}/submit",
            {
                "attempt_token": started["attempt_token"],
                "choices": {choice["id"]: right if correct else wrong},
                "inputs": {typed["id"]: "3/5" if correct else "1"},
            },
        )


# =====================================================================
# Ссылки по классам
# =====================================================================


class ClassLinks(Base):
    def test_each_class_gets_its_own_link(self) -> None:
        created = self.create("Ссылки", ["6А", "6Б"])
        links = self.links(created)
        self.assertEqual(sorted(links), ["6А", "6Б"])
        self.assertNotEqual(links["6А"]["code"], links["6Б"]["code"])
        self.assertNotIn(created["code"], [link["code"] for link in links.values()])

        status, info = self.student.get(f"/api/public/tests/{links['6А']['code']}")
        self.assertEqual(status, 200)
        self.assertEqual((info["class_name"], info["classes"], info["is_open"]), ("6А", ["6А"], True))

        # Общей ссылки у новой работы нет.
        status, data = self.student.get(f"/api/public/tests/{created['code']}")
        self.assertEqual(status, 404)
        self.assertIn("для каждого класса своя ссылка", data["detail"])

    def test_class_comes_from_link_and_variants_rotate_per_class(self) -> None:
        links = self.links(self.create("Очередь", ["6А", "6Б"], variants=3))
        a, b = links["6А"]["code"], links["6Б"]["code"]

        given_a = []
        for number in range(4):
            # Браузер прислал чужой класс — сервер верит ссылке.
            status, started = self.start(a, f"Ученик Номер{'абвг'[number]}", f"a{number}", "11Я")
            self.assertEqual(status, 201, started)
            self.assertEqual(started["student_class"], "6А")
            given_a.append(started["variant_no"])
        self.assertEqual(given_a, [1, 2, 3, 1])

        # Во втором классе очередь своя: снова с первого варианта.
        given_b = [self.start(b, f"Другой Номер{'абв'[n]}", f"b{n}")[1]["variant_no"] for n in range(3)]
        self.assertEqual(given_b, [1, 2, 3])

    def test_one_attempt_per_student_and_per_device_within_class_link(self) -> None:
        links = self.links(self.create("Пересдача", ["6А", "6Б"]))
        a, b = links["6А"]["code"], links["6Б"]["code"]

        status, first = self.start(a, "Ёжикова Алёна", "pc-1")
        self.assertEqual(status, 201)
        # Не сдала и открыла снова — тот же вариант и та же попытка.
        status, again = self.start(a, "ежикова  алена", "pc-1")
        self.assertEqual((status, again["attempt_id"]), (201, first["attempt_id"]))
        self.assertEqual(again["student_name"], "Ёжикова Алёна")
        self.assertEqual(self.solve(a, first)[0], 200)

        # Тот же ученик (другой регистр, е вместо ё) — уже сдавал, хоть и с другого устройства.
        status, data = self.start(a, "ЕЖИКОВА АЛЕНА", "pc-2")
        self.assertEqual(status, 409)
        self.assertIn("уже сдали", data["detail"])

        # Другое имя с того же устройства по той же ссылке — нельзя.
        status, data = self.start(a, "Хитров Пётр", "pc-1")
        self.assertEqual(status, 409)
        self.assertIn("С этого устройства", data["detail"])
        self.assertIn("Ёжикова Алёна", data["detail"])

        # Тот же компьютер, но ссылка ДРУГОГО класса — следующий урок в том же кабинете.
        status, other = self.start(b, "Хитров Пётр", "pc-1")
        self.assertEqual(status, 201, other)
        self.assertEqual(other["student_class"], "6Б")
        self.assertEqual(self.solve(b, other)[0], 200)

        # Без метки устройства (старый браузер, запрет хранилища) работает правило по ФИО.
        self.assertEqual(self.start(a, "Новый Ученик", "")[0], 201)

    def test_two_tabs_on_one_device_only_one_submits(self) -> None:
        code = self.links(self.create("Две вкладки", ["6А"]))["6А"]["code"]
        one = self.start(code, "Первый Ученик", "pc-9")[1]
        two = self.start(code, "Второй Ученик", "pc-9")[1]
        self.assertEqual(self.solve(code, one)[0], 200)
        status, data = self.solve(code, two)
        self.assertEqual(status, 409)
        self.assertIn("С этого устройства", data["detail"])

    def test_open_and_close_per_class(self) -> None:
        created = self.create("Приём", ["6А", "6Б"])
        links = self.links(created)
        test_id = created["id"]
        started = self.start(links["6А"]["code"], "Успел Начать", "x1")[1]

        status, link = self.anna.request(
            "PATCH", f"/api/tests/{test_id}/classes/{links['6А']['id']}", {"is_open": False}
        )
        self.assertEqual((status, link["is_open"]), (200, False))

        self.assertFalse(self.student.get(f"/api/public/tests/{links['6А']['code']}")[1]["is_open"])
        self.assertEqual(self.start(links["6А"]["code"], "Опоздал Ученик", "x2")[0], 409)
        self.assertEqual(self.solve(links["6А"]["code"], started)[0], 409)
        # Второй класс не затронут.
        self.assertEqual(self.start(links["6Б"]["code"], "Другой Класс", "x3")[0], 201)
        # Попытку 6А нельзя сдать через открытую ссылку 6Б.
        self.assertEqual(self.solve(links["6Б"]["code"], started)[0], 404)

        self.anna.request("PATCH", f"/api/tests/{test_id}/classes/{links['6А']['id']}", {"is_open": True})
        self.assertEqual(self.solve(links["6А"]["code"], started)[0], 200)

        # Чужой учитель и чужой номер класса.
        status, _ = self.boris.request(
            "PATCH", f"/api/tests/{test_id}/classes/{links['6А']['id']}", {"is_open": False}
        )
        self.assertEqual(status, 403)
        other = self.create("Чужая", ["7А"], client=self.boris)
        status, _ = self.boris.request(
            "PATCH", f"/api/tests/{other['id']}/classes/{links['6А']['id']}", {"is_open": False}
        )
        self.assertEqual(status, 404)

    def test_add_class_to_published_test(self) -> None:
        created = self.create("Добавить класс", ["6А"])
        test_id = created["id"]
        status, link = self.anna.post(f"/api/tests/{test_id}/classes", {"class_name": " 6В "})
        self.assertEqual((status, link["class_name"]), (201, "6В"))
        status, started = self.start(link["code"], "Из Нового Класса", "n1")
        self.assertEqual((status, started["student_class"]), (201, "6В"))

        self.assertEqual(self.anna.post(f"/api/tests/{test_id}/classes", {"class_name": "6В"})[0], 409)
        self.assertEqual(self.boris.post(f"/api/tests/{test_id}/classes", {"class_name": "6Г"})[0], 403)

        results = self.anna.get(f"/api/tests/{test_id}/results")[1]
        self.assertEqual(results["classes"], ["6А", "6В"])
        self.assertEqual([item["class_name"] for item in results["class_links"]], ["6А", "6В"])
        self.assertTrue(results["links_by_class"])

    def test_old_test_keeps_its_general_link(self) -> None:
        created = self.create("Старая работа", ["5А", "5Б"], variants=2)
        # Так выглядит работа, созданная до ссылок по классам (после миграции 010).
        query("UPDATE tests SET links_by_class = FALSE WHERE id = %s", (created["id"],))
        general = created["code"]

        info = self.student.get(f"/api/public/tests/{general}")[1]
        self.assertEqual((info["class_name"], info["classes"]), (None, ["5А", "5Б"]))
        self.assertEqual(self.start(general, "Старый Ученик", "o1")[0], 422)  # класс не выбран
        self.assertEqual(self.start(general, "Старый Ученик", "o1", "9Я")[0], 422)

        status, one = self.start(general, "Старый Ученик", "o1", "5А")
        self.assertEqual((status, one["variant_no"]), (201, 1))
        self.assertEqual(self.solve(general, one)[0], 200)
        # На общей ссылке правила прежние: устройство не учитывается.
        status, two = self.start(general, "Второй Старый", "o1", "5А")
        self.assertEqual((status, two["variant_no"]), (201, 2))
        self.assertEqual(self.start(general, "старый ученик", "o2", "5А")[0], 409)

        # Тот же ученик не пройдёт второй раз и по ссылке своего класса.
        class_code = self.links(created)["5А"]["code"]
        self.assertEqual(self.start(class_code, "Старый Ученик", "o3")[0], 409)

        results = self.anna.get(f"/api/tests/{created['id']}/results")[1]
        self.assertFalse(results["links_by_class"])
        self.assertEqual(results["code"], general)


# =====================================================================
# Разрешить пересдачу
# =====================================================================


class Retake(Base):
    def test_annulled_attempt_frees_student_and_device_and_leaves_statistics(self) -> None:
        created = self.create("Аннулирование", ["8А"], variants=2)
        test_id = created["id"]
        code = self.links(created)["8А"]["code"]

        bad = self.start(code, "Двоечников Вася", "dev-1")[1]
        self.assertEqual(self.solve(code, bad, correct=False)[1]["score"], 0)
        good = self.start(code, "Отличница Маша", "dev-2")[1]
        self.assertEqual(self.solve(code, good)[1]["score"], 2)

        before = self.anna.get(f"/api/tests/{test_id}/results")[1]
        self.assertEqual(before["attempts_count"], 2)
        self.assertEqual({stat["percent"] for stat in before["skill_stats"]}, {50})

        # Чужой учитель не может; чужой номер попытки — 404.
        url = f"/api/tests/{test_id}/attempts/{bad['attempt_id']}/annul"
        self.assertEqual(self.boris.post(url)[0], 403)
        self.assertEqual(self.anna.post(f"/api/tests/{test_id}/attempts/999999/annul")[0], 404)
        self.assertEqual(self.anna.post(url), (200, {"attempt_id": bad["attempt_id"], "annulled": True}))

        after = self.anna.get(f"/api/tests/{test_id}/results")[1]
        self.assertEqual(after["attempts_count"], 1)
        self.assertEqual(after["class_links"][0]["attempts_count"], 1)
        self.assertEqual({stat["percent"] for stat in after["skill_stats"]}, {100})
        annulled = [row for row in after["attempts"] if row["annulled"]]
        self.assertEqual([row["student_name"] for row in annulled], ["Двоечников Вася"])

        # Браузер ученика узнаёт, что старый результат больше не действует.
        check = f"/api/public/tests/{code}/attempts/{bad['attempt_id']}/check"
        self.assertEqual(self.student.post(check, {"attempt_token": bad["attempt_token"]})[1], {"state": "gone"})
        self.assertEqual(self.student.post(check, {"attempt_token": "чужой"})[1], {"state": "gone"})
        check_good = f"/api/public/tests/{code}/attempts/{good['attempt_id']}/check"
        self.assertEqual(
            self.student.post(check_good, {"attempt_token": good["attempt_token"]})[1], {"state": "active"}
        )

        # Тот же ученик с того же устройства проходит заново.
        status, retake = self.start(code, "Двоечников Вася", "dev-1")
        self.assertEqual(status, 201, retake)
        self.assertNotEqual(retake["attempt_id"], bad["attempt_id"])
        self.assertEqual(self.solve(code, retake)[1]["score"], 2)

        final = self.anna.get(f"/api/tests/{test_id}/results")[1]
        self.assertEqual(final["attempts_count"], 2)
        self.assertEqual(len(final["attempts"]), 3)

        # В статистике и в списке работ аннулированной попытки нет.
        stats = self.anna.get(f"/api/stats?test_id={test_id}")[1]
        self.assertEqual(stats["by_class"], [{"student_class": "8А", "attempts_count": 2, "average_percent": 100}])
        mine = next(row for row in self.anna.get("/api/my/tests")[1] if row["id"] == test_id)
        self.assertEqual(mine["attempts_count"], 2)

        # Аннулированную попытку сдать уже нельзя (не успел до пересдачи).
        late = self.start(code, "Медленный Петя", "dev-3")[1]
        self.anna.post(f"/api/tests/{test_id}/attempts/{late['attempt_id']}/annul")
        status, data = self.solve(code, late)
        self.assertEqual(status, 409)
        self.assertIn("аннулировал", data["detail"])


# =====================================================================
# Статистика: учителю — только своё
# =====================================================================


class Stats(Base):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.carol_id = add_user("Сидорова Вера", "vera@example.org")
        cls.dmitry_id = add_user("Кузнецов Дмитрий", "dmitry@example.org")
        cls.carol = Client().login("vera@example.org")
        cls.dmitry = Client().login("dmitry@example.org")

    def test_teacher_sees_only_own_tests(self) -> None:
        own = self.create("Своя работа Веры", ["9А", "9Б"], client=self.carol)
        foreign = self.create("Работа Дмитрия", ["9В"], client=self.dmitry)
        for created, names in ((own, ["Один Ученик", "Два Ученик"]), (foreign, ["Три Ученик"])):
            for class_name, link in self.links(created).items():
                for position, name in enumerate(names):
                    started = self.start(link["code"], name, f"{class_name}-{position}")[1]
                    self.solve(link["code"], started, correct=position == 0)

        status, stats = self.carol.get("/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(stats["scope"], "own")
        self.assertEqual(stats["totals"]["tests"], 1)
        self.assertEqual(stats["totals"]["attempts_total"], 4)
        self.assertEqual(stats["by_teacher"], [])
        self.assertEqual(sorted(row["student_class"] for row in stats["by_class"]), ["9А", "9Б"])
        self.assertEqual({row["test_title"] for row in stats["weak_skills"]}, {"Своя работа Веры"})
        self.assertEqual({row["student_class"] for row in stats["weak_skills"]}, {"9А", "9Б"})
        self.assertEqual(stats["filters"]["tests"], [{"id": own["id"], "title": "Своя работа Веры"}])
        self.assertEqual(stats["filters"]["classes"], ["9А", "9Б"])

        # Фильтры работают внутри своего.
        one_class = self.carol.get("/api/stats?student_class=9%D0%90")[1]
        self.assertEqual([row["attempts_count"] for row in one_class["by_class"]], [2])
        self.assertEqual(self.carol.get(f"/api/stats?test_id={own['id']}")[0], 200)

        # Чужую работу по номеру не отдаём; несуществующую — 404.
        status, data = self.carol.get(f"/api/stats?test_id={foreign['id']}")
        self.assertEqual(status, 403)
        self.assertIn("другого учителя", data["detail"])
        self.assertEqual(self.carol.get("/api/stats?test_id=999999")[0], 404)
        # Старый адрес — только администратору.
        self.assertEqual(self.carol.get("/api/admin/stats")[0], 403)
        self.assertEqual(Client().get("/api/stats")[0], 401)

        # Чужие результаты и выгрузка по номеру — тоже нет.
        self.assertEqual(self.carol.get(f"/api/tests/{foreign['id']}/results")[0], 403)
        self.assertEqual(self.carol.get(f"/api/tests/{foreign['id']}/export.xlsx", raw=True)[0], 403)

    def test_admin_sees_everything(self) -> None:
        self.create("Ещё одна работа Дмитрия", ["10А"], client=self.dmitry)
        for path in ("/api/stats", "/api/admin/stats"):
            status, stats = self.admin.get(path)
            self.assertEqual(status, 200)
            self.assertEqual(stats["scope"], "school")
            self.assertGreaterEqual(stats["totals"]["tests"], 1)
            self.assertIn("Кузнецов Дмитрий", [row["full_name"] for row in stats["by_teacher"]])
            self.assertGreater(stats["totals"]["teachers"], 0)
        any_test = query("SELECT id FROM tests WHERE teacher_id = %s LIMIT 1", (self.dmitry_id,))[0][0]
        self.assertEqual(self.admin.get(f"/api/stats?test_id={any_test}")[0], 200)


# =====================================================================
# Права администратора
# =====================================================================


class Roles(Base):
    def role(self, client: Client, user_id: int, role: str):
        return client.request("PUT", f"/api/admin/teachers/{user_id}/role", {"role": role})

    def test_grant_and_revoke_with_journal(self) -> None:
        target = add_user("Новый Завуч", "zavuch@example.org")
        zavuch = Client().login("zavuch@example.org")

        # Учитель права не раздаёт и в админку не ходит.
        self.assertEqual(self.role(self.anna, target, "admin")[0], 403)
        self.assertEqual(zavuch.get("/api/admin/teachers")[0], 403)

        status, row = self.role(self.admin, target, "admin")
        self.assertEqual((status, row["role"]), (200, "admin"))
        # Права действуют сразу, без повторного входа.
        self.assertEqual(zavuch.get("/api/admin/teachers")[0], 200)
        self.assertEqual(zavuch.get("/api/auth/me")[1]["role"], "admin")

        # С себя снять нельзя.
        status, data = self.role(zavuch, target, "teacher")
        self.assertEqual(status, 400)
        self.assertIn("с самого себя", data["detail"])

        status, row = self.role(self.admin, target, "teacher")
        self.assertEqual((status, row["role"]), (200, "teacher"))
        self.assertEqual(zavuch.get("/api/admin/teachers")[0], 403)

        # Повтор того же значения ничего не пишет в журнал.
        self.role(self.admin, target, "teacher")
        log = self.admin.get("/api/admin/log")[1]
        mine = [(item["action"], item["admin_name"]) for item in log if item["target_name"] == "Новый Завуч"]
        self.assertEqual(mine, [("revoke_admin", "Админов Админ"), ("grant_admin", "Админов Админ")])
        self.assertEqual(self.anna.get("/api/admin/log")[0], 403)

        self.assertEqual(self.role(self.admin, 999999, "admin")[0], 404)
        self.assertEqual(self.role(self.admin, target, "директор")[0], 422)

    def test_last_admin_cannot_be_revoked(self) -> None:
        # Единственный действующий администратор — он сам: с себя снять нельзя.
        status, data = self.role(self.admin, ADMIN_ID, "teacher")
        self.assertEqual(status, 400)

        # Второй администратор отключён — действующим он не считается.
        second = add_user("Бывший Админ", "former@example.org", "admin")
        former = Client().login("former@example.org")
        self.admin.request("PATCH", f"/api/admin/teachers/{second}", {"is_active": False})
        self.assertEqual(former.get("/api/admin/teachers")[0], 401)
        # Снять права с отключённого можно: действующий администратор остаётся.
        self.assertEqual(self.role(self.admin, second, "teacher")[0], 200)
        # А выдать права отключённой учётке — нет.
        status, data = self.role(self.admin, second, "admin")
        self.assertEqual(status, 400)
        self.assertIn("отключена", data["detail"])
        self.assertEqual(query("SELECT count(*) FROM users WHERE role = 'admin' AND is_active")[0][0], 1)

    def test_last_admin_rule_in_database_state(self) -> None:
        """Правило «последнего администратора» само по себе: без проверки «с себя»."""
        from fastapi import HTTPException

        from app.routers import admin as admin_router
        from app.schemas import RoleUpdate

        # Запрос пришёл от учётки, которую только что разжаловали в другой вкладке:
        # в сессии она ещё «админ», а в базе действующий администратор уже один.
        ghost = {"id": ANNA_ID, "full_name": "Иванова Анна", "email": "anna@example.org", "role": "admin"}
        with self.assertRaises(HTTPException) as caught:
            admin_router.update_role(RoleUpdate(role="teacher"), user_id=ADMIN_ID, admin=ghost)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("последний администратор", caught.exception.detail)
        self.assertEqual(query("SELECT role FROM users WHERE id = %s", (ADMIN_ID,))[0][0], "admin")


# =====================================================================
# Миграция 010: накат на старые данные, откат, повторный накат
# =====================================================================


class Migration010(unittest.TestCase):
    NAME = "kr_tests_tmp_migration010"

    def test_up_down_up_on_old_data(self) -> None:
        url = scratch_database(self.NAME)
        self.addCleanup(drop_database, self.NAME)
        up = next(path for path in migrations() if path.name.startswith("010"))
        down = up.with_name(up.name.replace(".sql", ".down.sql"))

        with psycopg.connect(url, autocommit=True) as conn:
            for path in migrations(up_to="009"):
                conn.execute(path.read_text(encoding="utf-8"))

            # Данные «до миграции»: работа с двумя классами и сданными работами.
            teacher = conn.execute(
                "INSERT INTO users (full_name, email, password_hash) VALUES ('У', 'u@example.org', 'x') RETURNING id"
            ).fetchone()[0]
            test_id = conn.execute(
                """
                INSERT INTO tests (title, subject, teacher_id, teacher_name, share_token,
                                   classes, variants_count, is_published, is_open)
                VALUES ('Старая', 'алгебра', %s, 'У', 'oldcode22', '{7А,7Б}', 2, TRUE, FALSE)
                RETURNING id
                """,
                (teacher,),
            ).fetchone()[0]
            for name, class_name in (("Ёжикова Алёна", "7А"), ("Ежикова Алена", "7А"), ("Иванов Иван", "7Б")):
                conn.execute(
                    "INSERT INTO attempts (test_id, student_name, student_class, attempt_token, finished_at) "
                    "VALUES (%s, %s, %s, %s, now())",
                    (test_id, name, class_name, name),
                )

            conn.execute(up.read_text(encoding="utf-8"))

            links = conn.execute(
                "SELECT class_name, code, is_open FROM test_classes WHERE test_id = %s ORDER BY class_name",
                (test_id,),
            ).fetchall()
            self.assertEqual([(name, is_open) for name, _, is_open in links], [("7А", False), ("7Б", False)])
            codes = [code for _, code, _ in links]
            self.assertEqual(len(set(codes)), 2)
            self.assertTrue(all(len(code) == 8 and code != "oldcode22" for code in codes))
            self.assertFalse(conn.execute("SELECT links_by_class FROM tests").fetchone()[0])
            # Попытки целы; у «двойника» по букве ё ключ особый, индекс построился.
            keys = [row[0] for row in conn.execute("SELECT name_key FROM attempts ORDER BY id")]
            self.assertEqual(keys[0], "ежикова алена")
            self.assertTrue(keys[1].startswith("ежикова алена#"))
            self.assertEqual(keys[2], "иванов иван")

            # Повторный накат ничего не ломает и не плодит ссылки.
            conn.execute(up.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("SELECT count(*) FROM test_classes").fetchone()[0], 2)

            # Аннулированная попытка + новая того же ученика — как после «Разрешить пересдачу».
            conn.execute("UPDATE attempts SET annulled_at = now() WHERE student_name = 'Иванов Иван'")
            conn.execute(
                "INSERT INTO attempts (test_id, student_name, student_class, name_key, attempt_token) "
                "VALUES (%s, 'Иванов Иван', '7Б', 'иванов иван', 'retake')",
                (test_id,),
            )

            conn.execute(down.read_text(encoding="utf-8"))
            tables = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
            self.assertNotIn("test_classes", tables)
            self.assertNotIn("admin_log", tables)
            columns = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'attempts'"
                )
            }
            self.assertFalse(columns & {"class_id", "device_id", "annulled_at", "name_key"})
            indexes = {row[0] for row in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'attempts'")}
            self.assertIn("uq_attempts_test_student", indexes)
            # Действующие попытки на месте, аннулированная удалена.
            self.assertEqual(conn.execute("SELECT count(*) FROM attempts").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT share_token FROM tests").fetchone()[0], "oldcode22")

            # И снова вперёд.
            conn.execute(up.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("SELECT count(*) FROM test_classes").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
