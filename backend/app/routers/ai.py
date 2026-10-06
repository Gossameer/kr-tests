"""
Генерация заданий через ИИ.

    GET  /api/ai/status                              — включена ли генерация, лимит на сегодня
    POST /api/ai/jobs                                — запустить генерацию проверочной работы
    GET  /api/ai/jobs/{id}                           — прогресс и результат
    POST /api/ai/jobs/{id}/resume                    — догенерировать недостающее
    POST /api/ai/jobs/{id}/variants/{no}/retry       — то же, но для одного варианта
    POST /api/ai/task-jobs                           — перегенерировать одно задание

    GET  /api/admin/ai                               — расход и лимит (администратор)
    PUT  /api/admin/ai                               — сменить дневной лимит

Сама генерация — в app/ai_generation.py, HTTP-запросы — в app/ai_client.py.
Ключ ИИ ни в одном ответе не возвращается.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, status
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app import ai_generation, db
from app.auth import db_unavailable, require_admin, require_user
from app.config import get_settings
from app.schemas import MAX_SKILLS, MAX_TASK_LEN, MAX_TITLE_LEN, MAX_VARIANTS, SkillIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])
admin_router = APIRouter(prefix="/api/admin/ai", tags=["admin"])

DEFAULT_DAILY_LIMIT = 10
MAX_DAILY_LIMIT = 1000
# Сколько уже готовых заданий фронтенд может прислать «чтобы не повторяться».
MAX_AVOID_TEXTS = 60


# =====================================================================
# Схемы
# =====================================================================


class GenerationContext(BaseModel):
    """Общее для генерации проверочной работы и одного задания."""

    subject: str = ""
    topic: str = ""
    grade: str = ""
    skills: list[SkillIn]

    @field_validator("subject", "topic", "grade")
    @classmethod
    def trim(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) > MAX_TITLE_LEN:
            raise ValueError(f"слишком длинное, максимум {MAX_TITLE_LEN} символов")
        return cleaned

    @field_validator("skills")
    @classmethod
    def check_skills(cls, value: list[SkillIn]) -> list[SkillIn]:
        if not value:
            raise ValueError("добавьте хотя бы одно умение")
        if len(value) > MAX_SKILLS:
            raise ValueError(f"слишком много умений, максимум {MAX_SKILLS}")
        return value


class JobCreate(GenerationContext):
    variants_count: int = Field(ge=1, le=MAX_VARIANTS)


class TaskJobCreate(GenerationContext):
    # Номер умения с единицы — как skill_index в заданиях.
    skill_index: int = Field(ge=1)
    variant_no: int = Field(ge=1, le=MAX_VARIANTS)
    variants_count: int = Field(default=1, ge=1, le=MAX_VARIANTS)
    current_text: str = Field(default="", max_length=MAX_TASK_LEN)
    avoid_texts: list[str] = []

    @field_validator("avoid_texts")
    @classmethod
    def trim_avoid(cls, value: list[str]) -> list[str]:
        cleaned = [text.strip()[:MAX_TASK_LEN] for text in value if text.strip()]
        return cleaned[-MAX_AVOID_TEXTS:]


class LimitUpdate(BaseModel):
    daily_limit: int = Field(ge=0, le=MAX_DAILY_LIMIT)


# =====================================================================
# Помощники
# =====================================================================


def get_pool():
    try:
        return db.get_pool()
    except Exception as exc:  # noqa: BLE001
        raise db_unavailable() from exc


def ai_not_configured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Генерация через ИИ не настроена на сервере. "
        "Воспользуйтесь ручным путём: скопируйте промт во внешний ИИ.",
    )


def migration_missing() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Генерация через ИИ пока не подключена. Скопируйте промт вручную — кнопка «или скопировать промт вручную».",
    )


def daily_limit(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = 'ai_daily_limit'").fetchone()
    try:
        return int(row["value"]) if row else DEFAULT_DAILY_LIMIT
    except ValueError:
        return DEFAULT_DAILY_LIMIT


def used_today(conn, teacher_id: int) -> int:
    """
    Сколько генераций проверочных работ учитель запустил сегодня.

    «Сегодня» — по часовому поясу базы (на сервере TZ=Europe/Moscow).
    Замены отдельных заданий и повторы вариантов в лимит не входят:
    это доводка уже запущенной генерации, их видно в журнале расхода.
    """
    row = conn.execute(
        """
        SELECT count(*) AS used FROM ai_jobs
        WHERE teacher_id = %s AND kind = 'test'
          AND created_at >= date_trunc('day', now())
        """,
        (teacher_id,),
    ).fetchone()
    return row["used"]


def request_dict(payload: GenerationContext, **extra) -> dict:
    return {
        "subject": payload.subject,
        "topic": payload.topic,
        "grade": payload.grade,
        "skills": [skill.model_dump() for skill in payload.skills],
        **extra,
    }


def own_job(job_id: int, user: dict) -> dict:
    """Задание текущего учителя или 404 (чужие не показываем даже админу)."""
    try:
        row = ai_generation.load_job_row(job_id)
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc
    if row is None or row["teacher_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Генерация не найдена.")
    return row


# =====================================================================
# Учитель
# =====================================================================


@router.get("/status", summary="Доступна ли генерация через ИИ")
def ai_status(user: dict = Depends(require_user)) -> dict:
    """
    enabled=false → фронтенд показывает только ручной путь.
    Ни ключа, ни адреса сервиса здесь нет.
    """
    settings = get_settings()
    if not settings.ai_enabled:
        return {"enabled": False, "daily_limit": 0, "used_today": 0}

    pool = get_pool()
    try:
        with pool.connection() as conn:
            return {
                "enabled": True,
                "daily_limit": daily_limit(conn),
                "used_today": used_today(conn, user["id"]),
            }
    except pg_errors.UndefinedTable:
        # Ключ задан, а таблиц нет — честно выключаем кнопку.
        return {"enabled": False, "daily_limit": 0, "used_today": 0}


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, summary="Запустить генерацию")
def create_job(payload: JobCreate, user: dict = Depends(require_user)) -> dict:
    if not get_settings().ai_enabled:
        raise ai_not_configured()

    pool = get_pool()
    try:
        with pool.connection() as conn, conn.transaction():
            # Блокируем строку учителя: два одновременных нажатия не должны
            # оба пройти проверку лимита.
            conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user["id"],))

            running = conn.execute(
                """
                SELECT id FROM ai_jobs
                WHERE teacher_id = %s AND kind = 'test' AND status = 'running'
                LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
            if running is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Генерация уже идёт — дождитесь её окончания.",
                )

            limit = daily_limit(conn)
            used = used_today(conn, user["id"])
            if used >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Лимит генераций на сегодня исчерпан: {used} из {limit}. "
                        "Завтра он обновится, а пока задания можно получить вручную — "
                        "скопируйте промт во внешний ИИ."
                        if limit > 0
                        else "Генерация через ИИ отключена администратором. "
                        "Задания можно получить вручную — скопируйте промт во внешний ИИ."
                    ),
                )

            row = conn.execute(
                """
                INSERT INTO ai_jobs (teacher_id, kind, status, request, result)
                VALUES (%s, 'test', 'running', %s, %s)
                RETURNING id
                """,
                (
                    user["id"],
                    Jsonb(
                        request_dict(payload, variants_count=payload.variants_count)
                    ),
                    Jsonb(
                        ai_generation.initial_result(
                            payload.variants_count, len(payload.skills)
                        )
                    ),
                ),
            ).fetchone()
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    job_id = row["id"]
    logger.info(
        "Учитель %s запустил генерацию %s: умений %s, вариантов %s",
        user["email"],
        job_id,
        len(payload.skills),
        payload.variants_count,
    )
    ai_generation.start_test_job(job_id, user["id"])
    return {"job_id": job_id}


