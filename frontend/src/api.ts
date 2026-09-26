/**
 * Обёртка над обращениями к бэкенду.
 * Все запросы к API собраны здесь, чтобы страницы не знали про fetch и адреса.
 */

import type {
  AttemptDetail,
  AttemptPayload,
  AttemptResult,
  CreatedTest,
  PublicTest,
  ResultsOverview,
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

/** Сообщение, когда браузер вообще не смог достучаться до сервера. */
export const NETWORK_ERROR = 'Нет связи с сервером, обновите страницу.'

/** Сообщение, когда сервер ответил, но упал внутри себя. */
export const SERVER_ERROR = 'Ошибка на сервере, сообщите учителю.'

/**
 * Выполняет запрос и превращает любые сбои в понятный русский текст.
 *
 * Без этой обёртки ученик видел бы «Failed to fetch» — так браузер сообщает,
 * что до сервера не достучаться (сервер не запущен, нет сети, запрет CORS).
 * Текст английский и ученику ничего не объясняет.
 */
async function request(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch {
    // fetch бросает исключение только когда ответа не было вообще.
    throw new Error(NETWORK_ERROR)
  }
}

/**
 * Достаёт понятный текст ошибки из ответа бэкенда.
 *
 * Наш бэкенд кладёт человеческое описание в поле detail
 * (например: «Вопрос 2: correct = 3, но вариантов 3...»).
 * Если описания нет — подбираем текст по коду ответа, но НЕ показываем
 * технические подробности: их всё равно некому читать.
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
    // тело ответа не JSON — например, голое «Internal Server Error»
  }

  // 5xx — сломался сервер, ученик тут ничего не исправит.
  if (response.status >= 500) {
    return SERVER_ERROR
  }

  return `Сервер ответил ошибкой ${response.status}. Попробуйте обновить страницу.`
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await request(`${API_URL}/health`)

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as HealthResponse
}

/** POST /api/tests — публикует контрольную и возвращает код ссылки. */
export async function createTest(draft: TestDraft): Promise<CreatedTest> {
  const response = await request(`${API_URL}/api/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(draft),
  })

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as CreatedTest
}

/* ===================== Публичная часть: экран ученика ===================== */

/** GET /api/public/tests/{code} — тест для прохождения, без правильных ответов. */
export async function fetchPublicTest(code: string): Promise<PublicTest> {
  const response = await request(`${API_URL}/api/public/tests/${encodeURIComponent(code)}`)

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
  const response = await request(
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

/* ===================== Результаты для учителя ===================== */

/** GET /api/results/{token} — таблица сдавших и сводка по вопросам. */
export async function fetchResults(token: string): Promise<ResultsOverview> {
  const response = await request(`${API_URL}/api/results/${encodeURIComponent(token)}`)

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as ResultsOverview
}

/** GET /api/results/{token}/attempts/{id} — разбор одной работы. */
export async function fetchAttemptDetail(
  token: string,
  attemptId: number,
): Promise<AttemptDetail> {
  const response = await request(
    `${API_URL}/api/results/${encodeURIComponent(token)}/attempts/${attemptId}`,
  )

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as AttemptDetail
}

/**
 * Адрес выгрузки в Excel.
 *
 * Файл не скачиваем через fetch: обычная ссылка проще и сразу даёт
 * браузеру правильное имя файла из заголовка Content-Disposition.
 */
export function resultsExportUrl(token: string): string {
  return `${API_URL}/api/results/${encodeURIComponent(token)}/export.xlsx`
}

/* ===================== Управление контрольной ===================== */

/** PATCH /api/results/{token} — открыть или закрыть приём работ. */
export async function updateTestSettings(
  token: string,
  isOpen: boolean,
): Promise<{ is_open: boolean }> {
  const response = await request(`${API_URL}/api/results/${encodeURIComponent(token)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_open: isOpen }),
  })

  if (!response.ok) {
    throw new Error(await extractError(response))
  }

  return (await response.json()) as { is_open: boolean }
}

/** DELETE /api/results/{token}/attempts/{id} — удалить работу ученика. */
export async function deleteAttempt(token: string, attemptId: number): Promise<void> {
  const response = await request(
    `${API_URL}/api/results/${encodeURIComponent(token)}/attempts/${attemptId}`,
    { method: 'DELETE' },
  )

  if (!response.ok) {
    throw new Error(await extractError(response))
  }
}

/**
 * DELETE /api/results/{token} — удалить контрольную целиком.
 *
 * confirmTitle — название, которое учитель ввёл вручную. Сервер сверяет его сам,
 * поэтому случайно удалить контрольную нельзя даже в обход страницы.
 */
export async function deleteTest(token: string, confirmTitle: string): Promise<void> {
  const url =
    `${API_URL}/api/results/${encodeURIComponent(token)}` +
    `?confirm_title=${encodeURIComponent(confirmTitle)}`

  const response = await request(url, { method: 'DELETE' })

  if (!response.ok) {
    throw new Error(await extractError(response))
  }
}
