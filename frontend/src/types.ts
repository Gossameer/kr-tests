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
  /** Предмет: нужен для школьной статистики. */
  subject: string
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
  id: number
  title: string
  subject: string
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


/* ===================== Учётные записи ===================== */

export type UserRole = 'teacher' | 'admin'

/** Кто сейчас вошёл. */
export type User = {
  id: number
  full_name: string
  email: string
  role: UserRole
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

/** Строка списка «Мои контрольные» — приходит с сервера, а не из браузера. */
export type MyTest = {
  id: number
  code: string
  title: string
  subject: string
  classes: string[]
  variants_count: number
  is_open: boolean
  created_at: string
  attempts_count: number
  teacher_name: string
  teacher_id: number
}

/* ===================== Админка ===================== */

export type TeacherRow = {
  id: number
  full_name: string
  email: string
  role: UserRole
  is_active: boolean
  created_at: string
  last_login_at: string | null
  tests_count: number
  attempts_count: number
}

export type PasswordReset = {
  user_id: number
  email: string
  temporary_password: string
}

export type SchoolSettings = {
  school_code: string
}

export type SchoolStats = {
  totals: {
    teachers: number
    tests: number
    attempts_total: number
    attempts_week: number
    attempts_month: number
  }
  by_teacher: {
    id: number
    full_name: string
    tests_count: number
    attempts_count: number
    average_percent: number
  }[]
  by_subject: { subject: string; attempts_count: number; average_percent: number }[]
  by_class: { student_class: string; attempts_count: number; average_percent: number }[]
  weak_skills: {
    skill_id: number
    title: string
    subject: string
    test_title: string
    teacher_name: string
    student_class: string
    answers_count: number
    percent: number
    is_weak: boolean
  }[]
  by_day: { day: string; attempts_count: number }[]
  filters: { subjects: string[]; classes: string[]; weak_below: number }
}
