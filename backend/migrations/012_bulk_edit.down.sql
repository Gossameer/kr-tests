-- =====================================================================
-- Откат миграции 012.
--
--   psql -U postgres -d kr_tests -f migrations/012_bulk_edit.down.sql
--
-- Теряются «Уточнения для ИИ» у умений и история массовых правок
-- (сами принятые правки уже лежат в заданиях работ и остаются).
-- =====================================================================

BEGIN;

DELETE FROM ai_jobs WHERE kind = 'bulk';

ALTER TABLE ai_jobs DROP CONSTRAINT IF EXISTS ai_jobs_kind_known;
ALTER TABLE ai_jobs
    ADD CONSTRAINT ai_jobs_kind_known CHECK (kind IN ('test', 'task'));

ALTER TABLE skills DROP COLUMN IF EXISTS ai_hint;

COMMIT;