@router.get("/jobs/{job_id}", summary="Прогресс и результат генерации")
def get_job(job_id: int = Path(ge=1), user: dict = Depends(require_user)) -> dict:
    row = own_job(job_id, user)
    # Задание числится «идёт», но его давно никто не выполняет (сервер
    # перезапустили) — закрываем его как прерванное, чтобы клетки не висели
    # «составляется» вечно, и отдаём уже честное состояние.
    if ai_generation.orphaned(row):
        logger.info("Генерация %s осиротела — помечаем прерванной", job_id)
        ai_generation.mark_interrupted(job_id)
        row = own_job(job_id, user)
    return ai_generation.job_view(row)


class ResumeIn(BaseModel):
    # Пусто — догенерировать всё недостающее; номер — только один вариант.
    variant_no: int | None = Field(default=None, ge=1, le=MAX_VARIANTS)


def _resume(job_id: int, user: dict, variant_no: int | None) -> dict:
    if not get_settings().ai_enabled:
        raise ai_not_configured()

    row = own_job(job_id, user)
    if row["kind"] != "test":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Генерация не найдена.")
    if ai_generation.orphaned(row):
        ai_generation.mark_interrupted(job_id)

    count, problem = ai_generation.prepare_resume(job_id, variant_no)
    if problem:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)

    logger.info(
        "Учитель %s догенерирует генерацию %s: клеток %s%s",
        user["email"],
        job_id,
        count,
        f", вариант {variant_no}" if variant_no else "",
    )
    ai_generation.start_test_job(job_id, user["id"])
    return {"job_id": job_id, "cells": count}


