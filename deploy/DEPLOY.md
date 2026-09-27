# Выкладка на школьный VPS

Инструкция для сервера `/opt/2090-fun-infra`, где уже живут другие сервисы
и работает `edge-caddy`. Цель — встроить сервис контрольных и не задеть соседей.

Домен: **kr.2090.fun**. Сервисы: `kr-postgres`, `kr-backend`, `kr-frontend`.

Все команды выполняются на сервере из папки `/opt/2090-fun-infra`, если не
сказано иное.

---

## Перед началом

Нужно, чтобы:

- DNS-запись `kr.2090.fun` указывала на IP сервера (Caddy сам получит сертификат);
- на сервере был доступ к репозиторию проекта;
- вы могли редактировать `docker-compose.yml` и `Caddyfile`.

Проверьте, что соседние сервисы сейчас здоровы — чтобы потом отличить свою
поломку от чужой:

```bash
cd /opt/2090-fun-infra
docker compose ps
```

---

## Шаг 1. Резервная копия того, что будете править

Делается всегда, даже если правка кажется мелкой.

```bash
cd /opt/2090-fun-infra
mkdir -p backups
cp docker-compose.yml "backups/docker-compose.yml.$(date +%F-%H%M)"
cp Caddyfile "backups/Caddyfile.$(date +%F-%H%M)"
ls -l backups | tail -5
```

Вы должны увидеть две свежие копии с сегодняшней датой. Как вернуться к ним —
в разделе «Откат» в конце.

---

## Шаг 2. Исходники проекта

```bash
cd /opt/2090-fun-infra/apps
git clone <адрес репозитория kr-tests> kr-tests
ls kr-tests
```

Проверка: в выводе есть папки `backend`, `frontend`, `deploy`.

---

## Шаг 3. Файл с настройками

```bash
cd /opt/2090-fun-infra
cp apps/kr-tests/deploy/kr.env.example env/kr.env
nano env/kr.env
```

Заполните:

- `KR_POSTGRES_PASSWORD` — длинный пароль (`openssl rand -base64 24` подскажет);
- `DATABASE_URL` — тот же пароль внутри строки подключения;
- остальное можно оставить как есть.

Закройте файл от посторонних:

```bash
chmod 600 env/kr.env
grep -c ЗАМЕНИТЕ env/kr.env
```

Проверка: команда должна напечатать `0`. Если больше нуля — где-то остался
текст-заглушка вместо пароля.

---

## Шаг 4. Сервисы в docker-compose.yml

Откройте `apps/kr-tests/deploy/compose-snippet.yml`, скопируйте три сервиса
в раздел `services:` общего файла, а `kr-postgres-data:` — в раздел `volumes:`.

```bash
nano docker-compose.yml
```

Проверка синтаксиса (файл только читается, ничего не запускается):

```bash
docker compose config > /dev/null && echo "файл корректен"
```

Если команда ругается — сравните отступы: в YAML они задают вложенность.

---

## Шаг 5. Сборка и запуск

```bash
docker compose build kr-backend kr-frontend
docker compose up -d kr-postgres
docker compose ps kr-postgres
```

Проверка: у `kr-postgres` состояние `healthy` (может занять до минуты).

```bash
docker compose up -d kr-backend kr-frontend
docker compose ps kr-backend kr-frontend
```

Проверка: оба в состоянии `running`. `kr-backend` пока может быть `unhealthy` —
база ещё пустая, это нормально до следующего шага.

---

## Шаг 6. Миграции

Накатываются по порядку номеров. Команда берёт файлы из образа бэкенда
и выполняет их в базе:

```bash
cd /opt/2090-fun-infra
for f in $(docker compose exec -T kr-backend sh -c 'ls migrations/*.sql | grep -v down | sort'); do
  echo "== $f"
  docker compose exec -T kr-backend cat "$f" \
    | docker compose exec -T kr-postgres psql -v ON_ERROR_STOP=1 -U "$KR_POSTGRES_USER" -d "$KR_POSTGRES_DB"
done
```

Если переменные `KR_POSTGRES_USER` и `KR_POSTGRES_DB` в вашей оболочке не
заданы, подставьте значения из `env/kr.env` прямо в команду.

Проверка — в базе должно быть десять таблиц:

```bash
docker compose exec kr-postgres psql -U kr_user -d kr_tests -c '\dt'
```

Ожидаются: `answers`, `attempts`, `login_attempts`, `sessions`, `settings`,
`skills`, `task_options`, `tasks`, `tests`, `users`.

Теперь бэкенд должен стать здоровым:

