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
