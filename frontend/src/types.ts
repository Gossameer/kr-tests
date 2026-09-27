/**
 * Типы данных, общие для всех файлов фронтенда.
 *
 * Модель: контрольная → умения (что проверяем) + варианты (1..N).
 * Задание принадлежит варианту и умению, отвечать на него можно
 * вводом текста ('input') или выбором варианта ('choice').
 */

/** Как ученик отвечает на задание. */
export type AnswerFormat = 'input' | 'choice'

/** Русские названия форматов — для подписей на экране. */
export const FORMAT_NAMES: Record<AnswerFormat, string> = {
  input: 'ввод ответа',
  choice: 'выбор из вариантов',
}

/* ===================== Создание контрольной ===================== */

/** Умение: что проверяет группа заданий. */
export type SkillDraft = {
  title: string
  /** Сколько заданий на это умение в каждом варианте. */
  tasksPerVariant: number
  answerFormat: AnswerFormat
}

/** Одно задание конкретного варианта. */
export type TaskDraft = {
  /** Номер умения в списке, считая с единицы. */
  skillIndex: number
  text: string
  answerFormat: AnswerFormat
  /** Для 'choice': варианты ответа и номер верного (с нуля). */
  options: string[]
  correct: number | null
  /** Для 'input': все ответы, которые засчитываются. */
  acceptedAnswers: string[]
  /** Краткое решение — видит только учитель. */
  solution: string
}

/** Один вариант контрольной. */
export type VariantDraft = {
  variantNo: number
  tasks: TaskDraft[]
}

/** Тело POST /api/tests */
export type TestCreatePayload = {
  title: string
  teacher_name: string
  classes: string[]
  variants_count: number
  shuffle: boolean
  skills: {
    title: string
    tasks_per_variant: number
    answer_format: AnswerFormat
  }[]
  variants: {
    variant_no: number
    tasks: {
      skill_index: number
      text: string
      answer_format: AnswerFormat
      options: string[]
      correct: number | null
      accepted_answers: string[]
      solution: string
    }[]
  }[]
}

/** Ответ POST /api/tests */
export type CreatedTest = {
  id: number
  code: string
  results_token: string
  title: string
  variants_count: number
  skills_count: number
  tasks_count: number
}

/* ===================== Экран ученика ===================== */

/** Ответ GET /api/public/tests/{code} — шапка до нажатия «Начать». */
export type PublicTestInfo = {
  code: string
  title: string
  teacher_name: string
  classes: string[]
  is_open: boolean
  tasks_count: number
}

export type PublicOption = {
  id: number
  text: string
}

/** Задание для ученика: без правильного ответа и без решения. */
export type PublicTask = {
  id: number
  position: number
  text: string
  answer_format: AnswerFormat
  options: PublicOption[]
}

/** Ответ POST /api/public/tests/{code}/start */
export type StartedAttempt = {
  attempt_id: number
  attempt_token: string
  variant_no: number
  student_name: string
  student_class: string
  title: string
  shuffle: boolean
  tasks: PublicTask[]
}

/** Тело сдачи работы. */
export type SubmitPayload = {
  attempt_token: string
  /** {id задания: id выбранного варианта} */
  choices: Record<number, number>
  /** {id задания: введённый текст} */
  inputs: Record<number, string>
}

/** Итог по одному заданию. Правильный ответ не раскрывается. */
export type TaskResult = {
  task_id: number
  position: number
  answered: boolean
  is_correct: boolean
}

/** Ответ на сдачу работы. */
export type AttemptResult = {
  attempt_id: number
  variant_no: number
  student_name: string
  student_class: string
  score: number
  max_score: number
  results: TaskResult[]
}

/**
 * Что кладём в localStorage после сдачи: результат и тексты заданий,
 * чтобы экран результата открывался и без связи с сервером.
 */
export type StoredAttempt = {
  savedAt: string
  code: string
  title: string
  result: AttemptResult
  tasks: { id: number; text: string; position: number }[]
}

/** Начатая, но не сданная работа — чтобы продолжить после перезагрузки. */
export type StoredProgress = {
  code: string
  attemptId: number
  attemptToken: string
  variantNo: number
  studentName: string
  studentClass: string
}

/* ===================== Результаты для учителя ===================== */

export type SkillInfo = {
  id: number
  position: number
  title: string
  tasks_per_variant: number
  answer_format: AnswerFormat
}

/** Строка таблицы учеников. */
export type AttemptRow = {
  attempt_id: number
  student_name: string
  student_class: string
  variant_no: number
  score: number
  max_score: number
  percent: number
  finished_at: string | null
  /** {id умения: процент выполнения} — основа матрицы «ученик × умение». */
  skill_percents: Record<number, number>
}

/** Сводка по умению для всего класса. */
export type SkillStat = {
  skill_id: number
  position: number
  title: string
  correct: number
  total: number
  percent: number
}

/** Ответ GET /api/results/{token} */
export type ResultsOverview = {
  title: string
  teacher_name: string
  code: string
  classes: string[]
  variants_count: number
  is_open: boolean
  skills: SkillInfo[]
  attempts_count: number
  attempts: AttemptRow[]
  skill_stats: SkillStat[]
}

/** Одно задание в разборе работы — здесь правильный ответ виден. */
export type AttemptDetailItem = {
  task_id: number
  position: number
  skill_title: string
  text: string
  answer_format: AnswerFormat
  student_answer: string | null
  correct_answer: string
  solution: string
  answered: boolean
  is_correct: boolean
}

/** Ответ GET /api/results/{token}/attempts/{id} */
export type AttemptDetail = {
  attempt_id: number
  student_name: string
  student_class: string
  variant_no: number
  score: number
  max_score: number
  percent: number
  finished_at: string | null
  items: AttemptDetailItem[]
}
