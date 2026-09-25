/**
 * Тонкая обёртка над обращениями к бэкенду.
 * Пока здесь только /health — дальше добавим методы для тестов и попыток.
 */

// import.meta.env — так Vite отдаёт переменные из .env.local.
// Если переменной нет, подставляем локальный адрес бэкенда.
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

/** Ответ эндпоинта GET /health */
export type HealthResponse = {
  status: string
  database: 'up' | 'down'
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_URL}/health`)

  if (!response.ok) {
    throw new Error(`Бэкенд ответил ошибкой ${response.status}`)
  }

  return (await response.json()) as HealthResponse
}
