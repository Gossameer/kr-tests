-- =====================================================================
-- Откат миграции 004.
--
-- ВНИМАНИЕ: удаляются колонки classes, shuffle и is_open вместе с данными
-- (списки классов у всех контрольных). Внешний ключ возвращается в вариант
-- без каскада — после этого удалить контрольную с работами снова нельзя.
--
-- Запуск: psql -U postgres -d kr_tests -f migrations/004_classes_shuffle_open.down.sql
-- =====================================================================

BEGIN;

DROP INDEX IF EXISTS uq_attempts_test_student;

ALTER TABLE attempts
    DROP CONSTRAINT IF EXISTS attempts_test_id_fkey;

ALTER TABLE attempts
    ADD CONSTRAINT attempts_test_id_fkey
    FOREIGN KEY (test_id) REFERENCES tests (id);

ALTER TABLE tests DROP COLUMN IF EXISTS is_open;
ALTER TABLE tests DROP COLUMN IF EXISTS shuffle;
ALTER TABLE tests DROP COLUMN IF EXISTS classes;

COMMIT;
