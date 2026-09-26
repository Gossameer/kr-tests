-- =====================================================================
-- Откат миграции 003.
-- ВНИМАНИЕ: удаление колонки удаляет и сами токены — после повторного
-- наката 003 ссылки на результаты будут ДРУГИМИ, старые перестанут работать.
--
-- Запуск: psql -U postgres -d kr_tests -f migrations/003_results_token.down.sql
-- =====================================================================

BEGIN;

DROP INDEX IF EXISTS idx_attempts_test_class_name;
DROP INDEX IF EXISTS uq_tests_results_token;

ALTER TABLE tests
    DROP COLUMN IF EXISTS results_token;

COMMIT;
