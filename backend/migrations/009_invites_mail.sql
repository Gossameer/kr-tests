-- =====================================================================
-- Миграция 009: приглашения учителей, сброс пароля по почте, журнал писем.
--
-- Накат:
--   $env:PGCLIENTENCODING="UTF8"
--   psql -U postgres -d kr_tests -f migrations/009_invites_mail.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/009_invites_mail.down.sql
--
-- Что меняется:
--   users.status        — 'invited' (учётку завёл админ, пароль ещё не задан)
--                         или 'active'. Существующие учётки остаются 'active'.
--   users.password_hash — теперь может быть пустым: у приглашённого пароля нет,
--                         войти он не может, пока не перейдёт по ссылке.
--   users.invited_by    — кто из администраторов завёл учётку.
--   auth_tokens         — одноразовые ссылки: приглашение (7 дней) и сброс
--                         пароля (1 час). В базе лежит ТОЛЬКО хэш токена:
--                         даже с копией базы по ссылке не войти.
--   mail_log            — журнал писем: кому, какое, когда, чем кончилось.
--                         Текста письма и ссылки в журнале нет.
--   reset_requests      — запросы «забыли пароль» для ограничения частоты.
--   settings.allow_self_registration — регистрация по школьному коду,
--                         по умолчанию ВЫКЛЮЧЕНА. Уже созданные учётки не трогаем.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Пользователи
-- ---------------------------------------------------------------------
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE users
    ADD CONSTRAINT users_status_known CHECK (status IN ('invited', 'active'));

ALTER TABLE users
    ALTER COLUMN password_hash DROP NOT NULL;

-- Приглашённый — без пароля, активный — с паролем. Иначе учётка не в порядке.
ALTER TABLE users
    ADD CONSTRAINT users_password_matches_status
    CHECK ((status = 'invited') = (password_hash IS NULL));

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS invited_by BIGINT REFERENCES users (id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- 2. Одноразовые ссылки
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_tokens (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- 'invite' — задать первый пароль, 'reset' — сменить забытый.
    kind        TEXT        NOT NULL,
    -- SHA-256 от токена. Сам токен есть только в письме/ссылке.
    token_hash  TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    -- Ссылка одноразовая: после установки пароля сюда пишется время.
    used_at     TIMESTAMPTZ,
    -- Новая ссылка гасит прежние: тут время, когда эту заменили.
    revoked_at  TIMESTAMPTZ,
    -- Кто выдал (администратор). У «забыли пароль» — пусто.
    created_by  BIGINT               REFERENCES users (id) ON DELETE SET NULL,

    CONSTRAINT auth_tokens_kind_known CHECK (kind IN ('invite', 'reset'))
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_kind
    ON auth_tokens (user_id, kind, created_at);

-- ---------------------------------------------------------------------
-- 3. Журнал писем
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mail_log (
    id         BIGSERIAL   PRIMARY KEY,
    -- Учётку могут удалить — запись в журнале остаётся.
    user_id    BIGINT               REFERENCES users (id) ON DELETE SET NULL,
    to_email   TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 'queued' — ждёт отправки; 'sent' — ушло; 'failed' — не ушло (см. error);
    -- 'test'   — почта не настроена, письмо только записано в лог сервера.
    status     TEXT        NOT NULL DEFAULT 'queued',
    sent_at    TIMESTAMPTZ,
    error      TEXT        NOT NULL DEFAULT '',

    CONSTRAINT mail_log_kind_known   CHECK (kind IN ('invite', 'reset')),
    CONSTRAINT mail_log_status_known CHECK (status IN ('queued', 'sent', 'failed', 'test'))
);

CREATE INDEX IF NOT EXISTS idx_mail_log_created ON mail_log (created_at);
CREATE INDEX IF NOT EXISTS idx_mail_log_user    ON mail_log (user_id, created_at);

-- ---------------------------------------------------------------------
-- 4. Запросы «забыли пароль» (ограничение частоты по email и по адресу)
--
-- Пишем каждый запрос, даже на несуществующий email: иначе по тому, сработал
-- ли лимит, можно было бы узнать, есть ли такая учётка.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reset_requests (
    id         BIGSERIAL   PRIMARY KEY,
    email      TEXT        NOT NULL,
    ip         TEXT        NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reset_requests_email_time ON reset_requests (email, created_at);
CREATE INDEX IF NOT EXISTS idx_reset_requests_ip_time    ON reset_requests (ip, created_at);

-- ---------------------------------------------------------------------
-- 5. Самостоятельная регистрация по школьному коду — по умолчанию выключена
-- ---------------------------------------------------------------------
INSERT INTO settings (key, value)
VALUES ('allow_self_registration', 'false')
ON CONFLICT (key) DO NOTHING;

COMMIT;
