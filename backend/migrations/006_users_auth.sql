-- =====================================================================
-- Миграция 006: учётные записи, вход по паролю, владелец у контрольной.
--
-- Накат:
--   $env:PGCLIENTENCODING="UTF8"
--   psql -U postgres -d kr_tests -f migrations/006_users_auth.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/006_users_auth.down.sql
--
-- ВНИМАНИЕ: контрольные без владельца эта миграция УДАЛЯЕТ вместе с работами.
-- Владельца брать неоткуда: администратора создают уже ПОСЛЕ наката командой
-- `python -m app.create_admin`, значит на момент миграции пользователей нет
-- вообще. Данные тестовые, поэтому чистим, а не изобретаем «ничейного» автора.
-- Если админ каким-то образом уже есть, контрольные достанутся ему.
--
-- Что появляется:
--   users          — учителя и администраторы
--   sessions       — вход по cookie (одна строка = один вход)
--   login_attempts — журнал попыток входа, по нему работает ограничение
--   settings       — настройки школы, сейчас там код регистрации
--   tests.teacher_id / tests.subject — владелец и предмет
--   минус tests.results_token — секретные ссылки заменены правами доступа
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Пользователи
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL   PRIMARY KEY,
    full_name     TEXT        NOT NULL,
    -- email хранится в нижнем регистре: «Ivanova@…» и «ivanova@…» — один человек.
    email         TEXT        NOT NULL UNIQUE,
    -- Только хеш (bcrypt). Сам пароль не хранится нигде и восстановлению не подлежит.
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'teacher',
    -- Заблокированный учитель остаётся в базе со своими контрольными, но войти не может.
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,

    CONSTRAINT users_role_known CHECK (role IN ('teacher', 'admin'))
);

-- ---------------------------------------------------------------------
-- 2. Сессии
--
-- Храним в базе, а не в подписанной cookie: так выход и блокировку видно
-- мгновенно — строку сессии просто удаляем.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT        PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id);

-- ---------------------------------------------------------------------
-- 3. Попытки входа (для ограничения подбора пароля)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS login_attempts (
    id         BIGSERIAL   PRIMARY KEY,
    email      TEXT        NOT NULL,
    success    BOOLEAN     NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_email_time
    ON login_attempts (email, created_at);

-- ---------------------------------------------------------------------
-- 4. Настройки школы
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT        PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Код регистрации по умолчанию. Админ обязан сменить его в разделе «Настройки»:
-- любой, кто знает код, может завести себе учётку учителя.
INSERT INTO settings (key, value)
VALUES ('school_code', 'ШКОЛА-2090')
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------------
-- 5. Владелец и предмет у контрольной
-- ---------------------------------------------------------------------
ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS teacher_id BIGINT REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT '';

-- Если админ уже есть — контрольные без владельца достаются ему.
UPDATE tests
SET teacher_id = (SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1)
WHERE teacher_id IS NULL;

-- Иначе такие контрольные удаляем: без владельца ими некому управлять.
-- Работы, задания и умения уйдут каскадом.
DELETE FROM tests WHERE teacher_id IS NULL;

ALTER TABLE tests
    ALTER COLUMN teacher_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tests_teacher_id ON tests (teacher_id);

-- ---------------------------------------------------------------------
-- 6. Секретные ссылки больше не нужны
--
-- Раньше результаты открывались по длинному токену в адресе. Теперь доступ
-- даёт учётная запись: владелец контрольной и администратор.
-- ---------------------------------------------------------------------
DROP INDEX IF EXISTS uq_tests_results_token;

ALTER TABLE tests
    DROP COLUMN IF EXISTS results_token;

COMMIT;
