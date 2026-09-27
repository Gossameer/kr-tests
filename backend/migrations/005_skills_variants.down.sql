-- =====================================================================
-- Откат миграции 005: возврат к модели «вопрос → варианты ответа».
--
-- ВНИМАНИЕ: откат удаляет ВСЕ контрольные новой модели вместе с работами.
-- Восстановить удалённые миграцией 005 старые контрольные он не может —
-- их данных больше нет нигде.
--
-- Запуск: psql -U postgres -d kr_tests -f migrations/005_skills_variants.down.sql
-- =====================================================================

BEGIN;

-- Сначала данные: иначе внешние ключи не дадут удалить таблицы.
DELETE FROM tests;

DROP TABLE IF EXISTS answers;
DROP TABLE IF EXISTS task_options;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS skills;

ALTER TABLE attempts DROP COLUMN IF EXISTS attempt_token;
ALTER TABLE attempts DROP COLUMN IF EXISTS variant_no;

ALTER TABLE tests DROP CONSTRAINT IF EXISTS tests_variants_count_positive;
ALTER TABLE tests DROP COLUMN IF EXISTS variant_counter;
ALTER TABLE tests DROP COLUMN IF EXISTS variants_count;

-- Возвращаем таблицы старой модели в том виде, в каком их создавала 001.
CREATE TABLE IF NOT EXISTS questions (
    id       BIGSERIAL PRIMARY KEY,
    test_id  BIGINT    NOT NULL REFERENCES tests (id) ON DELETE CASCADE,
    text     TEXT      NOT NULL,
    position INTEGER   NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_questions_test_id_position
    ON questions (test_id, position);

CREATE TABLE IF NOT EXISTS options (
    id          BIGSERIAL PRIMARY KEY,
    question_id BIGINT    NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
    text        TEXT      NOT NULL,
    is_correct  BOOLEAN   NOT NULL DEFAULT FALSE,
    position    INTEGER   NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_options_question_id_position
    ON options (question_id, position);

CREATE TABLE IF NOT EXISTS answers (
    id          BIGSERIAL PRIMARY KEY,
    attempt_id  BIGINT    NOT NULL REFERENCES attempts (id)  ON DELETE CASCADE,
    question_id BIGINT    NOT NULL REFERENCES questions (id) ON DELETE CASCADE,
    option_id   BIGINT             REFERENCES options (id)   ON DELETE CASCADE,
    CONSTRAINT uq_answers_attempt_question UNIQUE (attempt_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_answers_attempt_id
    ON answers (attempt_id);

CREATE INDEX IF NOT EXISTS idx_answers_question_id
    ON answers (question_id);

COMMIT;
