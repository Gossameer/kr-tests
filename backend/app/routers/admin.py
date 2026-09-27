"""
Админка: учителя, настройки школы и статистика.

    GET   /api/admin/teachers                      — список учителей
    PATCH /api/admin/teachers/{id}                 — заблокировать / разблокировать
    POST  /api/admin/teachers/{id}/reset-password  — выдать временный пароль
    GET   /api/admin/settings                      — школьный код
    PUT   /api/admin/settings                      — сменить школьный код
    GET   /api/admin/stats                         — статистика по школе

Все эндпоинты требуют роль admin (зависимость require_admin).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app import db
from app.auth import db_unavailable, require_admin
from app.schemas import (
    PasswordResetOut,
    SettingsOut,
    SettingsUpdate,
    TeacherRow,
    TeacherUpdate,
)
from app.security import hash_password, temporary_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Ниже этого процента умение считаем несформированным (красная зона).
WEAK_SKILL_PERCENT = 50


def percent_of(correct: int | None, total: int | None) -> int:
    if not correct or not total:
        return 0
    return round(correct * 100 / total)


@router.get("/teachers", response_model=list[TeacherRow], summary="Список учителей")
def list_teachers(admin: dict = Depends(require_admin)) -> list[TeacherRow]:
    """
    Все учётные записи с рабочими цифрами: сколько контрольных создано
    и сколько работ по ним сдано. По ним видно, кто пользуется сервисом.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.full_name, u.email, u.role, u.is_active,
                   u.created_at, u.last_login_at,
                   count(DISTINCT t.id) AS tests_count,
                   count(a.id)          AS attempts_count
            FROM users u
            LEFT JOIN tests t    ON t.teacher_id = u.id
            LEFT JOIN attempts a ON a.test_id = t.id AND a.finished_at IS NOT NULL
            GROUP BY u.id
            ORDER BY u.role, u.full_name
            """
        ).fetchall()

    return [TeacherRow(**row) for row in rows]


@router.patch(
    "/teachers/{user_id}",
    response_model=TeacherRow,
    summary="Заблокировать или разблокировать учителя",
)
def update_teacher(
    payload: TeacherUpdate,
    user_id: int = Path(ge=1),
    admin: dict = Depends(require_admin),
) -> TeacherRow:
    """
    Блокировка не удаляет ни учётку, ни контрольные — человек просто перестаёт
    входить. Его сессии обрываются сразу: строки сессий удаляем.
    """
    if user_id == admin["id"] and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя заблокировать самого себя.",
        )

    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        with conn.transaction():
            updated = conn.execute(
                "UPDATE users SET is_active = %s WHERE id = %s RETURNING id",
                (payload.is_active, user_id),
            ).fetchone()

            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Такого пользователя нет.",
                )

            if not payload.is_active:
                # Блокировка должна действовать немедленно, а не когда человек выйдет.
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))

        row = conn.execute(
            """
            SELECT u.id, u.full_name, u.email, u.role, u.is_active,
                   u.created_at, u.last_login_at,
                   count(DISTINCT t.id) AS tests_count,
                   count(a.id)          AS attempts_count
            FROM users u
            LEFT JOIN tests t    ON t.teacher_id = u.id
            LEFT JOIN attempts a ON a.test_id = t.id AND a.finished_at IS NOT NULL
            WHERE u.id = %s
            GROUP BY u.id
            """,
            (user_id,),
        ).fetchone()

    logger.info(
        "Администратор %s %s учётку %s",
        admin["email"],
        "разблокировал" if payload.is_active else "заблокировал",
        user_id,
    )
    return TeacherRow(**row)


@router.post(
    "/teachers/{user_id}/reset-password",
    response_model=PasswordResetOut,
    summary="Сбросить пароль и выдать временный",
)
def reset_password(
    user_id: int = Path(ge=1),
    admin: dict = Depends(require_admin),
) -> PasswordResetOut:
    """
    Выдаёт новый случайный пароль. Он возвращается ОДИН раз — в базе лежит
    только хеш, посмотреть пароль повторно нельзя даже администратору.

    Все сессии пользователя обрываются: если доступ уводили, он прекращается.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    new_password = temporary_password()

    with pool.connection() as conn, conn.transaction():
        row = conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s RETURNING id, email",
            (hash_password(new_password), user_id),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Такого пользователя нет.",
            )

        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))

    logger.info("Администратор %s сбросил пароль пользователю %s", admin["email"], row["email"])
    return PasswordResetOut(
        user_id=row["id"],
        email=row["email"],
        temporary_password=new_password,
    )


