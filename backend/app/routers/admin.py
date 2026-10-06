"""
Админка: учителя, настройки школы и статистика.

    GET   /api/admin/teachers                      — список учителей
    PATCH /api/admin/teachers/{id}                 — заблокировать / разблокировать
    POST  /api/admin/teachers/{id}/reset-password  — выдать временный пароль
    GET   /api/admin/settings                      — школьный код
    PUT   /api/admin/settings                      — сменить школьный код
    PUT   /api/admin/teachers/{id}/role            — выдать / снять права администратора
    GET   /api/admin/log                           — журнал выдачи прав
    GET   /api/admin/stats                         — статистика по школе
    GET   /api/stats                               — статистика: учителю — по его
                                                     работам, администратору — вся

Эндпоинты /api/admin/* требуют роль admin (зависимость require_admin).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from psycopg import errors as pg_errors

from app import db
from app.auth import db_unavailable, forbidden, require_admin, require_user
from app.schemas import (
    PasswordResetOut,
    RoleUpdate,
    SettingsOut,
    SettingsUpdate,
    TeacherRow,
    TeacherUpdate,
)
from app.security import hash_password, temporary_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])
# Статистика доступна и учителю (только по своим работам) — адрес без /admin.
stats_router = APIRouter(prefix="/api/stats", tags=["stats"])

# Ниже этого процента умение считаем несформированным (красная зона).
WEAK_SKILL_PERCENT = 50


# Список учителей с рабочими цифрами и состоянием приглашения. Один запрос
# на оба случая: весь список и одна строка после изменения.
TEACHERS_SQL = """
    SELECT u.id, u.full_name, u.email, u.role, u.is_active, u.status,
           u.created_at, u.last_login_at,
           count(DISTINCT t.id) AS tests_count,
           count(a.id)          AS attempts_count,
           mail.created_at      AS invite_sent_at,
           mail.status          AS invite_mail_status,
           coalesce(mail.error, '') AS invite_mail_error,
           link.expires_at      AS invite_expires_at
    FROM users u
    LEFT JOIN tests t    ON t.teacher_id = u.id
    LEFT JOIN attempts a ON a.test_id = t.id AND a.finished_at IS NOT NULL
                        AND a.annulled_at IS NULL
    -- Последнее письмо-приглашение этому человеку.
    LEFT JOIN LATERAL (
        SELECT m.created_at, m.status, m.error FROM mail_log m
        WHERE m.user_id = u.id AND m.kind = 'invite'
        ORDER BY m.id DESC LIMIT 1
    ) mail ON TRUE
    -- Действующая (не использованная и не заменённая) ссылка-приглашение.
    LEFT JOIN LATERAL (
        SELECT k.expires_at FROM auth_tokens k
        WHERE k.user_id = u.id AND k.kind = 'invite'
          AND k.used_at IS NULL AND k.revoked_at IS NULL
        ORDER BY k.id DESC LIMIT 1
    ) link ON TRUE
    {where}
    GROUP BY u.id, mail.created_at, mail.status, mail.error, link.expires_at
    ORDER BY u.role, u.full_name
"""


def teacher_row(row: dict) -> TeacherRow:
    """Строка списка + понятное состояние учётки для столбца «Статус»."""
    if not row["is_active"]:
        state = "disabled"
    elif row["status"] == "invited":
        expires = row["invite_expires_at"]
        alive = expires is not None and expires > datetime.now(timezone.utc)
        state = "invited" if alive else "invite_expired"
    else:
        state = "active"
    return TeacherRow(**{**row, "state": state})


def percent_of(correct: int | None, total: int | None) -> int:
    if not correct or not total:
        return 0
    return round(correct * 100 / total)


@router.get("/teachers", response_model=list[TeacherRow], summary="Список учителей")
def list_teachers(admin: dict = Depends(require_admin)) -> list[TeacherRow]:
    """
    Все учётные записи с рабочими цифрами: сколько проверочных работ создано
    и сколько работ по ним сдано. По ним видно, кто пользуется сервисом.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    try:
        with pool.connection() as conn:
            rows = conn.execute(TEACHERS_SQL.format(where="")).fetchall()
    except (pg_errors.UndefinedTable, pg_errors.UndefinedColumn) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Список учителей недоступен: на сервере не накачена миграция 009.",
        ) from exc

    return [teacher_row(row) for row in rows]


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
    Блокировка не удаляет ни учётку, ни проверочные работы — человек просто перестаёт
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
            TEACHERS_SQL.format(where="WHERE u.id = %s"), (user_id,)
        ).fetchone()

    logger.info(
        "Администратор %s %s учётку %s",
        admin["email"],
        "разблокировал" if payload.is_active else "заблокировал",
        user_id,
    )
    return teacher_row(row)


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
            """
            UPDATE users SET password_hash = %s, status = 'active'
            WHERE id = %s RETURNING id, email
            """,
            (hash_password(new_password), user_id),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Такого пользователя нет.",
            )

        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        # Пароль выдан лично — письма-приглашения и ссылки сброса больше не нужны.
        conn.execute(
            """
            UPDATE auth_tokens SET revoked_at = now()
            WHERE user_id = %s AND used_at IS NULL AND revoked_at IS NULL
            """,
            (user_id,),
        )

    logger.info("Администратор %s сбросил пароль пользователю %s", admin["email"], row["email"])
    return PasswordResetOut(
        user_id=row["id"],
        email=row["email"],
        temporary_password=new_password,
    )


