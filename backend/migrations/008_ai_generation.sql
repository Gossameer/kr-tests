-- =====================================================================
-- Миграция 008: встроенная генерация вариантов через ИИ.
--
-- Накат:
--   $env:PGCLIENTENCODING="UTF8"
--   psql -U postgres -d kr_tests -f migrations/008_ai_generation.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/008_ai_generation.down.sql
--
-- Что появляется:
--   ai_jobs      — фоновые задания генерации: прогресс и результат. Результат
--                  лежит в базе, поэтому учитель может обновить страницу
--                  или уйти с неё и вернуться — генерация не теряется.
--   ai_requests  — журнал КАЖДОГО запроса к ИИ: кто, когда, какая модель,
--                  генерация или самопроверка, сколько токенов, успех.
--                  По нему считаются расход в админке и лимит.
--   settings.ai_daily_limit — сколько генераций в день доступно учителю.
--
-- Ни ключ ИИ, ни тексты запросов и ответов в журнал не пишутся.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Фоновые задания
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_jobs (
    id          BIGSERIAL   PRIMARY KEY,
    teacher_id  BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- 'test' — вся контрольная (считается в дневной лимит),
    -- 'task' — перегенерация одного задания.
    kind        TEXT        NOT NULL DEFAULT 'test',
    -- 'running' — идёт; 'done' — закончено (часть вариантов может не удаться);
    -- 'failed'  — задание сорвалось целиком (нет ключа, нет денег, перезапуск).
    status      TEXT        NOT NULL DEFAULT 'running',
    -- Что просили: умения, число вариантов, предмет, тема, класс.
    request     JSONB       NOT NULL,
    -- Итог: {"variants": [...]} или {"task": {...}}. Заполняется по мере готовности.
    result      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- Ошибка всего задания (у отдельных вариантов — свои, внутри result).
    error       TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,

    CONSTRAINT ai_jobs_kind_known   CHECK (kind IN ('test', 'task')),
    CONSTRAINT ai_jobs_status_known CHECK (status IN ('running', 'done', 'failed'))
);

-- Лимит считается по учителю за день — индекс ровно под этот запрос.
CREATE INDEX IF NOT EXISTS idx_ai_jobs_teacher_created
    ON ai_jobs (teacher_id, created_at);

-- ---------------------------------------------------------------------
-- 2. Журнал запросов к ИИ
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_requests (
    id                BIGSERIAL   PRIMARY KEY,
    -- Учитель остаётся в журнале, даже если задание удалят.
    teacher_id        BIGINT      NOT NULL REFERENCES users (id)   ON DELETE CASCADE,
    job_id            BIGINT               REFERENCES ai_jobs (id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    model             TEXT        NOT NULL,
    -- 'generate' — составление заданий, 'check' — самопроверка ответа.
    kind              TEXT        NOT NULL,
    prompt_tokens     INTEGER     NOT NULL DEFAULT 0,
    completion_tokens INTEGER     NOT NULL DEFAULT 0,
    success           BOOLEAN     NOT NULL,
    -- Короткое описание сбоя без ключа и без текста запроса.
    error             TEXT        NOT NULL DEFAULT '',

    CONSTRAINT ai_requests_kind_known CHECK (kind IN ('generate', 'check'))
);

CREATE INDEX IF NOT EXISTS idx_ai_requests_created
    ON ai_requests (created_at);

CREATE INDEX IF NOT EXISTS idx_ai_requests_teacher_created
    ON ai_requests (teacher_id, created_at);

-- ---------------------------------------------------------------------
-- 3. Лимит генераций в день на учителя
-- ---------------------------------------------------------------------
INSERT INTO settings (key, value)
VALUES ('ai_daily_limit', '10')
ON CONFLICT (key) DO NOTHING;

COMMIT;
