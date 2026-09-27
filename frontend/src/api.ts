/**
 * Обёртка над обращениями к бэкенду.
 * Все запросы собраны здесь, чтобы страницы не знали про fetch и адреса.
 */

import type {
  AttemptDetail,
  AttemptResult,
  CreatedTest,
  PublicTestInfo,
  ResultsOverview,
  StartedAttempt,
  SubmitPayload,
  TestCreatePayload,
} from './types'

// import.meta.env — так Vite отдаёт переменные из .env.local.
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
 */
async function request(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch {
    throw new Error(NETWORK_ERROR)
  }
}

/**
 * Достаёт понятный текст ошибки из ответа бэкенда.
 *
 * Наш бэкенд кладёт человеческое описание в поле detail. Если описания нет —
 * подбираем текст по коду ответа, без технических подробностей.
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

  if (response.status >= 500) {
    return SERVER_ERROR
  }

  return `Сервер ответил ошибкой ${response.status}. Попробуйте обновить страницу.`
}

/** Общий разбор ответа: либо данные, либо понятная ошибка. */
async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
  return (await response.json()) as T
}

export async function fetchHealth(): Promise<HealthResponse> {
  return parse<HealthResponse>(await request(`${API_URL}/health`))
}

/* ===================== Учитель: создание ===================== */

/** POST /api/tests — публикует контрольную и возвращает обе ссылки. */
export async function createTest(payload: TestCreatePayload): Promise<CreatedTest> {
  const response = await request(`${API_URL}/api/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parse<CreatedTest>(response)
}

/* ===================== Ученик ===================== */

/** GET /api/public/tests/{code} — шапка контрольной до нажатия «Начать». */
export async function fetchPublicTest(code: string): Promise<PublicTestInfo> {
  const response = await request(`${API_URL}/api/public/tests/${encodeURIComponent(code)}`)
  return parse<PublicTestInfo>(response)
}

/** POST /api/public/tests/{code}/start — получить вариант и задания. */
export async function startAttempt(
  code: string,
  studentName: string,
  studentClass: string,
): Promise<StartedAttempt> {
  const response = await request(
    `${API_URL}/api/public/tests/${encodeURIComponent(code)}/start`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_name: studentName, student_class: studentClass }),
    },
  )
  return parse<StartedAttempt>(response)
}

/** POST /api/public/tests/{code}/attempts/{id}/submit — сдать работу. */
export async function submitAttempt(
  code: string,
  attemptId: number,
  payload: SubmitPayload,
): Promise<AttemptResult> {
  const response = await request(
    `${API_URL}/api/public/tests/${encodeURIComponent(code)}/attempts/${attemptId}/submit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
  return parse<AttemptResult>(response)
}

/* ===================== Учитель: результаты ===================== */

/** GET /api/results/{token} — таблица учеников, матрица умений и сводка. */
export async function fetchResults(token: string): Promise<ResultsOverview> {
  const response = await request(`${API_URL}/api/results/${encodeURIComponent(token)}`)
  return parse<ResultsOverview>(response)
}

/** GET /api/results/{token}/attempts/{id} — разбор работы. */
export async function fetchAttemptDetail(
  token: string,
  attemptId: number,
): Promise<AttemptDetail> {
  const response = await request(
    `${API_URL}/api/results/${encodeURIComponent(token)}/attempts/${attemptId}`,
  )
  return parse<AttemptDetail>(response)
}

/**
 * Адрес выгрузки в Excel.
 *
 * Файл не скачиваем через fetch: обычная ссылка проще и сразу даёт браузеру
 * правильное имя файла из заголовка Content-Disposition.
 */
export function resultsExportUrl(token: string): string {
  return `${API_URL}/api/results/${encodeURIComponent(token)}/export.xlsx`
}

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
  return parse<{ is_open: boolean }>(response)
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
 * confirmTitle сверяет сам сервер, поэтому случайно удалить нельзя.
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