@router.put(
    "/teachers/{user_id}/role",
    response_model=TeacherRow,
    summary="Сделать администратором или снять права",
)
def update_role(
    payload: RoleUpdate,
    user_id: int = Path(ge=1),
    admin: dict = Depends(require_admin),
) -> TeacherRow:
    """
    Права действуют сразу: роль читается из базы при каждом запросе.

    Два запрета, чтобы школа не осталась без администратора:
      * нельзя снять права с самого себя;
      * нельзя снять права с последнего действующего администратора.
    Каждое изменение пишется в журнал admin_log.
    """
    if user_id == admin["id"] and payload.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя снять права администратора с самого себя — попросите другого администратора.",
        )

    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        with conn.transaction():
            # Блокируем всех администраторов разом: два одновременных «Снять права»
            # не должны оба увидеть «администраторов ещё двое».
            admins = conn.execute(
                "SELECT id, is_active FROM users WHERE role = 'admin' FOR UPDATE"
            ).fetchall()
            target = conn.execute(
                "SELECT id, full_name, role, is_active FROM users WHERE id = %s FOR UPDATE",
                (user_id,),
            ).fetchone()

            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Такого пользователя нет.",
                )

            if target["role"] != payload.role:
                if payload.role == "teacher":
                    others = [
                        row for row in admins if row["id"] != user_id and row["is_active"]
                    ]
                    if not others:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Это последний администратор — снять с него права нельзя.",
                        )
                elif not target["is_active"]:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Учётная запись отключена — сначала включите её.",
                    )

                conn.execute("UPDATE users SET role = %s WHERE id = %s", (payload.role, user_id))
                conn.execute(
                    """
                    INSERT INTO admin_log (admin_id, admin_name, action, target_id, target_name)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        admin["id"],
                        admin["full_name"],
                        "grant_admin" if payload.role == "admin" else "revoke_admin",
                        user_id,
                        target["full_name"],
                    ),
                )
                logger.info(
                    "Администратор %s %s: %s (id %s)",
                    admin["email"],
                    "выдал права администратора"
                    if payload.role == "admin"
                    else "снял права администратора",
                    target["full_name"],
                    user_id,
                )

        row = conn.execute(
            TEACHERS_SQL.format(where="WHERE u.id = %s"), (user_id,)
        ).fetchone()

    return teacher_row(row)


@router.get("/log", summary="Журнал выдачи прав администратора")
def admin_log(admin: dict = Depends(require_admin)) -> list[dict]:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, created_at, admin_name, action, target_name
            FROM admin_log ORDER BY id DESC LIMIT 50
            """
        ).fetchall()


def read_settings(conn) -> SettingsOut:
    values = {
        row["key"]: row["value"]
        for row in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('school_code', 'allow_self_registration')"
        ).fetchall()
    }
    return SettingsOut(
        school_code=values.get("school_code", ""),
        allow_self_registration=values.get("allow_self_registration") == "true",
    )


@router.get("/settings", response_model=SettingsOut, summary="Настройки школы")
def get_settings(admin: dict = Depends(require_admin)) -> SettingsOut:
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    with pool.connection() as conn:
        return read_settings(conn)


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
        if payload.allow_self_registration is not None:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('allow_self_registration', %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                ("true" if payload.allow_self_registration else "false",),
            )
        result = read_settings(conn)

    logger.info(
        "Администратор %s изменил настройки школы (регистрация по коду: %s)",
        admin["email"],
        "разрешена" if result.allow_self_registration else "выключена",
    )
    return result


@router.get("/stats", summary="Статистика по школе")
def stats(
    admin: dict = Depends(require_admin),
    days: int = Query(default=0, ge=0, le=3650, description="Период в днях, 0 — за всё время"),
    subject: str = Query(default="", description="Фильтр по предмету"),
    student_class: str = Query(default="", description="Фильтр по классу"),
    test_id: int = Query(default=0, ge=0, description="Только эта работа, 0 — все"),
) -> dict:
    """Статистика по всей школе — то же, что /api/stats для администратора."""
    return build_stats(admin, days, subject, student_class, test_id)


