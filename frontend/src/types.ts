/**
 * Типы данных, общие для всех файлов фронтенда.
 *
 * Модель: проверочная работа → умения (что проверяем) + варианты (1..N).
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

/* ===================== Создание проверочной работы ===================== */

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
  /**
   * Итог самопроверки ИИ, если ответы генерации и проверки не совпали
   * (или проверить не удалось). null/нет поля — вопросов к заданию нет.
   */
  review?: TaskReview | null
}

/**
 * Почему задание стоит посмотреть:
 *   mismatch  — при самопроверке ИИ получил другой ответ (generated ≠ checked);
 *   unchecked — самопроверка не выполнилась, в checked — короткая причина;
 *   ok        — ответы сошлись, но есть замечание к самому заданию (warning).
 */
export type TaskReview = {
  status: 'ok' | 'mismatch' | 'unchecked'
  generated: string
  checked: string
  /** Замечание к заданию: «ответ виден в условии» и т. п. Пусто — замечаний нет. */
  warning?: string
}

/** Один вариант проверочной работы. */
export type VariantDraft = {
  variantNo: number
  tasks: TaskDraft[]
  /**
   * Состояние клеток этого варианта при генерации через ИИ: ключ — номер умения.
   * Нет поля — вариант составлен вручную.
   */
  aiCells?: Record<number, AiCellState>
}

