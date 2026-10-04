-- =====================================================================
-- Откат миграции 009: убрать приглашения, сброс пароля по почте и журнал писем.
--
-- ВНИМАНИЕ:
--   * удаляются все неиспользованные ссылки-приглашения и журнал писем;
--   * учётки, которые так и не задали пароль (status = 'invited'), остаются,
--     но войти в них нельзя: вместо пароля записывается заведомо негодная
--     строка. Доступ такому учителю даст админ кнопкой «Сбросить пароль»;
--   * регистрация по школьному коду снова открыта всем, кто знает код
--     (как было до миграции).
--
-- Запуск:
--   psql -U postgres -d kr_tests -f migrations/009_invites_mail.down.sql
-- =====================================================================

BEGIN;

DROP TABLE IF EXISTS reset_requests;
DROP TABLE IF EXISTS mail_log;
DROP TABLE IF EXISTS auth_tokens;

DELETE FROM settings WHERE key = 'allow_self_registration';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_password_matches_status;
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_known;

-- '!' не является хешем bcrypt: проверка пароля по нему всегда отвечает «не подошёл».
UPDATE users SET password_hash = '!' WHERE password_hash IS NULL;

ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL;
ALTER TABLE users DROP COLUMN IF EXISTS invited_by;
ALTER TABLE users DROP COLUMN IF EXISTS status;

COMMIT;
