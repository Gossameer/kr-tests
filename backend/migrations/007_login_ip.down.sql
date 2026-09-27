-- =====================================================================
-- Откат миграции 007: убрать адрес из журнала попыток входа.
--
-- После отката ограничение снова считает только попытки по одному email.
--
-- Запуск:
--   psql -U postgres -d kr_tests -f migrations/007_login_ip.down.sql
-- =====================================================================

BEGIN;

DROP INDEX IF EXISTS idx_login_attempts_ip_time;

ALTER TABLE login_attempts
    DROP COLUMN IF EXISTS ip;

COMMIT;
