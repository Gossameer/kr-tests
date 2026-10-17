-- =====================================================================
-- Откат миграции 013.
--
--   psql -U postgres -d kr_tests -f migrations/013_fix_correct_answer.down.sql
--
-- Исправленные учителем правильные ответы возвращаются к исходным (как их дал ИИ),
-- после чего колонки удаляются. Вердикты и баллы уже сданных работ после отката
-- остаются посчитанными по исправленному ответу — чтобы пересчитать их по
-- исходному, перед откатом отмените правки на странице работы.
-- =====================================================================

BEGIN;

UPDATE tasks
SET accepted_answers = original_answers
WHERE original_answers IS NOT NULL;

UPDATE task_options o
SET is_correct = (o.id = t.original_correct_option)
FROM tasks t
WHERE o.task_id = t.id AND t.original_correct_option IS NOT NULL;

ALTER TABLE tasks DROP COLUMN IF EXISTS original_correct_option;
ALTER TABLE tasks DROP COLUMN IF EXISTS original_answers;

COMMIT;
