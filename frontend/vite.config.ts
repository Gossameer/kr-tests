import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  server: {
    // host: по умолчанию Vite слушает 'localhost', а Node на Windows разворачивает
    // 'localhost' в IPv6-адрес ::1 — и сервер оказывается доступен ТОЛЬКО по IPv6.
    // Браузер при этом часто стучится на IPv4 (127.0.0.1) и получает «сайт не найден».
    // Явный 127.0.0.1 = слушаем IPv4-петлю, и работают оба адреса:
    // http://127.0.0.1:5174 и http://localhost:5174.
    //
    // Почему не host: true — это 0.0.0.0, то есть сервер виден всей локальной сети,
    // и Windows показывает запрос брандмауэра. Для локальной разработки это не нужно.
    host: '127.0.0.1',

    // Свой порт, чтобы не сталкиваться с другим Vite-проектом (school-frontend на 5173).
    port: 5174,

    // strictPort: если 5174 всё-таки занят — Vite честно упадёт с ошибкой,
    // а не переедет молча на 5175. Иначе адрес в браузере перестаёт совпадать.
    strictPort: true,
  },
})
