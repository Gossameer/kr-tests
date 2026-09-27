-- =====================================================================
-- Миграция 007: адрес клиента в журнале попыток входа.
--
-- Накат:
--   $env:PGCLIENTENCODING="UTF8"
--   psql -U postgres -d kr_tests -f migrations/007_login_ip.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/007_login_ip.down.sql
--
-- Зачем: ограничение подбора пароля работало только по email. Злоумышленнику
-- достаточно было перебирать разные адреса — счётчик по каждому из них свой.
-- Теперь считаем ещё и попытки с одного IP.
--
-- За обратным прокси настоящий адрес приходит в заголовке X-Forwarded-For;
-- доверять ему приложение будет только при TRUST_PROXY=true (см. app/config.py).
-- =====================================================================

BEGIN;

ALTER TABLE login_attempts
    ADD COLUMN IF NOT EXISTS ip TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
    ON login_attempts (ip, created_at);

COMMIT;