@router.post("/jobs/{job_id}/resume", summary="Догенерировать недостающее")
def resume_job(
    payload: ResumeIn | None = None,
    job_id: int = Path(ge=1),
    user: dict = Depends(require_user),
) -> dict:
    """
    Заново составляет только те клетки, которых нет: не удались или прерваны
    перезапуском сервера. Готовые задания (и правки учителя в них) не трогаем.
    В дневной лимит не входит: это доводка уже запущенной генерации.
    """
    return _resume(job_id, user, payload.variant_no if payload else None)


@router.post(
    "/jobs/{job_id}/variants/{variant_no}/retry",
    summary="Догенерировать недостающее в одном варианте",
)
def retry_variant(
    job_id: int = Path(ge=1),
    variant_no: int = Path(ge=1, le=MAX_VARIANTS),
    user: dict = Depends(require_user),
) -> dict:
    return _resume(job_id, user, variant_no)


@router.post(
    "/task-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Перегенерировать одно задание",
)
def create_task_job(payload: TaskJobCreate, user: dict = Depends(require_user)) -> dict:
    if not get_settings().ai_enabled:
        raise ai_not_configured()
    if payload.skill_index > len(payload.skills):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Умения №{payload.skill_index} нет в списке.",
        )

    pool = get_pool()
    try:
        with pool.connection() as conn:
            # Выключенная администратором генерация (лимит 0) выключает и замены.
            if daily_limit(conn) <= 0:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Генерация через ИИ отключена администратором.",
                )
            row = conn.execute(
                """
                INSERT INTO ai_jobs (teacher_id, kind, status, request)
                VALUES (%s, 'task', 'running', %s)
                RETURNING id
                """,
                (
                    user["id"],
                    Jsonb(
                        request_dict(
                            payload,
                            variants_count=payload.variants_count,
                            skill_index=payload.skill_index,
                            variant_no=payload.variant_no,
                            current_text=payload.current_text,
                            avoid_texts=payload.avoid_texts,
                        )
                    ),
                ),
            ).fetchone()
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    ai_generation.start_task_job(row["id"], user["id"])
    return {"job_id": row["id"]}


# =====================================================================
# Администратор: расход и лимит
# =====================================================================


def _usage(conn, since: datetime) -> dict:
    """Расход с момента since: отдельно генерация и самопроверка."""
    rows = conn.execute(
        """
        SELECT kind,
               count(*)                              AS requests,
               count(*) FILTER (WHERE NOT success)   AS failed,
               coalesce(sum(prompt_tokens), 0)       AS prompt_tokens,
               coalesce(sum(completion_tokens), 0)   AS completion_tokens
        FROM ai_requests
        WHERE created_at >= %s
        GROUP BY kind
        """,
        (since,),
    ).fetchall()

    empty = {"requests": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0}
    usage = {"generate": dict(empty), "check": dict(empty)}
    for row in rows:
        usage[row["kind"]] = {
            "requests": row["requests"],
            "failed": row["failed"],
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
        }
    return usage


