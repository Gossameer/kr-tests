-- =====================================================================
-- Миграция 014: «пробное прохождение».
--
-- Накат:
--   psql -U postgres -d kr_tests -f migrations/014_trial_attempts.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/014_trial_attempts.down.sql
--
-- Учитель (или администратор) открывает ссылку для учеников в том же браузере, где
-- вошёл, и проходит работу сам — проверить, как она выглядит. Такая попытка
-- помечается attempts.is_trial: она видна в результатах с пометкой «пробное
-- прохождение», но не входит в баллы класса, умения, статистику, «ошибки учеников»
-- и Excel, не занимает ФИО ученика и не блокирует устройство.
-- =====================================================================

BEGIN;

ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT FALSE;

-- Пробная попытка с ФИО «Иванов Иван» не должна мешать настоящему Иванову.
DROP INDEX IF EXISTS uq_attempts_test_class_name_active;
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_test_class_name_active
    ON attempts (test_id, student_class, name_key)
    WHERE annulled_at IS NULL AND NOT is_trial;

COMMIT;
