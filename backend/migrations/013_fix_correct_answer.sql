-- =====================================================================
-- Миграция 013: «Исправить правильный ответ» у уже проведённой работы.
--
-- Накат:
--   psql -U postgres -d kr_tests -f migrations/013_fix_correct_answer.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/013_fix_correct_answer.down.sql
--
-- ИИ ошибся в правильном ответе (log_4^2 x − 3log_4 x + 2 = 0: дано 16, верно 64).
-- Учитель заменяет ответ — не добавляет ещё один, а именно заменяет. Новый ответ
-- записывается прямо в задание (accepted_answers / task_options.is_correct), поэтому
-- его сразу видят разбор работы, Excel, ключ ответов и статистика. Исходный ответ
-- сохраняется здесь — чтобы правку можно было отменить:
--   tasks.original_answers         — прежние правильные ответы ввода;
--   tasks.original_correct_option  — прежний верный вариант выбора.
-- NULL — ответ не исправляли.
-- =====================================================================

BEGIN;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS original_answers TEXT[];
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS original_correct_option BIGINT REFERENCES task_options (id) ON DELETE SET NULL;

COMMIT;
