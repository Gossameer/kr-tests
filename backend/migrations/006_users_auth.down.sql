-- =====================================================================
-- Откат миграции 006: убрать учётные записи и вернуть секретные ссылки.
--
-- ВНИМАНИЕ: удаляются ВСЕ учётные записи, сессии и настройки школы.
-- Контрольные при этом сохраняются, но лишаются владельца, а вместо прав
-- доступа им выдаются новые случайные results_token — старые ссылки,
-- если они где-то остались, работать не будут.
--
-- Запуск:
--   $env:PGCLIENTENCODING="UTF8"
--   psql -U postgres -d kr_tests -f migrations/006_users_auth.down.sql
-- =====================================================================

BEGIN;

-- Возвращаем колонку секретного токена и заполняем её заново.
ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS results_token TEXT;

UPDATE tests
SET results_token = replace(gen_random_uuid()::text, '-', '')
                 || replace(gen_random_uuid()::text, '-', '')
WHERE results_token IS NULL;

ALTER TABLE tests
    ALTER COLUMN results_token SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tests_results_token
    ON tests (results_token);

-- Убираем владельца и предмет.
DROP INDEX IF EXISTS idx_tests_teacher_id;
ALTER TABLE tests DROP COLUMN IF EXISTS subject;
ALTER TABLE tests DROP COLUMN IF EXISTS teacher_id;

-- Убираем таблицы учётных записей.
DROP TABLE IF EXISTS login_attempts;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS users;

COMMIT;
