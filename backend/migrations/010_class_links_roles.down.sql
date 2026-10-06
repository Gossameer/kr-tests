-- =====================================================================
-- Откат миграции 010.
--
--   psql -U postgres -d kr_tests -f migrations/010_class_links_roles.down.sql
--
-- ВНИМАНИЕ, что теряется при откате:
--   * ссылки по классам перестают работать (остаётся общая ссылка работы);
--   * АННУЛИРОВАННЫЕ попытки удаляются вместе с ответами: старая схема не умеет
--     их отличать, и они мешали бы и статистике, и правилу «один ученик — одна
--     попытка»;
--   * журнал выдачи прав администратора.
-- Действующие попытки и сами работы остаются.
-- =====================================================================

BEGIN;

DELETE FROM attempts WHERE annulled_at IS NOT NULL;

DROP INDEX IF EXISTS idx_attempts_class_device;
DROP INDEX IF EXISTS uq_attempts_test_class_name_active;

-- Работы, созданные после миграции, жили только на классовых ссылках.
-- Возвращаем им общую: её код (share_token) у работы есть всегда.
ALTER TABLE tests DROP COLUMN IF EXISTS links_by_class;

-- Если один ученик прошёл работу и по общей, и по классовой ссылке под именами,
-- отличающимися только буквой ё, старый индекс не построится — оставляем
-- более раннюю попытку.
DELETE FROM attempts a
USING attempts b
WHERE a.test_id = b.test_id
  AND a.student_name = b.student_name
  AND a.student_class = b.student_class
  AND a.id > b.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_test_student
    ON attempts (test_id, student_name, student_class);

ALTER TABLE attempts DROP COLUMN IF EXISTS name_key;
ALTER TABLE attempts DROP COLUMN IF EXISTS annulled_at;
ALTER TABLE attempts DROP COLUMN IF EXISTS device_id;
ALTER TABLE attempts DROP COLUMN IF EXISTS class_id;

DROP TABLE IF EXISTS test_classes;
DROP TABLE IF EXISTS admin_log;

COMMIT;
