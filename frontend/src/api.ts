/**
 * Обёртка над обращениями к бэкенду.
 * Все запросы собраны здесь, чтобы страницы не знали про fetch и адреса.
 */

import type {
  AttemptDetail,
  AttemptResult,
  CreatedTest,
  MyTest,
  PasswordReset,
  PublicTestInfo,
  ResultsOverview,
  SchoolSettings,
  SchoolStats,
  StartedAttempt,
  SubmitPayload,
  TeacherRow,
  TestCreatePayload,
  User,
} from './types'

// Пусто = запросы идут на адрес самой страницы, а Vite передаёт их бэкенду
// (proxy в vite.config.ts). Так браузер отдаёт cookie входа, и нет CORS.
export const API_URL = import.meta.env.VITE_API_URL ?? ''

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
    // credentials: 'include' — без этого браузер не приложит cookie сессии,
    // и сервер посчитает, что никто не вошёл.
    return await fetch(url, { ...init, credentials: 'include' })
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

/** GET /api/tests/{id}/results — таблица учеников, матрица умений и сводка. */
export async function fetchResults(testId: number): Promise<ResultsOverview> {
  const response = await request(`${API_URL}/api/tests/${testId}/results`)
  return parse<ResultsOverview>(response)
}

/** GET /api/tests/{id}/attempts/{attemptId} — разбор работы. */
export async function fetchAttemptDetail(
  testId: number,
  attemptId: number,
): Promise<AttemptDetail> {
  const response = await request(`${API_URL}/api/tests/${testId}/attempts/${attemptId}`)
  return parse<AttemptDetail>(response)
}

/**
 * Адрес выгрузки в Excel.
 *
 * Файл не скачиваем через fetch: обычная ссылка проще и сразу даёт браузеру
 * правильное имя файла из заголовка Content-Disposition.
 */
export function resultsExportUrl(testId: number): string {
  return `${API_URL}/api/tests/${testId}/export.xlsx`
}

/** PATCH /api/tests/{id} — открыть или закрыть приём работ. */
export async function updateTestSettings(
  testId: number,
  isOpen: boolean,
): Promise<{ is_open: boolean }> {
  const response = await request(`${API_URL}/api/tests/${testId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_open: isOpen }),
  })
  return parse<{ is_open: boolean }>(response)
}

/** DELETE /api/tests/{id}/attempts/{attemptId} — удалить работу ученика. */
export async function deleteAttempt(testId: number, attemptId: number): Promise<void> {
  const response = await request(`${API_URL}/api/tests/${testId}/attempts/${attemptId}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
}

/**
 * DELETE /api/tests/{id} — удалить контрольную целиком.
 * confirmTitle сверяет сам сервер, поэтому случайно удалить нельзя.
 */
export async function deleteTest(testId: number, confirmTitle: string): Promise<void> {
  const url =
    `${API_URL}/api/tests/${testId}` +
    `?confirm_title=${encodeURIComponent(confirmTitle)}`

  const response = await request(url, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
}


/* ===================== Вход и учётные записи ===================== */

/** GET /api/auth/me — кто вошёл. null, если никто: это не ошибка. */
export async function fetchMe(): Promise<User | null> {
  const response = await request(`${API_URL}/api/auth/me`)
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
  return (await response.json()) as User | null
}

export async function login(email: string, password: string): Promise<User> {
  const response = await request(`${API_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  return parse<User>(response)
}

export async function register(payload: {
  full_name: string
  email: string
  password: string
  school_code: string
}): Promise<User> {
  const response = await request(`${API_URL}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parse<User>(response)
}

export async function logout(): Promise<void> {
  const response = await request(`${API_URL}/api/auth/logout`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
}

/** GET /api/my/tests — список своих контрольных (у админа — всех). */
export async function fetchMyTests(): Promise<MyTest[]> {
  const response = await request(`${API_URL}/api/my/tests`)
  return parse<MyTest[]>(response)
}

/* ===================== Админка ===================== */

export async function fetchTeachers(): Promise<TeacherRow[]> {
  return parse<TeacherRow[]>(await request(`${API_URL}/api/admin/teachers`))
}

export async function setTeacherActive(
  userId: number,
  isActive: boolean,
): Promise<TeacherRow> {
  const response = await request(`${API_URL}/api/admin/teachers/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  })
  return parse<TeacherRow>(response)
}

export async function resetTeacherPassword(userId: number): Promise<PasswordReset> {
  const response = await request(
    `${API_URL}/api/admin/teachers/${userId}/reset-password`,
    { method: 'POST' },
  )
  return parse<PasswordReset>(response)
}

export async function fetchSchoolSettings(): Promise<SchoolSettings> {
  return parse<SchoolSettings>(await request(`${API_URL}/api/admin/settings`))
}

export async function updateSchoolSettings(schoolCode: string): Promise<SchoolSettings> {
  const response = await request(`${API_URL}/api/admin/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ school_code: schoolCode }),
  })
  return parse<SchoolSettings>(response)
}

/** GET /api/admin/stats — статистика по школе с фильтрами. */
export async function fetchSchoolStats(filters: {
  days?: number
  subject?: string
  studentClass?: string
}): Promise<SchoolStats> {
  const query = new URLSearchParams()
  if (filters.days) query.set('days', String(filters.days))
  if (filters.subject) query.set('subject', filters.subject)
  if (filters.studentClass) query.set('student_class', filters.studentClass)

  const suffix = query.toString() ? `?${query.toString()}` : ''
  return parse<SchoolStats>(await request(`${API_URL}/api/admin/stats${suffix}`))
}