@stats_router.get("", summary="Статистика: учителю — по своим работам, администратору — вся")
def my_stats(
    user: dict = Depends(require_user),
    days: int = Query(default=0, ge=0, le=3650, description="Период в днях, 0 — за всё время"),
    subject: str = Query(default="", description="Фильтр по предмету"),
    student_class: str = Query(default="", description="Фильтр по классу"),
    test_id: int = Query(default=0, ge=0, description="Только эта работа, 0 — все"),
) -> dict:
    return build_stats(user, days, subject, student_class, test_id)


def build_stats(user: dict, days: int, subject: str, student_class: str, test_id: int) -> dict:
    """
    Всё, что нужно разделу «Статистика», одним запросом.

    Учитель видит только свои работы: условие «автор — я» добавляется на сервере
    ко ВСЕМ выборкам, а чужая работа, запрошенная по номеру, даёт 403.
    Администратор видит всю школу.

    Фильтры применяются к сданным работам: период считается по времени сдачи,
    предмет — по проверочной работе, класс — по ученику. Аннулированные попытки
    (после «Разрешить пересдачу») в статистику не идут.
    """
    try:
        pool = db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc

    own_only = user["role"] != "admin"
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    # Условие для выборок по работам. Собираем один раз, чтобы фильтры
    # применялись одинаково во всех разрезах.
    where = ["a.finished_at IS NOT NULL", "a.annulled_at IS NULL"]
    params: list = []
    if own_only:
        where.append("t.teacher_id = %s")
        params.append(user["id"])
    if test_id:
        where.append("t.id = %s")
        params.append(test_id)
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

    # Чьи работы считаем в сводке и в списках фильтров.
    owner = "t.teacher_id = %(owner)s" if own_only else "TRUE"
    owner_params = {"owner": user["id"]}

    with pool.connection() as conn:
        if test_id:
            # Чужую работу по номеру не отдаём — даже пустой статистикой.
            test_row = conn.execute(
                "SELECT teacher_id FROM tests WHERE id = %s", (test_id,)
            ).fetchone()
            if test_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Проверочная работа не найдена.",
                )
            if own_only and test_row["teacher_id"] != user["id"]:
                raise forbidden(
                    "Это проверочная работа другого учителя — её статистика доступна только автору."
                )

        # --- Сводка ---
        totals = conn.execute(
            f"""
            SELECT
                (SELECT count(*) FROM users WHERE role = 'teacher') AS teachers,
                (SELECT count(*) FROM tests t WHERE {owner})        AS tests,
                (SELECT count(*) FROM attempts a JOIN tests t ON t.id = a.test_id
                  WHERE {owner} AND a.annulled_at IS NULL
                    AND a.finished_at IS NOT NULL)                  AS attempts_total,
                (SELECT count(*) FROM attempts a JOIN tests t ON t.id = a.test_id
                  WHERE {owner} AND a.annulled_at IS NULL
                    AND a.finished_at >= now() - interval '7 days') AS attempts_week,
                (SELECT count(*) FROM attempts a JOIN tests t ON t.id = a.test_id
                  WHERE {owner} AND a.annulled_at IS NULL
                    AND a.finished_at >= now() - interval '30 days') AS attempts_month
            """,
            owner_params,
        ).fetchone()

        # --- По учителям (только администратору) ---
        by_teacher = [] if own_only else conn.execute(
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

        # --- Списки для фильтров (учителю — только из его работ) ---
        subjects = conn.execute(
            f"SELECT DISTINCT t.subject FROM tests t WHERE t.subject <> '' AND {owner} ORDER BY 1",
            owner_params,
        ).fetchall()
        classes = conn.execute(
            f"""
            SELECT DISTINCT a.student_class FROM attempts a
            JOIN tests t ON t.id = a.test_id
            WHERE {owner} AND a.annulled_at IS NULL ORDER BY 1
            """,
            owner_params,
        ).fetchall()
        tests = conn.execute(
            f"SELECT t.id, t.title FROM tests t WHERE {owner} ORDER BY t.created_at DESC, t.id DESC",
            owner_params,
        ).fetchall()

    return {
        # own — учитель видит только свои работы; school — администратор, вся школа.
        "scope": "own" if own_only else "school",
        "totals": {
            "teachers": 0 if own_only else totals["teachers"],
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
            "tests": [{"id": row["id"], "title": row["title"]} for row in tests],
            "weak_below": WEAK_SKILL_PERCENT,
        },
    }
