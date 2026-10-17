-- =====================================================================
-- Миграция 012: правка сразу во всех вариантах.
--
-- Накат:
--   psql -U postgres -d kr_tests -f migrations/012_bulk_edit.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/012_bulk_edit.down.sql
--
--   skills.ai_hint — «Уточнение для ИИ»: пожелание учителя к умению
--                    («без логарифмов по основанию 10», «только натуральные числа»).
--                    Учитывается при генерации, догенерации и правках; копируется
--                    вместе с работой.
--   ai_jobs.kind  — новый вид фонового задания 'bulk': ИИ правит или заменяет
--                    задания одного умения во всех вариантах.
-- =====================================================================

BEGIN;

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS ai_hint TEXT NOT NULL DEFAULT '';

ALTER TABLE ai_jobs DROP CONSTRAINT IF EXISTS ai_jobs_kind_known;
ALTER TABLE ai_jobs
    ADD CONSTRAINT ai_jobs_kind_known CHECK (kind IN ('test', 'task', 'bulk'));

COMMIT;
