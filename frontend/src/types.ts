/** Типы данных, общие для всех файлов фронтенда. */

/** Один вопрос в том виде, в каком его присылает ИИ и принимает бэкенд. */
export type DraftQuestion = {
  text: string
  options: string[]
  /** Индекс правильного варианта, считая с нуля. */
  correct: number
}

/** Контрольная, готовая к отправке на бэкенд. */
export type TestDraft = {
  title: string
  questions: DraftQuestion[]
}

/** Ответ бэкенда на POST /api/tests */
export type CreatedTest = {
  id: number
  code: string
  /** Длинный секрет для ссылки на результаты — показывать только учителю. */
  results_token: string
  title: string
  questions_count: number
}

/** Вариант ответа в ответе GET /api/tests/{code} */
export type SavedOption = {
  id: number
  text: string
  is_correct: boolean
  position: number
}

export type SavedQuestion = {
  id: number
  text: string
  position: number
  options: SavedOption[]
}

/** Ответ бэкенда на GET /api/tests/{code} — превью для учителя. */
export type SavedTest = {
  id: number
  code: string
  title: string
  teacher_name: string
  is_published: boolean
  created_at: string
  questions: SavedQuestion[]
}

/* ===================== Публичная часть: экран ученика ===================== */

/** Вариант ответа так, как его видит ученик: без признака правильности. */
export type PublicOption = {
  id: number
  text: string
}

export type PublicQuestion = {
  id: number
  text: string
  position: number
  options: PublicOption[]
}

/** Ответ GET /api/public/tests/{code} */
export type PublicTest = {
  code: string
  title: string
  teacher_name: string
  questions: PublicQuestion[]
}

/** Тело POST /api/public/tests/{code}/attempts */
export type AttemptPayload = {
  student_name: string
  student_class: string
  /** {id вопроса: id выбранного варианта}. Пропущенных вопросов здесь просто нет. */
  answers: Record<number, number>
}

/** Итог по одному вопросу. Какой вариант верный — бэкенд не сообщает. */
export type QuestionResult = {
  question_id: number
  position: number
  answered: boolean
  is_correct: boolean
}

/** Ответ POST /api/public/tests/{code}/attempts */
export type AttemptResult = {
  attempt_id: number
  student_name: string
  student_class: string
  score: number
  max_score: number
  results: QuestionResult[]
}

/**
 * То, что кладём в localStorage после сдачи.
 *
 * Тексты вопросов сохраняем вместе с результатом, чтобы экран результата
 * открывался даже без связи с сервером.
 */
export type StoredAttempt = {
  savedAt: string
  code: string
  title: string
  result: AttemptResult
  questions: { id: number; text: string; position: number }[]
}

/* ===================== Результаты для учителя ===================== */

/** Строка таблицы сдавших. */
export type AttemptRow = {
  attempt_id: number
  student_name: string
  student_class: string
  score: number
  max_score: number
  percent: number
  finished_at: string | null
}

/** Сводка по одному вопросу. */
export type QuestionStat = {
  question_id: number
  position: number
  text: string
  correct_count: number
  wrong_count: number
  skipped_count: number
}

/** Ответ GET /api/results/{token} */
export type ResultsOverview = {
  title: string
  teacher_name: string
  code: string
  questions_count: number
  attempts_count: number
  attempts: AttemptRow[]
  question_stats: QuestionStat[]
}

/** Один вопрос в разборе работы. Здесь правильный вариант виден — это экран учителя. */
export type AttemptDetailItem = {
  question_id: number
  position: number
  question_text: string
  chosen_option_text: string | null
  correct_option_text: string | null
  answered: boolean
  is_correct: boolean
}

/** Ответ GET /api/results/{token}/attempts/{id} */
export type AttemptDetail = {
  attempt_id: number
  student_name: string
  student_class: string
  score: number
  max_score: number
  percent: number
  finished_at: string | null
  items: AttemptDetailItem[]
}
