-- =====================================================================
-- Миграция 010: ссылки по классам, аннулирование попыток, журнал прав.
--
-- Накат:
--   psql -U postgres -d kr_tests -f migrations/010_class_links_roles.sql
-- Откат:
--   psql -U postgres -d kr_tests -f migrations/010_class_links_roles.down.sql
--
-- Что появляется:
--   test_classes       — у работы своя ссылка на каждый класс (/t/<код класса>),
--                        свой переключатель «приём открыт» и свой счётчик вариантов;
--   tests.links_by_class — TRUE у новых работ: общая ссылка у них не работает,
--                        только ссылки классов. У уже созданных работ FALSE —
--                        их старая ссылка работает как раньше;
--   attempts.class_id  — по какой классовой ссылке пришёл ученик (NULL — по общей);
--   attempts.device_id — метка устройства: одна попытка с устройства в рамках
--                        одной классовой ссылки;
--   attempts.name_key  — ФИО для сравнения (нижний регистр, ё → е);
--   attempts.annulled_at — «аннулирована»: учитель разрешил пересдачу. Попытка
--                        остаётся в базе, но в результаты и статистику не идёт;
--   admin_log          — журнал: кто кому выдал или снял права администратора.
--
-- Данные не теряются: существующие попытки и ссылки остаются рабочими.
-- Каждому классу уже созданных работ тоже выдаётся своя ссылка.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Ссылки по классам
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_classes (
    id              BIGSERIAL   PRIMARY KEY,
    test_id         BIGINT      NOT NULL REFERENCES tests (id) ON DELETE CASCADE,
    class_name      TEXT        NOT NULL,
    -- Код в ссылке /t/<code>. Не совпадает ни с одним tests.share_token:
    -- это проверяется при создании (и ниже, при переносе старых работ).
    code            TEXT        NOT NULL UNIQUE,
    is_open         BOOLEAN     NOT NULL DEFAULT TRUE,
    -- Варианты раздаются по очереди ВНУТРИ класса: 1, 2, ..., N, снова 1.
    variant_counter BIGINT      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_test_classes_test_class UNIQUE (test_id, class_name)
);

ALTER TABLE tests
    ADD COLUMN IF NOT EXISTS links_by_class BOOLEAN NOT NULL DEFAULT FALSE;

-- Уже созданным работам — по ссылке на каждый их класс. Код: 8 знаков из того же
-- алфавита, что и в приложении (без 0/O/1/l/I). Занятый код перегенерируется.
DO $$
DECLARE
    item     RECORD;
    alphabet CONSTANT TEXT := 'abcdefghijkmnpqrstuvwxyz23456789';
    new_code TEXT;
BEGIN
    FOR item IN
        SELECT t.id AS test_id, t.is_open, c.class_name
        FROM tests t
        CROSS JOIN LATERAL unnest(t.classes) AS c(class_name)
        WHERE NOT EXISTS (
            SELECT 1 FROM test_classes tc
            WHERE tc.test_id = t.id AND tc.class_name = c.class_name
        )
    LOOP
        LOOP
            SELECT string_agg(substr(alphabet, 1 + floor(random() * length(alphabet))::int, 1), '')
            INTO new_code
            FROM generate_series(1, 8);

            EXIT WHEN NOT EXISTS (SELECT 1 FROM test_classes WHERE code = new_code)
                  AND NOT EXISTS (SELECT 1 FROM tests WHERE share_token = new_code);
        END LOOP;

        INSERT INTO test_classes (test_id, class_name, code, is_open)
        VALUES (item.test_id, item.class_name, new_code, item.is_open)
        ON CONFLICT (test_id, class_name) DO NOTHING;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 2. Попытки: класс-ссылка, устройство, аннулирование
-- ---------------------------------------------------------------------
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS class_id BIGINT REFERENCES test_classes (id) ON DELETE SET NULL;
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS device_id TEXT NOT NULL DEFAULT '';
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS annulled_at TIMESTAMPTZ;
ALTER TABLE attempts
    ADD COLUMN IF NOT EXISTS name_key TEXT NOT NULL DEFAULT '';

UPDATE attempts
SET name_key = replace(lower(student_name), 'ё', 'е')
WHERE name_key = '';

-- «Алёна» и «Алена» раньше считались разными учениками и могли сдать оба.
-- Чтобы новый уникальный индекс построился, у более поздней из таких попыток
-- ключ делаем особым (к имени на экране это отношения не имеет).
UPDATE attempts a
SET name_key = a.name_key || '#' || a.id
WHERE EXISTS (
    SELECT 1 FROM attempts b
    WHERE b.test_id = a.test_id
      AND b.student_class = a.student_class
      AND b.name_key = a.name_key
      AND b.id < a.id
);

-- Один ученик — одна ДЕЙСТВУЮЩАЯ попытка в классе. Аннулированные не мешают:
-- после «Разрешить пересдачу» ученик начинает новую.
DROP INDEX IF EXISTS uq_attempts_test_student;
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempts_test_class_name_active
    ON attempts (test_id, student_class, name_key)
    WHERE annulled_at IS NULL;

-- Поиск «с этого устройства по этой ссылке уже сдавали».
CREATE INDEX IF NOT EXISTS idx_attempts_class_device
    ON attempts (class_id, device_id)
    WHERE class_id IS NOT NULL AND device_id <> '';

-- ---------------------------------------------------------------------
-- 3. Журнал действий администратора
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_log (
    id          BIGSERIAL   PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Учётку могут удалить — запись остаётся, поэтому рядом лежат и имена.
    admin_id    BIGINT               REFERENCES users (id) ON DELETE SET NULL,
    admin_name  TEXT        NOT NULL DEFAULT '',
    -- 'grant_admin' — выдал права администратора, 'revoke_admin' — снял.
    action      TEXT        NOT NULL,
    target_id   BIGINT               REFERENCES users (id) ON DELETE SET NULL,
    target_name TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_admin_log_created ON admin_log (created_at DESC);

COMMIT;
