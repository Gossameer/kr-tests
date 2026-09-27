# =====================================================================
# Образ фронтенда: сборка Vite, раздача через nginx.
#
# Сборка: docker build -t kr-frontend ./frontend
#
# Две стадии: в первой ставим зависимости и собираем, во вторую кладём только
# готовые файлы. В итоговом образе нет ни node_modules, ни исходников —
# он маленький и в нём нечего исполнять.
# =====================================================================

# ---------- Стадия 1: сборка ----------
FROM node:22-alpine AS build

WORKDIR /app

# Сначала только манифесты: пока зависимости не менялись, слой берётся из кеша.
COPY package.json package-lock.json ./
# npm ci ставит РОВНО то, что записано в package-lock.json — сборка
# воспроизводима и не зависит от того, что вышло на npm сегодня.
RUN npm ci

COPY . .

# Пустой VITE_API_URL: фронт и API живут на одном домене, запросы идут на /api,
# а Caddy отправляет их бэкенду. Так браузер отдаёт cookie входа и не нужен CORS.
ENV VITE_API_URL=""

RUN npm run build

# ---------- Стадия 2: раздача ----------
FROM nginx:1.27-alpine

# Свой конфиг вместо стандартного: нужен возврат index.html для путей React.
COPY --from=build /app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Healthcheck: wget есть в alpine-образе nginx.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1/ || exit 1
