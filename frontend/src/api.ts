/**
 * Обёртка над обращениями к бэкенду.
 * Все запросы собраны здесь, чтобы страницы не знали про fetch и адреса.
 */

import type {
  UserRole,
  AdminLogEntry,
  ClassLink,
  AdminAiOverview,
  AiJob,
  AiStatus,
  AttemptDetail,
  ImportPreview,
  ImportResult,
  InviteLink,
  MailOverview,
  TokenInfo,
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
  SkillDraft,
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
export const NETWORK_ERROR =
  'Нет связи с сервером. Проверьте интернет и обновите страницу — введённое не пропадёт.'

/** Сообщение, когда сервер ответил, но упал внутри себя. */
export const SERVER_ERROR =
  'На сервере что-то сломалось. Подождите минуту и попробуйте ещё раз.'

/**
 * Понятный текст по коду ответа — когда сервер не прислал своего описания.
 * Без номеров и технических слов: человеку важно, что делать дальше.
 */
function messageForStatus(status: number): string {
  if (status >= 500) {
    return SERVER_ERROR
  }
  switch (status) {
    case 401:
      return 'Войдите в систему заново — сессия закончилась.'
    case 403:
      return 'У вас нет доступа к этой странице.'
    case 404:
      return 'Ничего не нашлось. Проверьте ссылку или вернитесь на главную.'
    case 413:
      return 'Слишком много данных за раз. Уменьшите число заданий или вариантов.'
    case 429:
      return 'Слишком много попыток подряд. Подождите минуту и попробуйте снова.'
    default:
      return 'Не получилось. Обновите страницу и попробуйте ещё раз.'
  }
}

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

  return messageForStatus(response.status)
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

/** POST /api/tests — публикует проверочную работу и возвращает обе ссылки. */
export async function createTest(payload: TestCreatePayload): Promise<CreatedTest> {
  const response = await request(`${API_URL}/api/tests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parse<CreatedTest>(response)
}

/* ===================== Ученик ===================== */

/** GET /api/public/tests/{code} — шапка проверочной работы до нажатия «Начать». */
export async function fetchPublicTest(code: string): Promise<PublicTestInfo> {
  const response = await request(`${API_URL}/api/public/tests/${encodeURIComponent(code)}`)
  return parse<PublicTestInfo>(response)
}

/** POST /api/public/tests/{code}/start — получить вариант и задания. */
export async function startAttempt(
  code: string,
  studentName: string,
  studentClass: string,
  deviceId: string,
): Promise<StartedAttempt> {
  const response = await request(
    `${API_URL}/api/public/tests/${encodeURIComponent(code)}/start`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        student_name: studentName,
        student_class: studentClass,
        device_id: deviceId,
      }),
    },
  )
  return parse<StartedAttempt>(response)
}

/**
 * POST /api/public/tests/{code}/attempts/{id}/check — действует ли ещё попытка,
 * которую помнит браузер. «gone» — учитель разрешил пересдачу или удалил её.
 */
export async function checkAttempt(
  code: string,
  attemptId: number,
  attemptToken: string,
): Promise<'active' | 'gone'> {
  const response = await postJson(
    `${API_URL}/api/public/tests/${encodeURIComponent(code)}/attempts/${attemptId}/check`,
    { attempt_token: attemptToken },
  )
  return (await parse<{ state: 'active' | 'gone' }>(response)).state
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

/** POST /api/tests/{id}/classes — добавить класс: у него появится своя ссылка. */
export async function addTestClass(testId: number, className: string): Promise<ClassLink> {
  return parse<ClassLink>(
    await postJson(`${API_URL}/api/tests/${testId}/classes`, { class_name: className }),
  )
}

/** PATCH /api/tests/{id}/classes/{classId} — открыть или закрыть приём по классу. */
export async function updateTestClass(
  testId: number,
  classId: number,
  isOpen: boolean,
): Promise<ClassLink> {
  const response = await request(`${API_URL}/api/tests/${testId}/classes/${classId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_open: isOpen }),
  })
  return parse<ClassLink>(response)
}