@router.get("/settings", response_model=SettingsOut, summary="Настройки школы")
def get_settings(admin: dict = Depends(require_admin)) -> SettingsOut:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'school_code'").fetchone()

    return SettingsOut(school_code=row["value"] if row else "")


@router.put("/settings", response_model=SettingsOut, summary="Сменить школьный код")
def update_settings(
    payload: SettingsUpdate,
    admin: dict = Depends(require_admin),
) -> SettingsOut:
    """
    Смена кода не влияет на уже созданные учётки — только на новые регистрации.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES ('school_code', %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (payload.school_code,),
        )

    logger.info("Администратор %s сменил школьный код", admin["email"])
    return SettingsOut(school_code=payload.school_code)


@router.get("/stats", summary="Статистика по школе")
def stats(
    admin: dict = Depends(require_admin),
    days: int = Query(default=0, ge=0, le=3650, description="Период в днях, 0 — за всё время"),
    subject: str = Query(default="", description="Фильтр по предмету"),
    student_class: str = Query(default="", description="Фильтр по классу"),
) -> dict:
    """
    Всё, что нужно разделу «Статистика», одним запросом.

    Фильтры применяются к сданным работам: период считается по времени сдачи,
    предмет — по контрольной, класс — по ученику.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    # Условие для выборок по работам. Собираем один раз, чтобы фильтры
    # применялись одинаково во всех разрезах.
    where = ["a.finished_at IS NOT NULL"]
    params: list = []
    if since is not None:
        where.append("a.finished_at >= %s")
        params.append(since)
    if subject:
        where.append("t.subject = %s")
        params.append(subject)
    if student_class:
        where.append("a.student_class = %s")
        params.append(student_class)
    condition = " AND ".join(where)

    with pool.connection() as conn:
        # --- Сводка ---
        totals = conn.execute(
            """
            SELECT
                (SELECT count(*) FROM users WHERE role = 'teacher') AS teachers,
                (SELECT count(*) FROM tests)                        AS tests,
                (SELECT count(*) FROM attempts WHERE finished_at IS NOT NULL) AS attempts_total,
                (SELECT count(*) FROM attempts
                  WHERE finished_at >= now() - interval '7 days')   AS attempts_week,
                (SELECT count(*) FROM attempts
                  WHERE finished_at >= now() - interval '30 days')  AS attempts_month
            """
        ).fetchone()

        # --- По учителям ---
        by_teacher = conn.execute(
            f"""
            SELECT u.id, u.full_name,
                   count(DISTINCT t.id) AS tests_count,
                   count(a.id)          AS attempts_count,
                   coalesce(round(avg(a.score::numeric * 100 / nullif(a.max_score, 0))), 0)
                       AS average_percent
            FROM users u
            LEFT JOIN tests t    ON t.teacher_id = u.id
            LEFT JOIN attempts a ON a.test_id = t.id AND {condition}
            WHERE u.role = 'teacher'
            GROUP BY u.id
            ORDER BY attempts_count DESC, u.full_name
            """,
            params,
        ).fetchall()

        # --- По предметам ---
        by_subject = conn.execute(
            f"""
            SELECT coalesce(nullif(t.subject, ''), 'не указан') AS subject,
                   count(a.id) AS attempts_count,
                   coalesce(round(avg(a.score::numeric * 100 / nullif(a.max_score, 0))), 0)
                       AS average_percent
            FROM attempts a
            JOIN tests t ON t.id = a.test_id
            WHERE {condition}
            GROUP BY 1
            ORDER BY attempts_count DESC
            """,
            params,
        ).fetchall()

        # --- По классам ---
        by_class = conn.execute(
            f"""
            SELECT a.student_class AS student_class,
                   count(a.id) AS attempts_count,
                   coalesce(round(avg(a.score::numeric * 100 / nullif(a.max_score, 0))), 0)
                       AS average_percent
            FROM attempts a
            JOIN tests t ON t.id = a.test_id
            WHERE {condition}
            GROUP BY 1
            ORDER BY average_percent
            """,
            params,
        ).fetchall()

        # --- Слабые умения по школе ---
        # Считаем по ответам: у каждого ответа известно задание, у задания — умение.
        weak_skills = conn.execute(
            f"""
            SELECT s.id, s.title, t.subject, t.title AS test_title,
                   u.full_name AS teacher_name, a.student_class,
                   count(*) AS answers_count,
                   round(count(*) FILTER (WHERE ans.is_correct)::numeric * 100 / count(*))
                       AS percent
            FROM answers ans
            JOIN attempts a ON a.id = ans.attempt_id
            JOIN tasks tk   ON tk.id = ans.task_id
            JOIN skills s   ON s.id = tk.skill_id
            JOIN tests t    ON t.id = a.test_id
            JOIN users u    ON u.id = t.teacher_id
            WHERE {condition}
            GROUP BY s.id, s.title, t.subject, t.title, u.full_name, a.student_class
            HAVING count(*) > 0
            ORDER BY percent, answers_count DESC
            LIMIT 30
            """,
            params,
        ).fetchall()

        # --- Активность по дням ---
        by_day = conn.execute(
            f"""
            SELECT a.finished_at::date AS day, count(*) AS attempts_count
            FROM attempts a
            JOIN tests t ON t.id = a.test_id
            WHERE {condition}
            GROUP BY 1
            ORDER BY 1
            """,
            params,
        ).fetchall()

        # --- Списки для фильтров ---
        subjects = conn.execute(
            "SELECT DISTINCT subject FROM tests WHERE subject <> '' ORDER BY subject"
        ).fetchall()
        classes = conn.execute(
            "SELECT DISTINCT student_class FROM attempts ORDER BY student_class"
        ).fetchall()

    return {
        "totals": {
            "teachers": totals["teachers"],
            "tests": totals["tests"],
            "attempts_total": totals["attempts_total"],
            "attempts_week": totals["attempts_week"],
            "attempts_month": totals["attempts_month"],
        },
        "by_teacher": [
            {
                "id": row["id"],
                "full_name": row["full_name"],
                "tests_count": row["tests_count"],
                "attempts_count": row["attempts_count"],
                "average_percent": int(row["average_percent"]),
            }
            for row in by_teacher
        ],
        "by_subject": [
            {
                "subject": row["subject"],
                "attempts_count": row["attempts_count"],
                "average_percent": int(row["average_percent"]),
            }
            for row in by_subject
        ],
        "by_class": [
            {
                "student_class": row["student_class"],
                "attempts_count": row["attempts_count"],
                "average_percent": int(row["average_percent"]),
            }
            for row in by_class
        ],
        "weak_skills": [
            {
                "skill_id": row["id"],
                "title": row["title"],
                "subject": row["subject"],
                "test_title": row["test_title"],
                "teacher_name": row["teacher_name"],
                "student_class": row["student_class"],
                "answers_count": row["answers_count"],
                "percent": int(row["percent"]),
                "is_weak": int(row["percent"]) < WEAK_SKILL_PERCENT,
            }
            for row in weak_skills
        ],
        "by_day": [
            {
                "day": row["day"].isoformat() if isinstance(row["day"], date) else str(row["day"]),
                "attempts_count": row["attempts_count"],
            }
            for row in by_day
        ],
        "filters": {
            "subjects": [row["subject"] for row in subjects],
            "classes": [row["student_class"] for row in classes],
            "weak_below": WEAK_SKILL_PERCENT,
        },
    }