/** Что известно о клетке «умение × вариант» в таблице проверки. */
export type AiCellState = {
  status: AiCellStatus
  /** Какая версия результата ИИ уже перенесена в таблицу. */
  version: number
  error: string
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
/** Ссылка работы для одного класса: /t/<code>. */
export type ClassLink = {
  id: number
  class_name: string
  code: string
  is_open: boolean
  /** Сколько работ сдано в этом классе (аннулированные не в счёт). */
  attempts_count: number
}

export type CreatedTest = {
  id: number
  code: string
  /** У каждой новой работы — своя ссылка на каждый класс. */
  class_links: ClassLink[]
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
  /** Класс задан ссылкой (ссылка класса) — ученик вводит только ФИО. */
  class_name: string | null
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
  /**
   * Секрет попытки: по нему страница спрашивает сервер, действует ли ещё
   * результат (учитель мог разрешить пересдачу). У старых записей его нет.
   */
  attemptToken?: string
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
  /** Учитель разрешил пересдачу: попытка осталась для истории, в итоги не идёт. */
  annulled: boolean
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
  /** true — ссылки только по классам; false — старая работа с общей ссылкой (code). */
  links_by_class: boolean
  class_links: ClassLink[]
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

/** Строка списка «Мои проверочные работы» — приходит с сервера, а не из браузера. */
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
  links_by_class: boolean
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
  /** Что показать в столбце «Статус». */
  state: TeacherState
  /** Последнее письмо-приглашение: когда и чем кончилась отправка. */
  invite_sent_at: string | null
  invite_mail_status: MailStatus | null
  invite_mail_error: string
  invite_expires_at: string | null
}

/** Состояние учётки: приглашён / приглашение истекло / активен / отключён. */
export type TeacherState = 'invited' | 'invite_expired' | 'active' | 'disabled'

/** Что стало с письмом: в очереди, ушло, не ушло, почта не настроена. */
export type MailStatus = 'queued' | 'sent' | 'failed' | 'test'

/** Строка превью списка учителей. */
export type ImportRow = {
  line: number
  full_name: string
  email: string
  status: 'new' | 'exists' | 'duplicate' | 'invalid'
  message: string
}

/** Ответ POST /api/admin/teachers/import/preview */
export type ImportPreview = {
  rows: ImportRow[]
  summary: { new: number; exists: number; duplicate: number; invalid: number }
}

/** Выданное приглашение: ссылку можно передать учителю лично. */
export type InviteLink = {
  id: number
  full_name: string
  email: string
  invite_url: string
  expires_at: string
}

/** Ответ POST /api/admin/teachers/import */
export type ImportResult = {
  created: InviteLink[]
  skipped: ImportRow[]
  mail_configured: boolean
}

/** Ответ GET /api/admin/mail */
export type MailOverview = {
  configured: boolean
  from_address: string
  public_base_url: string
  log: {
    id: number
    to_email: string
    full_name: string | null
    kind: 'invite' | 'reset'
    created_at: string
    status: MailStatus
    sent_at: string | null
    error: string
  }[]
}

/** Ответ GET /api/auth/tokens/{token}: чья ссылка и какого она вида. */
export type TokenInfo = {
  kind: 'invite' | 'reset'
  full_name: string
  email: string
  expires_at: string
}

export type PasswordReset = {
  user_id: number
  email: string
  temporary_password: string
}

export type SchoolSettings = {
  school_code: string
  /** Можно ли учителю зарегистрироваться самому по школьному коду. */
  allow_self_registration: boolean
}

/** Строка журнала выдачи прав администратора. */
export type AdminLogEntry = {
  id: number
  created_at: string
  admin_name: string
  action: 'grant_admin' | 'revoke_admin'
  target_name: string
}

export type SchoolStats = {
  /** own — учитель, только его работы; school — администратор, вся школа. */
  scope: 'own' | 'school'
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
  filters: {
    subjects: string[]
    classes: string[]
    tests: { id: number; title: string }[]
    weak_below: number
  }
}

/* ===================== Генерация через ИИ ===================== */

/** Ответ GET /api/ai/status */
export type AiStatus = {
  enabled: boolean
  daily_limit: number
  used_today: number
}

/**
 * Состояние клетки: в очереди → составляется → проверяется → готова.
 * failed — ИИ не справился; interrupted — сервер перезапустили посреди работы.
 */
export type AiCellStatus = 'pending' | 'running' | 'checking' | 'ok' | 'failed' | 'interrupted'

/** Задание из результата генерации (поля — как в TaskIn на бэкенде). */
export type AiTask = {
  skill_index: number
  text: string
  answer_format: AnswerFormat
  options: string[]
  correct: number | null
  accepted_answers: string[]
  solution: string
  needs_review: boolean
  review: {
    status: 'ok' | 'mismatch' | 'unchecked'
    generated: string
    checked: string
    warning?: string
  }
}

/** Клетка результата: задания одного умения в одном варианте. */
export type AiCell = {
  variant_no: number
  skill_index: number
  status: AiCellStatus
  attempts: number
  version: number
  error: string
  tasks: AiTask[]
}

/** Ответ GET /api/ai/jobs/{id} */
export type AiJob = {
  id: number
  kind: 'test' | 'task'
  status: 'running' | 'done' | 'failed'
  error: string
  variants_count: number
  /** Прогресс считается по клеткам «умение × вариант». */
  total: number
  /** Готовых клеток (составлены и проверены). */
  done: number
  failed: number
  /** Клеток, прерванных перезапуском сервера. */
  interrupted_cells: number
  /** Сейчас составляется (или ждёт очереди) и проходит самопроверку. */
  generating: number
  checking: number
  /** Сервер перезапустили посреди генерации — есть что догенерировать. */
  interrupted: boolean
  needs_review: number
  /** С чем запускали генерацию: умения нужны, чтобы разложить задания по таблице. */
  request: {
    skills: { title: string; tasks_per_variant: number; answer_format: AnswerFormat }[]
  }
  cells: AiCell[]
  task: AiTask | null
}

export type AiTokens = { prompt_tokens: number; completion_tokens: number }

export type AiUsage = {
  requests: number
  failed: number
  prompt_tokens: number
  completion_tokens: number
}

/** Ответ GET /api/admin/ai */
export type AdminAiOverview = {
  enabled: boolean
  model: string
  check_model: string
  daily_limit: number
  today: { generate: AiUsage; check: AiUsage }
  month: { generate: AiUsage; check: AiUsage }
  by_teacher: {
    id: number
    full_name: string
    email: string
    jobs_today: number
    jobs_month: number
    generate_day: AiTokens
    check_day: AiTokens
    generate_month: AiTokens
    check_month: AiTokens
  }[]
}