/** POST /api/tests/{id}/attempts/{attemptId}/annul — разрешить пересдачу. */
export async function annulAttempt(testId: number, attemptId: number): Promise<void> {
  const response = await postJson(`${API_URL}/api/tests/${testId}/attempts/${attemptId}/annul`)
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
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
 * DELETE /api/tests/{id} — удалить проверочную работу целиком.
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

/** GET /api/my/tests — список своих проверочных работ (у админа — всех). */
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

/** PUT /api/admin/teachers/{id}/role — выдать или снять права администратора. */
export async function setTeacherRole(userId: number, role: UserRole): Promise<TeacherRow> {
  const response = await request(`${API_URL}/api/admin/teachers/${userId}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
  return parse<TeacherRow>(response)
}

/** GET /api/admin/log — журнал выдачи прав администратора. */
export async function fetchAdminLog(): Promise<AdminLogEntry[]> {
  return parse<AdminLogEntry[]>(await request(`${API_URL}/api/admin/log`))
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

export async function updateSchoolSettings(
  schoolCode: string,
  allowSelfRegistration: boolean,
): Promise<SchoolSettings> {
  const response = await request(`${API_URL}/api/admin/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      school_code: schoolCode,
      allow_self_registration: allowSelfRegistration,
    }),
  })
  return parse<SchoolSettings>(response)
}

/**
 * GET /api/stats — статистика с фильтрами. Учителю сервер отдаёт только его
 * работы, администратору — всю школу.
 */
export async function fetchSchoolStats(filters: {
  days?: number
  subject?: string
  studentClass?: string
  testId?: number
}): Promise<SchoolStats> {
  const query = new URLSearchParams()
  if (filters.days) query.set('days', String(filters.days))
  if (filters.subject) query.set('subject', filters.subject)
  if (filters.studentClass) query.set('student_class', filters.studentClass)
  if (filters.testId) query.set('test_id', String(filters.testId))

  const suffix = query.toString() ? `?${query.toString()}` : ''
  return parse<SchoolStats>(await request(`${API_URL}/api/stats${suffix}`))
}

/* ===================== Генерация через ИИ ===================== */

/** Что нужно ИИ, кроме умений: предмет, тема, класс. */
export type AiContext = {
  subject: string
  topic: string
  grade: string
  skills: SkillDraft[]
}

function aiSkills(skills: SkillDraft[]) {
  return skills.map((skill) => ({
    title: skill.title.trim(),
    tasks_per_variant: skill.tasksPerVariant,
    answer_format: skill.answerFormat,
  }))
}

/** GET /api/ai/status — включена ли генерация и сколько осталось на сегодня. */
export async function fetchAiStatus(): Promise<AiStatus> {
  return parse<AiStatus>(await request(`${API_URL}/api/ai/status`))
}

/** POST /api/ai/jobs — запустить генерацию всех вариантов в фоне. */
export async function startAiJob(
  context: AiContext,
  variantsCount: number,
): Promise<{ job_id: number }> {
  const response = await request(`${API_URL}/api/ai/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      subject: context.subject,
      topic: context.topic,
      grade: context.grade,
      skills: aiSkills(context.skills),
      variants_count: variantsCount,
    }),
  })
  return parse<{ job_id: number }>(response)
}

/** GET /api/ai/jobs/{id} — прогресс и результат. */
export async function fetchAiJob(jobId: number): Promise<AiJob> {
  return parse<AiJob>(await request(`${API_URL}/api/ai/jobs/${jobId}`))
}

/**
 * POST /api/ai/jobs/{id}/resume — догенерировать недостающее: клетки, которые
 * не удались или прерваны перезапуском сервера. Готовые задания не трогаются.
 */
export async function resumeAiJob(jobId: number): Promise<void> {
  const response = await request(`${API_URL}/api/ai/jobs/${jobId}/resume`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await extractError(response))
  }
}

/** POST /api/ai/task-jobs — перегенерировать одно задание (тоже в фоне). */
export async function startAiTaskJob(
  context: AiContext,
  params: {
    skillIndex: number
    variantNo: number
    variantsCount: number
    currentText: string
    avoidTexts: string[]
  },
): Promise<{ job_id: number }> {
  const response = await request(`${API_URL}/api/ai/task-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      subject: context.subject,
      topic: context.topic,
      grade: context.grade,
      skills: aiSkills(context.skills),
      skill_index: params.skillIndex,
      variant_no: params.variantNo,
      variants_count: params.variantsCount,
      current_text: params.currentText,
      avoid_texts: params.avoidTexts,
    }),
  })
  return parse<{ job_id: number }>(response)
}

