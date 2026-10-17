-- =====================================================================
-- Миграция 011: исправление уже проведённых работ.
--
-- Накат:
--   psql -U postgres -d kr_tests -f migrations/011_task_review.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/011_task_review.down.sql
--
-- После проверочной работы учитель видит, что задание было составлено плохо
-- («правильный» ответ «∞», а ученица верно написала промежуток). Теперь можно:
--   * добавить ещё один верный ответ (tasks.extra_answers) или засчитать
--     ещё один вариант ответа у задания с выбором (tasks.extra_option_ids);
--   * засчитать задание всем, кто его получил (tasks.credit_all);
--   * исключить задание из оценки (tasks.excluded): оно не входит ни в баллы,
--     ни в максимум, ни в умения.
-- Исходные правильные ответы (accepted_answers, task_options.is_correct) и сами
-- ответы учеников не меняются: правки лежат отдельно, их можно отменить.
-- После каждой правки сервер пересчитывает answers.is_correct и баллы попыток.
--
-- task_review_log — журнал: кто, что и когда поправил.
-- =====================================================================

BEGIN;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS extra_answers TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS extra_option_ids BIGINT[] NOT NULL DEFAULT '{}';
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS credit_all BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS task_review_log (
    id          BIGSERIAL   PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    test_id     BIGINT      NOT NULL REFERENCES tests (id) ON DELETE CASCADE,
    task_id     BIGINT               REFERENCES tasks (id) ON DELETE SET NULL,
    -- Кто поправил. Учётку могут удалить — имя остаётся.
    user_id     BIGINT               REFERENCES users (id) ON DELETE SET NULL,
    user_name   TEXT        NOT NULL DEFAULT '',
    -- add_answer / remove_answer / accept_option / unaccept_option /
    -- credit_all / uncredit_all / exclude / include
    action      TEXT        NOT NULL,
    -- Что именно: добавленный ответ, текст варианта, «вариант 3, задание 2».
    detail      TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_task_review_log_test ON task_review_log (test_id, id DESC);

COMMIT;
