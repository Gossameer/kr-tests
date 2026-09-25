-- =====================================================================
-- Откат миграции 001: удаляет все созданные таблицы.
-- ВНИМАНИЕ: вместе с таблицами удаляются все данные в них.
--
-- Запуск: psql -U postgres -d kr_tests -f migrations/001_init.down.sql
--
-- Порядок удаления — обратный созданию: сначала «дети», потом «родители»,
-- иначе внешние ключи не дадут удалить таблицу.
-- =====================================================================

BEGIN;

DROP TABLE IF EXISTS answers;
DROP TABLE IF EXISTS attempts;
DROP TABLE IF EXISTS options;
DROP TABLE IF EXISTS questions;
DROP TABLE IF EXISTS tests;

COMMIT;