```bash
docker compose ps kr-backend
docker compose exec kr-backend python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

Ожидается `{"status":"ok","database":"up"}`.

---

## Шаг 7. Администратор

Учётку администратора создаёт команда внутри контейнера. Вводите данные
руками — копирование через конвейер портит кириллицу:

```bash
docker compose exec -it kr-backend python -m app.create_admin
```

Спросит ФИО, email и пароль (дважды, ввод не отображается).

Проверка:

```bash
docker compose exec kr-postgres psql -U kr_user -d kr_tests -c "SELECT email, role FROM users;"
```

---

## Шаг 8. Домен в Caddy

Добавьте блок из `apps/kr-tests/deploy/Caddyfile-snippet` в общий `Caddyfile`:

```bash
nano Caddyfile
docker compose exec edge-caddy caddy validate --config /etc/caddy/Caddyfile
```

Проверка: `Valid configuration`. Только после этого применяйте:

```bash
docker compose exec edge-caddy caddy reload --config /etc/caddy/Caddyfile
```

`reload` не роняет соседние сайты — Caddy подменяет конфигурацию на ходу.

---

## Шаг 9. Проверка снаружи

```bash
curl -I https://kr.2090.fun
curl -s https://kr.2090.fun/health
```

Ожидается `HTTP/2 200` и `{"status":"ok","database":"up"}`.

Затем в браузере:

1. откройте `https://kr.2090.fun` — появится страница входа;
2. войдите администратором;
3. смените школьный код в разделе «Настройки»;
4. создайте пробную контрольную и пройдите её по ученической ссылке с телефона.

Если страница входа открылась, но вход «не держится» — проверьте
`COOKIE_SECURE=true` в `env/kr.env` и что сайт открыт именно по `https`.

---

## Обновление версии

```bash
cd /opt/2090-fun-infra
cp docker-compose.yml "backups/docker-compose.yml.$(date +%F-%H%M)"

cd apps/kr-tests
git pull
cd /opt/2090-fun-infra

docker compose build kr-backend kr-frontend
docker compose up -d kr-backend kr-frontend
docker compose ps kr-backend kr-frontend
```

Если в обновлении появились новые миграции — накатите их так же, как на шаге 6:
файлы, которые уже применялись, написаны безопасно для повторного запуска
(`IF NOT EXISTS`), но лучше выполнять только новые номера.

Проверка после обновления:

```bash
curl -s https://kr.2090.fun/health
```

---

## Резервная копия базы

Данные учеников живут только в томе `kr-postgres-data`. Снимок базы:

```bash
cd /opt/2090-fun-infra
docker compose exec -T kr-postgres pg_dump -U kr_user -d kr_tests \
  | gzip > "backups/kr_tests-$(date +%F-%H%M).sql.gz"
ls -lh backups | tail -3
```

Восстановление из снимка (база должна быть пустой):

```bash
gunzip -c backups/kr_tests-2026-09-27-1200.sql.gz \
  | docker compose exec -T kr-postgres psql -U kr_user -d kr_tests
```

---

## Откат

**Если сломался только сервис контрольных** — остановите его, соседи не пострадают:

```bash
docker compose stop kr-frontend kr-backend
```

**Если проблема в правке Caddyfile** — верните копию и перезагрузите конфигурацию:

```bash
cd /opt/2090-fun-infra
cp backups/Caddyfile.<дата-время> Caddyfile
docker compose exec edge-caddy caddy validate --config /etc/caddy/Caddyfile
docker compose exec edge-caddy caddy reload --config /etc/caddy/Caddyfile
```

**Если проблема в правке docker-compose.yml:**

```bash
cp backups/docker-compose.yml.<дата-время> docker-compose.yml
docker compose config > /dev/null && echo "файл корректен"
docker compose up -d
```

**Если нужно убрать сервис целиком** (данные сохранятся в томе):

```bash
docker compose rm -sf kr-frontend kr-backend kr-postgres
```

Удалять том `kr-postgres-data` не нужно — вместе с ним исчезнут все работы
учеников. Делайте это только сознательно:

```bash
docker volume rm 2090-fun-infra_kr-postgres-data
```

---

## Что смотреть при разборе аварии

```bash
docker compose logs --tail=100 kr-backend
docker compose logs --tail=50 kr-frontend
docker compose ps
```

Частые причины:

| Симптом | Обычная причина |
| --- | --- |
| `kr-backend` перезапускается | неверный `DATABASE_URL` или база ещё не поднялась |
| `/health` отвечает `"database":"down"` | пароль в `DATABASE_URL` не совпадает с паролем базы |
| Страница открывается, вход не держится | `COOKIE_SECURE=true` при заходе по http |
| 404 на `/tests/…/results` при перезагрузке | правило `try_files` в nginx фронтенда (оно уже есть в образе) |
| Пустая страница, в консоли браузера ошибки CSP | появился внешний скрипт или шрифт — их сервис не использует |
