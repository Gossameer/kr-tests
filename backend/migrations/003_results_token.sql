-- =====================================================================
-- Миграция 003: секретная ссылка на результаты.
--
-- Накат:  psql -U postgres -d kr_tests -f migrations/003_results_token.sql
-- Откат:  psql -U postgres -d kr_tests -f migrations/003_results_token.down.sql
--
-- Зачем: авторизации учителя ещё нет, поэтому страница результатов защищена
-- «секретом в адресе». Он ОТДЕЛЬНЫЙ от ученического share_token: ученик знает
-- только короткий код и по нему до результатов не доберётся.
--
-- Почему длинный: share_token короткий (8 символов), его удобно диктовать классу.
-- Токен результатов, наоборот, должен быть непереборным — его никто не диктует,
-- учитель открывает ссылку из закладки.
-- =====================================================================

BEGIN;

-- Шаг 1: добавляем колонку без ограничений — так можно заполнить существующие строки.
ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS results_token TEXT;

-- Шаг 2: заполняем токен для тестов, созданных ДО этой миграции.
-- gen_random_uuid() встроен в PostgreSQL 13+, расширения ставить не нужно.
-- Два UUID без дефисов = 64 случайных шестнадцатеричных символа.
UPDATE tests
SET results_token = replace(gen_random_uuid()::text, '-', '')
                 || replace(gen_random_uuid()::text, '-', '')
WHERE results_token IS NULL;

-- Шаг 3: теперь, когда пусто нигде не осталось, запрещаем NULL.
ALTER TABLE tests
    ALTER COLUMN results_token SET NOT NULL;

-- Шаг 4: уникальность — по токену мы находим контрольную.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tests_results_token
    ON tests (results_token);

-- Индекс под сортировку списка сдавших: класс, потом фамилия.
CREATE INDEX IF NOT EXISTS idx_attempts_test_class_name
    ON attempts (test_id, student_class, student_name);

COMMIT;

-- ---------------------------------------------------------------------
-- После наката psql напечатает готовые ссылки на результаты всех тестов.
-- Скопируйте нужную и сохраните в закладки — ученикам её отправлять нельзя.
-- ---------------------------------------------------------------------
SELECT
    share_token                                   AS "код для учеников",
    title                                         AS "название",
    'http://localhost:5174/r/' || results_token   AS "ссылка на результаты"
FROM tests
ORDER BY id;