@admin_router.get("", summary="Расход ИИ и лимит")
def admin_ai(admin: dict = Depends(require_admin)) -> dict:
    settings = get_settings()
    pool = get_pool()

    try:
        with pool.connection() as conn:
            bounds = conn.execute(
                """
                SELECT date_trunc('day', now())   AS day_start,
                       date_trunc('month', now()) AS month_start
                """
            ).fetchone()
            day_start = bounds["day_start"]
            month_start = bounds["month_start"]

            by_teacher = conn.execute(
                """
                SELECT u.id, u.full_name, u.email,
                       (SELECT count(*) FROM ai_jobs j
                         WHERE j.teacher_id = u.id AND j.kind = 'test'
                           AND j.created_at >= %(day)s)                  AS jobs_today,
                       (SELECT count(*) FROM ai_jobs j
                         WHERE j.teacher_id = u.id AND j.kind = 'test'
                           AND j.created_at >= %(month)s)                AS jobs_month,
                       coalesce(sum(r.prompt_tokens)     FILTER (WHERE r.kind = 'generate' AND r.created_at >= %(day)s), 0)   AS gen_in_day,
                       coalesce(sum(r.completion_tokens) FILTER (WHERE r.kind = 'generate' AND r.created_at >= %(day)s), 0)   AS gen_out_day,
                       coalesce(sum(r.prompt_tokens)     FILTER (WHERE r.kind = 'check'    AND r.created_at >= %(day)s), 0)   AS check_in_day,
                       coalesce(sum(r.completion_tokens) FILTER (WHERE r.kind = 'check'    AND r.created_at >= %(day)s), 0)   AS check_out_day,
                       coalesce(sum(r.prompt_tokens)     FILTER (WHERE r.kind = 'generate'), 0) AS gen_in_month,
                       coalesce(sum(r.completion_tokens) FILTER (WHERE r.kind = 'generate'), 0) AS gen_out_month,
                       coalesce(sum(r.prompt_tokens)     FILTER (WHERE r.kind = 'check'),    0) AS check_in_month,
                       coalesce(sum(r.completion_tokens) FILTER (WHERE r.kind = 'check'),    0) AS check_out_month,
                       count(r.id) AS requests_month
                FROM users u
                LEFT JOIN ai_requests r
                       ON r.teacher_id = u.id AND r.created_at >= %(month)s
                GROUP BY u.id
                HAVING count(r.id) > 0
                    OR (SELECT count(*) FROM ai_jobs j
                         WHERE j.teacher_id = u.id AND j.created_at >= %(month)s) > 0
                ORDER BY requests_month DESC, u.full_name
                """,
                {"day": day_start, "month": month_start},
            ).fetchall()

            result = {
                "enabled": settings.ai_enabled,
                "model": settings.ai_model,
                "check_model": settings.ai_check_model_name,
                "daily_limit": daily_limit(conn),
                "today": _usage(conn, day_start),
                "month": _usage(conn, month_start),
                "by_teacher": [
                    {
                        "id": row["id"],
                        "full_name": row["full_name"],
                        "email": row["email"],
                        "jobs_today": row["jobs_today"],
                        "jobs_month": row["jobs_month"],
                        "generate_day": {
                            "prompt_tokens": int(row["gen_in_day"]),
                            "completion_tokens": int(row["gen_out_day"]),
                        },
                        "check_day": {
                            "prompt_tokens": int(row["check_in_day"]),
                            "completion_tokens": int(row["check_out_day"]),
                        },
                        "generate_month": {
                            "prompt_tokens": int(row["gen_in_month"]),
                            "completion_tokens": int(row["gen_out_month"]),
                        },
                        "check_month": {
                            "prompt_tokens": int(row["check_in_month"]),
                            "completion_tokens": int(row["check_out_month"]),
                        },
                    }
                    for row in by_teacher
                ],
            }
    except pg_errors.UndefinedTable as exc:
        raise migration_missing() from exc

    return result


@admin_router.put("", summary="Сменить дневной лимит генераций")
def update_limit(payload: LimitUpdate, admin: dict = Depends(require_admin)) -> dict:
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES ('ai_daily_limit', %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (str(payload.daily_limit),),
        )
    logger.info("Администратор %s сменил лимит генераций: %s", admin["email"], payload.daily_limit)
    return {"daily_limit": payload.daily_limit}

