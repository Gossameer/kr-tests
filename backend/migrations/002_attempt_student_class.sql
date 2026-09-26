-- =====================================================================
-- Миграция 002: класс ученика в попытке + индекс для будущей статистики.
--
-- Накат:  psql -U postgres -d kr_tests -f migrations/002_attempt_student_class.sql
-- Откат:  psql -U postgres -d kr_tests -f migrations/002_attempt_student_class.down.sql
--
-- Зачем: ученик при входе указывает фамилию, имя И класс, а в таблице attempts
-- из первой миграции было только student_name. Учителю нужен класс, чтобы
-- различать однофамильцев из разных классов в выгрузке результатов.
-- =====================================================================

BEGIN;

-- IF NOT EXISTS — миграцию можно случайно запустить дважды без ошибки.
-- DEFAULT '' нужен, чтобы колонка могла быть NOT NULL:
-- у уже существующих попыток (если они есть) класс неизвестен.
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS student_class TEXT NOT NULL DEFAULT '';

-- Индекс для вопроса «на каком задании чаще всего ошибаются» —
-- понадобится на экране результатов у учителя.
CREATE INDEX IF NOT EXISTS idx_answers_question_id
    ON answers (question_id);

COMMIT;
