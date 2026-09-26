-- =====================================================================
-- Откат миграции 002.
-- ВНИМАНИЕ: удаление колонки удаляет и данные в ней (классы учеников).
--
-- Запуск: psql -U postgres -d kr_tests -f migrations/002_attempt_student_class.down.sql
-- =====================================================================

BEGIN;

DROP INDEX IF EXISTS idx_answers_question_id;

ALTER TABLE attempts
    DROP COLUMN IF EXISTS student_class;

COMMIT;
