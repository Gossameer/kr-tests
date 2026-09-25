-- =====================================================================
-- Миграция 001: базовая схема сервиса контрольных работ.
--
-- Накат:  psql -U postgres -d kr_tests -f migrations/001_init.sql
-- Откат:  psql -U postgres -d kr_tests -f migrations/001_init.down.sql
--
-- Пояснения к типам:
--   BIGSERIAL   — целочисленный id, который БД сама увеличивает (1, 2, 3, ...)
--   TIMESTAMPTZ — момент времени с учётом часового пояса
--   REFERENCES ... ON DELETE CASCADE — при удалении «родителя» дети удаляются сами
-- =====================================================================

BEGIN;  -- всё внутри выполнится целиком либо не выполнится вовсе

-- ---------------------------------------------------------------------
-- tests — сама контрольная работа
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tests (
    id           BIGSERIAL   PRIMARY KEY,
    title        TEXT        NOT NULL,
    teacher_name TEXT        NOT NULL,
    -- share_token — случайная строка в ссылке для учеников (/t/<share_token>).
    -- UNIQUE, потому что именно по нему находим контрольную.
    share_token  TEXT        NOT NULL UNIQUE,
    is_published BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- questions — вопросы контрольной
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS questions (
    id       BIGSERIAL PRIMARY KEY,
    test_id  BIGINT    NOT NULL REFERENCES tests (id) ON DELETE CASCADE,
    text     TEXT      NOT NULL,
    -- position — порядок вопроса в контрольной (1, 2, 3, ...).
    position INTEGER   NOT NULL DEFAULT 0
);

-- Индекс: почти всегда выбираем «все вопросы одной контрольной по порядку».
CREATE INDEX IF NOT EXISTS idx_questions_test_id_position
    ON questions (test_id, position);

-- ---------------------------------------------------------------------
-- options — варианты ответа на вопрос
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS options (
    id          BIGSERIAL PRIMARY KEY,
    question_id BIGINT    NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
    text        TEXT      NOT NULL,
    -- is_correct — правильный ли это вариант. Ученику это поле не отдаём!
    is_correct  BOOLEAN   NOT NULL DEFAULT FALSE,
    position    INTEGER   NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_options_question_id_position
    ON options (question_id, position);

-- ---------------------------------------------------------------------
-- attempts — попытка прохождения контрольной одним учеником
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attempts (
    id           BIGSERIAL   PRIMARY KEY,
    -- Без CASCADE: контрольную с результатами нельзя удалить случайно.
    -- Если понадобится удалить — сначала удаляем попытки явно.
    test_id      BIGINT      NOT NULL REFERENCES tests (id),
    student_name TEXT        NOT NULL,
    -- score/max_score заполняются, когда работа сдана; до этого NULL.
    score        INTEGER,
    max_score    INTEGER,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_attempts_test_id
    ON attempts (test_id);

-- ---------------------------------------------------------------------
-- answers — конкретный ответ ученика на конкретный вопрос
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answers (
    id          BIGSERIAL PRIMARY KEY,
    attempt_id  BIGINT    NOT NULL REFERENCES attempts (id)  ON DELETE CASCADE,
    question_id BIGINT    NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
    -- option_id может быть NULL: «вопрос пропущен, вариант не выбран».
    option_id   BIGINT             REFERENCES options (id)   ON DELETE CASCADE,
    -- На один вопрос внутри одной попытки — ровно один ответ.
    CONSTRAINT uq_answers_attempt_question UNIQUE (attempt_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_answers_attempt_id
    ON answers (attempt_id);

COMMIT;
