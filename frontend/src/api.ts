/**
 * Обёртка над обращениями к бэкенду.
 * Все запросы к API собраны здесь, чтобы страницы не знали про fetch и адреса.
 */

import type {
  AttemptPayload,
  AttemptResult,
  CreatedTest,
  PublicTest,
  SavedTest,
  TestDraft,
} from './types'

// import.meta.env — так Vite отдаёт переменные из .env.local.
// Если переменной нет, подставляем локальный адрес бэкенда.
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

/** Ответ эндпоинта GET /health */
export type HealthResponse = {
  status: string
  database: 'up' | 'down'
}

/**
 * Достаёт понятный текст ошибки из ответа бэкенда.
 *
 * Наш бэкенд кладёт человеческое описание в поле detail
 * (например: «Вопрос 2: correct = 3, но вариантов 3...»).
 * Если этого поля нет — показываем хотя бы код ответа.
 */
async function extractError(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json()
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') {
        return detail
      }
    }
  } catch {
    // тело ответа не JSON — ничего страшного, используем запасной текст
  }

  return `Бэкенд ответил ошибкой ${response.status}`
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_URL}/health`)

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as HealthResponse
}

/** POST /api/tests — публикует контрольную и возвращает код ссылки. */
export async function createTest(draft: TestDraft): Promise<CreatedTest> {
  const response = await fetch(`${API_URL}/api/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(draft),
  })

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as CreatedTest
}

/**
 * GET /api/tests/{code} — контрольная целиком (превью для учителя).
 * Понадобится на следующих шагах; здесь уже описан, чтобы все запросы лежали рядом.
 */
export async function fetchTest(code: string): Promise<SavedTest> {
  const response = await fetch(`${API_URL}/api/tests/${encodeURIComponent(code)}`)

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as SavedTest
}

/* ===================== Публичная часть: экран ученика ===================== */

/** GET /api/public/tests/{code} — тест для прохождения, без правильных ответов. */
export async function fetchPublicTest(code: string): Promise<PublicTest> {
  const response = await fetch(`${API_URL}/api/public/tests/${encodeURIComponent(code)}`)

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as PublicTest
}

/** POST /api/public/tests/{code}/attempts — сдать работу и получить результат. */
export async function submitAttempt(
  code: string,
  payload: AttemptPayload,
): Promise<AttemptResult> {
  const response = await fetch(
    `${API_URL}/api/public/tests/${encodeURIComponent(code)}/attempts`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as AttemptResult
}
