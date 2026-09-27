-- =====================================================================
-- Миграция 005: контрольная по УМЕНИЯМ с несколькими ВАРИАНТАМИ.
--
-- Накат:  psql -U postgres -d kr_tests -f migrations/005_skills_variants.sql
-- Откат:  psql -U postgres -d kr_tests -f migrations/005_skills_variants.down.sql
--
-- ВНИМАНИЕ: миграция УДАЛЯЕТ все существующие контрольные вместе с работами.
-- Старая модель («один тест на всех, вопрос → варианты ответа») несовместима
-- с новой: у задания появился вариант, умение, формат ответа и решение.
-- Переносить тестовые данные незачем, поэтому таблицы создаются заново.
--
-- Что получается:
--   tests         — добавлены variants_count и счётчик выдачи вариантов
--   skills        — умения контрольной (сколько заданий, какой формат ответа)
--   tasks         — задания: своё на каждый вариант и каждое умение
--   task_options  — варианты ответа для заданий формата «выбор»
--   attempts      — добавлены номер выданного варианта и токен попытки
--   answers       — ответ ученика: выбранный вариант ИЛИ введённый текст
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. Чистим старую модель
-- ---------------------------------------------------------------------

-- Удаляем контрольные: работы и ответы уйдут каскадом (см. миграции 001 и 004).
DELETE FROM tests;

-- Старые таблицы больше не нужны: их место занимают tasks и task_options.
DROP TABLE IF EXISTS answers;
DROP TABLE IF EXISTS options;
DROP TABLE IF EXISTS questions;

-- ---------------------------------------------------------------------
-- 1. Настройки контрольной
-- ---------------------------------------------------------------------

-- Сколько всего вариантов у контрольной.
ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS variants_count INTEGER NOT NULL DEFAULT 1;

-- Счётчик выданных вариантов. Растёт всегда вверх; номер варианта считается
-- как ((variant_counter - 1) % variants_count) + 1.
--
-- Почему счётчик, а не «посчитать число попыток»: UPDATE ... RETURNING берёт
-- блокировку строки, поэтому два ученика, нажавшие «Начать» одновременно,
-- получат разные номера. Подсчёт COUNT(*) в такой ситуации дал бы обоим одно
-- и то же число.
ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS variant_counter BIGINT NOT NULL DEFAULT 0;

ALTER TABLE tests
    ADD CONSTRAINT tests_variants_count_positive CHECK (variants_count >= 1);

-- ---------------------------------------------------------------------
-- 2. Умения
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
    id                BIGSERIAL PRIMARY KEY,
    test_id           BIGINT    NOT NULL REFERENCES tests (id) ON DELETE CASCADE,
    -- Порядок умения в контрольной (1, 2, 3, ...).
    position          INTEGER   NOT NULL DEFAULT 1,
    title             TEXT      NOT NULL,
    -- Сколько заданий на это умение в КАЖДОМ варианте.
    tasks_per_variant INTEGER   NOT NULL DEFAULT 1,
    -- 'input'  — ученик вводит ответ с клавиатуры (по умолчанию),
    -- 'choice' — ученик выбирает один из готовых вариантов.
    answer_format     TEXT      NOT NULL DEFAULT 'input',

    CONSTRAINT skills_tasks_per_variant_positive CHECK (tasks_per_variant >= 1),
    CONSTRAINT skills_answer_format_known CHECK (answer_format IN ('input', 'choice'))
);

CREATE INDEX IF NOT EXISTS idx_skills_test_id_position
    ON skills (test_id, position);

-- ---------------------------------------------------------------------
-- 3. Задания
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id               BIGSERIAL PRIMARY KEY,
    test_id          BIGINT    NOT NULL REFERENCES tests (id)  ON DELETE CASCADE,
    skill_id         BIGINT    NOT NULL REFERENCES skills (id) ON DELETE CASCADE,
    -- Номер варианта: 1..tests.variants_count.
    variant_no       INTEGER   NOT NULL,
    -- Порядок задания внутри варианта.
    position         INTEGER   NOT NULL DEFAULT 1,
    text             TEXT      NOT NULL,
    answer_format    TEXT      NOT NULL DEFAULT 'input',
    -- Для формата 'input': все ответы, которые засчитываются.
    -- Сравнение идёт после нормализации (см. app/answers.py).
    accepted_answers TEXT[]    NOT NULL DEFAULT '{}',
    -- Краткое решение. Видит ТОЛЬКО учитель — ученику не отдаём никогда.
    solution         TEXT      NOT NULL DEFAULT '',

    CONSTRAINT tasks_variant_no_positive CHECK (variant_no >= 1),
    CONSTRAINT tasks_answer_format_known CHECK (answer_format IN ('input', 'choice'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_test_variant
    ON tasks (test_id, variant_no, position);

CREATE INDEX IF NOT EXISTS idx_tasks_skill_id
    ON tasks (skill_id);

-- ---------------------------------------------------------------------
-- 4. Варианты ответа (только для заданий формата 'choice')
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_options (
    id         BIGSERIAL PRIMARY KEY,
    task_id    BIGINT    NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    text       TEXT      NOT NULL,
    -- Ученику это поле не отдаётся никогда.
    is_correct BOOLEAN   NOT NULL DEFAULT FALSE,
    position   INTEGER   NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_task_options_task_id_position
    ON task_options (task_id, position);

-- ---------------------------------------------------------------------
-- 5. Попытки
-- ---------------------------------------------------------------------

-- Какой вариант выдан этому ученику.
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS variant_no INTEGER NOT NULL DEFAULT 1;

-- Секрет попытки. Выдаётся при «Начать» и требуется при сдаче: без него
-- ученик мог бы отправить ответы за чужую попытку, подставив её id.
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS attempt_token TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_token
    ON attempts (attempt_token);

-- ---------------------------------------------------------------------
-- 6. Ответы
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answers (
    id          BIGSERIAL PRIMARY KEY,
    attempt_id  BIGINT    NOT NULL REFERENCES attempts (id)     ON DELETE CASCADE,
    task_id     BIGINT    NOT NULL REFERENCES tasks (id)        ON DELETE CASCADE,
    -- Для формата 'choice': что выбрал ученик. NULL — не выбрал ничего.
    option_id   BIGINT             REFERENCES task_options (id) ON DELETE CASCADE,
    -- Для формата 'input': что ввёл ученик, как есть, без нормализации.
    -- Исходный текст полезен учителю: видно, ошибся ученик по сути или в записи.
    answer_text TEXT,
    -- Вердикт считает сервер при сдаче. Храним готовым, чтобы сводка по умениям
    -- не пересчитывала ответы заново на каждый показ таблицы.
    is_correct  BOOLEAN   NOT NULL DEFAULT FALSE,

    -- На одно задание внутри попытки — ровно один ответ.
    CONSTRAINT uq_answers_attempt_task UNIQUE (attempt_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_answers_attempt_id
    ON answers (attempt_id);

CREATE INDEX IF NOT EXISTS idx_answers_task_id
    ON answers (task_id);

COMMIT;
