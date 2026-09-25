# КР — сервис контрольных работ

Сервис для школьных контрольных с выбором ответа.

**Как это работает (целевая картина):**

1. Учитель создаёт контрольную: вставляет JSON с вопросами (сгенерированный во внешнем ИИ
   по готовому промту), проверяет задания и публикует → получает ссылку.
2. Ученики открывают ссылку, проходят тест с выбором ответа.
3. Учитель видит результаты и выгружает их.

Сейчас в репозитории **только фундамент**: структура, подключение к базе, первая миграция
и страница-заглушка. Бизнес-логики пока нет.

## Стек

| Часть    | Технологии                                   |
| -------- | -------------------------------------------- |
| Бэкенд   | Python 3.12+, FastAPI, psycopg 3, PostgreSQL 16 |
| Фронтенд | React 19, TypeScript, Vite                   |
| Прочее   | docker-compose (на будущее, для деплоя)      |

## Структура репозитория

```
kr-tests/
├── backend/                  # FastAPI-приложение
│   ├── app/
│   │   ├── main.py           # точка входа: создание приложения, старт/остановка
│   │   ├── config.py         # чтение настроек из .env
│   │   ├── db.py             # пул соединений с PostgreSQL
│   │   └── routers/
│   │       └── health.py     # GET /health
│   ├── migrations/
│   │   ├── 001_init.sql      # создание таблиц
│   │   └── 001_init.down.sql # откат
│   ├── requirements.txt
│   └── .env.example          # шаблон настроек → скопировать в .env
├── frontend/                 # Vite + React + TypeScript
│   ├── src/
│   │   ├── App.tsx           # страница-заглушка с проверкой связи
│   │   ├── api.ts            # обращения к бэкенду
│   │   └── main.tsx
│   └── .env.example          # шаблон → скопировать в .env.local
├── docker-compose.yml
└── README.md
```

## Схема базы данных

```
tests ──< questions ──< options
  │                         │
  └──< attempts ──< answers ┘
```

| Таблица     | Назначение                        | Ключевые поля                                                        |
| ----------- | --------------------------------- | -------------------------------------------------------------------- |
| `tests`     | контрольная работа                | `title`, `teacher_name`, `share_token` (уникальный), `is_published`   |
| `questions` | вопросы контрольной               | `test_id` → `tests`, `text`, `position`                               |
| `options`   | варианты ответа                   | `question_id` → `questions`, `text`, `is_correct`, `position`         |
| `attempts`  | попытка ученика                   | `test_id` → `tests`, `student_name`, `score`, `max_score`, даты       |
| `answers`   | выбранный вариант на вопрос       | `attempt_id` → `attempts`, `question_id`, `option_id`                 |

Удаление контрольной каскадом удаляет её вопросы и варианты; удаление попытки — её ответы.
Ссылка `attempts.test_id` намеренно **без** каскада, чтобы нельзя было случайно снести
контрольную вместе с результатами учеников.

---

# Запуск на своей машине

Дальше — по шагам. Всё выполняется в PowerShell из папки проекта.

> На этой машине PostgreSQL 16 установлен в `C:\Program Files\PostgreSQL\16`,
> но `psql` не прописан в PATH — поэтому ниже он вызывается полным путём.
> Чтобы не писать путь каждый раз, один раз выполни:
>
> ```powershell
> $env:Path += ';C:\Program Files\PostgreSQL\16\bin'
> ```
>
> (это действует до закрытия окна PowerShell).

## Шаг 1. Создать базу `kr_tests`

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\createdb.exe' -U postgres kr_tests
```

Спросит пароль пользователя `postgres` — тот, который ты задал при установке PostgreSQL.

Проверить, что база появилась:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -U postgres -l
```

В списке должна быть строка `kr_tests`.

## Шаг 2. Накатить миграцию

Из папки `backend`:

```powershell
cd backend
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -U postgres -d kr_tests -f migrations/001_init.sql
```

Ожидаемый вывод: несколько строк `CREATE TABLE` и `CREATE INDEX`, затем `COMMIT`.

Проверить, что таблицы создались:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -U postgres -d kr_tests -c '\dt'
```

Должны быть пять таблиц: `answers`, `attempts`, `options`, `questions`, `tests`.

**Откат** (удалит таблицы вместе с данными):

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -U postgres -d kr_tests -f migrations/001_init.down.sql
```

## Шаг 3. Настроить и установить бэкенд

Из папки `backend`:

```powershell
# 1. Виртуальное окружение — отдельная «песочница» для зависимостей проекта
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# В начале строки появится (.venv) — значит, окружение активно.

# 2. Зависимости
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Настройки: скопировать шаблон и вписать свой пароль от postgres
Copy-Item .env.example .env
notepad .env
```

В `.env` поправь пароль в строке `DATABASE_URL`, если он не `postgres`:

```
DATABASE_URL=postgresql://postgres:ТВОЙ_ПАРОЛЬ@localhost:5432/kr_tests
```

> Если PowerShell ругается `Activate.ps1 не может быть загружен, так как выполнение
> сценариев отключено` — выполни один раз:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Шаг 4. Запустить бэкенд

Из папки `backend` с активным `.venv`:

```powershell
uvicorn app.main:app --reload
```

`--reload` = сервер сам перезапускается после правок в коде.

Проверка — открой в браузере:

* http://127.0.0.1:8000/health → `{"status":"ok","database":"up"}`
* http://127.0.0.1:8000/docs → автодокументация API

Если в ответе `"database":"down"` — бэкенд работает, но не видит базу.
Проверь: запущена ли служба PostgreSQL, создана ли база (шаг 1), верен ли пароль в `.env`.

## Шаг 5. Запустить фронтенд

В **новом** окне PowerShell (бэкенд оставь работать), из папки `frontend`:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

Открой **http://localhost:5174** — увидишь страницу «КР — сервис контрольных».

> Порт 5174, а не стандартный для Vite 5173: на 5173 может работать другой проект
> (school-frontend). Адрес и порт заданы в `frontend/vite.config.ts` в блоке `server`,
> там же `host: '127.0.0.1'` — без него Vite на Windows слушает только IPv6 (`[::1]`)
> и страница не открывается по 127.0.0.1.

## Шаг 6. Проверить связь

На странице фронтенда в блоке «Связь с бэкендом» должно быть два зелёных пункта:

* Бэкенд отвечает: **ok**
* База данных: **подключена**

Если написано «Бэкенд недоступен» — проверь, что окно из шага 4 всё ещё работает
и адрес в `frontend/.env.local` совпадает с адресом uvicorn (`http://127.0.0.1:8000`).

Если страница вообще не открывается, проверь, на каком адресе слушает Vite:

```powershell
netstat -ano | Select-String ':5174'
```

Должно быть `127.0.0.1:5174 ... LISTENING`. Если видишь `[::1]:5174` — значит,
правка `host` в `vite.config.ts` не подхватилась, перезапусти `npm run dev`.

---

## Шпаргалка: повседневный запуск

Два окна PowerShell:

```powershell
# окно 1 — бэкенд
cd backend; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload

# окно 2 — фронтенд
cd frontend; npm run dev
```

## Что дальше

- [ ] Приём JSON с вопросами и сохранение контрольной
- [ ] Публикация и генерация `share_token`
- [ ] Страница прохождения теста по ссылке
- [ ] Подсчёт результата и сохранение попытки
- [ ] Просмотр и выгрузка результатов для учителя
