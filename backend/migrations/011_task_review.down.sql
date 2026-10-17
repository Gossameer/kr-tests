-- =====================================================================
-- Откат миграции 011.
--
--   psql -U postgres -d kr_tests -f migrations/011_task_review.down.sql
--
-- Правки учителя (добавленные ответы, «засчитать всем», «исключить») и их
-- журнал удаляются. Баллы попыток пересчитываются обратно по исходным
-- правильным ответам: для заданий с выбором — здесь же, в SQL; для заданий
-- с вводом вердикт, изменённый добавленным ответом, остаётся как был
-- (сравнение ответов живёт в приложении). Чтобы вернуть и их, достаточно
-- после отката перезапустить пересчёт — либо не откатывать.
-- =====================================================================

BEGIN;

-- Выбор: вердикт снова только по исходному правильному варианту.
UPDATE answers ans
SET is_correct = coalesce((SELECT o.is_correct FROM task_options o WHERE o.id = ans.option_id), FALSE)
FROM tasks t
WHERE t.id = ans.task_id
  AND t.answer_format = 'choice'
  AND (t.credit_all OR cardinality(t.extra_option_ids) > 0);

-- «Засчитать всем» у ввода: без самого ответа вердикт не восстановить точно —
-- пустой ответ точно неверен, остальные оставляем как есть.
UPDATE answers ans
SET is_correct = FALSE
FROM tasks t
WHERE t.id = ans.task_id
  AND t.answer_format = 'input'
  AND t.credit_all
  AND coalesce(btrim(ans.answer_text), '') = '';

-- Баллы и максимум — снова по всем заданиям варианта.
UPDATE attempts a
SET score = sub.score, max_score = sub.max_score
FROM (
    SELECT ans.attempt_id,
           count(*) FILTER (WHERE ans.is_correct) AS score,
           count(*)                               AS max_score
    FROM answers ans
    GROUP BY ans.attempt_id
) sub
WHERE sub.attempt_id = a.id AND a.finished_at IS NOT NULL;

DROP TABLE IF EXISTS task_review_log;

ALTER TABLE tasks DROP COLUMN IF EXISTS excluded;
ALTER TABLE tasks DROP COLUMN IF EXISTS credit_all;
ALTER TABLE tasks DROP COLUMN IF EXISTS extra_option_ids;
ALTER TABLE tasks DROP COLUMN IF EXISTS extra_answers;

COMMIT;
