"""
Создание администратора из командной строки.

Запуск (из папки backend, с активным виртуальным окружением):

    python -m app.create_admin

Спросит ФИО, email и пароль. Через обычную регистрацию администратором стать
нельзя — только этой командой, у неё же нет веб-интерфейса. Значит, права
администратора получает только тот, у кого есть доступ к серверу.
"""

import getpass
import sys

from app import db
from app.security import hash_password, normalize_email, password_problem


def ask(prompt: str) -> str:
    """Спрашивает строку и не принимает пустой ответ."""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  Значение не может быть пустым.")


def ask_password() -> str:
    """
    Спрашивает пароль дважды. getpass не показывает ввод на экране —
    пароль не останется в истории терминала и не будет виден коллегам.
    """
    while True:
        first = getpass.getpass("Пароль: ")
        problem = password_problem(first)
        if problem is not None:
            print(f"  {problem}")
            continue

        second = getpass.getpass("Пароль ещё раз: ")
        if first != second:
            print("  Пароли не совпали, попробуйте снова.")
            continue

        return first


def main() -> int:
    print("Создание администратора сервиса проверочных работ.")
    print("Отменить — Ctrl+C.\n")

    try:
        pool = db.init_pool()
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось подключиться к базе: {exc}")
        print("Проверьте, что PostgreSQL запущен и DATABASE_URL в backend/.env верный.")
        return 1

    try:
        full_name = ask("ФИО: ")
        email = normalize_email(ask("Email (он же логин): "))
        password = ask_password()

        with pool.connection() as conn:
            existing = conn.execute(
                "SELECT id, role FROM users WHERE email = %s", (email,)
            ).fetchone()

            if existing is not None:
                # Учётка есть — предлагаем повысить её до администратора
                # и заодно сменить пароль. Это единственный способ восстановить
                # доступ, если админ забыл пароль.
                answer = input(
                    f"Пользователь {email} уже есть (роль: {existing['role']}). "
                    "Сделать администратором и сменить пароль? [y/N]: "
                ).strip().lower()

                if answer not in ("y", "yes", "д", "да"):
                    print("Отменено.")
                    return 1

                conn.execute(
                    """
                    UPDATE users
                    SET full_name = %s, password_hash = %s, role = 'admin', is_active = TRUE
                    WHERE id = %s
                    """,
                    (full_name, hash_password(password), existing["id"]),
                )
                # Старые сессии этого пользователя сбрасываем: роль изменилась.
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (existing["id"],))
                print(f"\nГотово: {email} теперь администратор.")
                return 0

            conn.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (%s, %s, %s, 'admin')
                """,
                (full_name, email, hash_password(password)),
            )

        print(f"\nГотово: администратор {email} создан.")
        print("Войдите на странице входа и смените школьный код в разделе «Настройки».")
        return 0

    except KeyboardInterrupt:
        print("\nОтменено.")
        return 1
    finally:
        db.close_pool()


if __name__ == "__main__":
    sys.exit(main())