/** GET /api/admin/ai — расход ИИ и лимит. */
export async function fetchAdminAi(): Promise<AdminAiOverview> {
  return parse<AdminAiOverview>(await request(`${API_URL}/api/admin/ai`))
}

/** PUT /api/admin/ai — сменить дневной лимит генераций на учителя. */
export async function updateAiLimit(dailyLimit: number): Promise<{ daily_limit: number }> {
  const response = await request(`${API_URL}/api/admin/ai`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ daily_limit: dailyLimit }),
  })
  return parse<{ daily_limit: number }>(response)
}

/* ===================== Приглашения и сброс пароля ===================== */

function postJson(url: string, body?: unknown): Promise<Response> {
  return request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** GET /api/auth/options — показывать ли ссылку на регистрацию. */
export async function fetchAuthOptions(): Promise<{ self_registration: boolean }> {
  return parse<{ self_registration: boolean }>(await request(`${API_URL}/api/auth/options`))
}

/** POST /api/auth/forgot — «забыли пароль?». Ответ одинаковый для любого адреса. */
export async function forgotPassword(email: string): Promise<{ message: string }> {
  return parse<{ message: string }>(await postJson(`${API_URL}/api/auth/forgot`, { email }))
}

/** GET /api/auth/tokens/{token} — чья это ссылка; ошибка, если она больше не годится. */
export async function fetchTokenInfo(token: string): Promise<TokenInfo> {
  return parse<TokenInfo>(
    await request(`${API_URL}/api/auth/tokens/${encodeURIComponent(token)}`),
  )
}

/** POST /api/auth/tokens/{token} — задать пароль по ссылке и сразу войти. */
export async function setPasswordByToken(token: string, password: string): Promise<User> {
  return parse<User>(
    await postJson(`${API_URL}/api/auth/tokens/${encodeURIComponent(token)}`, { password }),
  )
}

/** POST /api/admin/teachers/import/preview — проверить список, ничего не создавая. */
export async function previewTeacherImport(text: string): Promise<ImportPreview> {
  return parse<ImportPreview>(
    await postJson(`${API_URL}/api/admin/teachers/import/preview`, { text }),
  )
}

/** POST /api/admin/teachers/import — создать учётки и отправить приглашения. */
export async function importTeachers(text: string): Promise<ImportResult> {
  return parse<ImportResult>(await postJson(`${API_URL}/api/admin/teachers/import`, { text }))
}

/**
 * POST /api/admin/teachers/{id}/invite — новое приглашение.
 * send=true — письмом, false — только ссылка. Прежние ссылки перестают работать.
 */
export async function inviteTeacher(userId: number, send: boolean): Promise<InviteLink> {
  return parse<InviteLink>(
    await postJson(`${API_URL}/api/admin/teachers/${userId}/invite`, { send }),
  )
}

/** POST /api/admin/teachers/invite-pending — ещё раз всем, кто не активировал. */
export async function invitePending(): Promise<{ invited: InviteLink[] }> {
  return parse<{ invited: InviteLink[] }>(
    await postJson(`${API_URL}/api/admin/teachers/invite-pending`),
  )
}

/** GET /api/admin/mail — настроена ли почта и журнал писем. */
export async function fetchMailOverview(): Promise<MailOverview> {
  return parse<MailOverview>(await request(`${API_URL}/api/admin/mail`))
}
