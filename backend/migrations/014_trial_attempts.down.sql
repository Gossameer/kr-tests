-- =====================================================================
-- Откат миграции 014.
--
--   psql -U postgres -d kr_tests -f migrations/014_trial_attempts.down.sql
--
-- Пробные прохождения удаляются вместе с ответами: без пометки они смешались бы
-- с настоящими работами учеников.
-- =====================================================================

BEGIN;

DELETE FROM attempts WHERE is_trial;

DROP INDEX IF EXISTS uq_attempts_test_class_name_active;
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_test_class_name_active
    ON attempts (test_id, student_class, name_key)
    WHERE annulled_at IS NULL;

ALTER TABLE attempts DROP COLUMN IF EXISTS is_trial;

COMMIT;
