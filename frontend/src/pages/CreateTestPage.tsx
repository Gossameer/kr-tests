/**
 * Страница «Создать проверочную работу».
 *
 * Порядок работы учителя:
 *   1. шапка: название, предмет, классы, число вариантов;
 *   2. список умений — что именно проверяем;
 *   3. промт для ИИ собирается из умений, числа вариантов, предмета и класса;
 *      ответ ИИ вставляется в поле и разбирается (можно и не пользоваться ИИ);
 *   4. экран проверки: таблица «варианты × умения», правка любого задания,
 *      замена отдельного задания через ИИ;
 *   5. «Опубликовать» — когда все задания заполнены.
 *
 * Учитель не должен гадать, что от него хотят: у каждого обязательного поля
 * подсказка с примером и ошибка прямо под полем, а перед кнопкой — живой список
 * «Чтобы опубликовать, осталось:» с переходом к нужному месту.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import {
  createTest,
  fetchAiJob,
  fetchAiStatus,
  resumeAiJob,
  startAiJob,
  startAiTaskJob,
} from '../api'
import CopyLink from '../components/CopyLink'
import SkillEditor from '../components/SkillEditor'
import TaskTable from '../components/TaskTable'
import {
  clearDraft,
  countNeedsReview,
  loadDraft,
  mergeJob,
  replaceTaskAt,
  saveDraft,
  sleep,
  taskFromAi,
} from '../lib/aiJob'
import type { TaskPlace } from '../lib/aiJob'
import { buildTestPrompt } from '../lib/aiPrompt'
import { RESUME_ID, buildChecklist, goToItem, plural } from '../lib/checklist'
import { usePageTitle } from '../lib/usePageTitle'
import { emptyTask, parseTestJson, validateTest } from '../lib/parseTestJson'
import type { AiJob, AiStatus, CreatedTest, SkillDraft, VariantDraft } from '../types'

/** Как часто спрашиваем сервер о прогрессе генерации. */
const POLL_MS = 2000

/** Сервер не примет класс длиннее этого (MAX_CLASS_LEN в schemas.py). */
const MAX_CLASS_LEN = 20

/** Пустая ли таблица: ни в одном задании учитель ещё ничего не написал. */
function isBlank(variants: VariantDraft[]): boolean {
  return variants.every((variant) =>
    variant.tasks.every(
      (task) =>
        task.text.trim() === '' &&
        task.solution.trim() === '' &&
        task.acceptedAnswers.every((answer) => answer.trim() === '') &&
        task.options.every((option) => option.trim() === ''),
    ),
  )
}

/** Разбирает «8А, 8Б 8В» в ['8А','8Б','8В']. */
function parseClasses(raw: string): string[] {
  return raw
    .split(/[,;\n]+/)
    .flatMap((part) => part.split(/\s+/))
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

/** Создаёт пустые варианты под текущие умения — чтобы таблицу можно было заполнить руками. */
function buildEmptyVariants(skills: SkillDraft[], variantsCount: number): VariantDraft[] {
  return Array.from({ length: variantsCount }, (_, index) => ({
    variantNo: index + 1,
    tasks: skills.flatMap((skill, skillPosition) =>
      Array.from({ length: skill.tasksPerVariant }, () =>
        emptyTask(skillPosition + 1, skill.answerFormat),
      ),
    ),
  }))
}

export default function CreateTestPage() {
  // Если на этой странице шла генерация через ИИ, черновик лежит в браузере:
  // возвращаем его, а прогресс подтянется с сервера.
  const [draft] = useState(loadDraft)

  // --- Шапка ---
  // ФИО учителя больше не спрашиваем: сервер берёт его из учётной записи.
  const [title, setTitle] = useState(draft?.title ?? '')
  const [subject, setSubject] = useState(draft?.subject ?? '')
  const [classesRaw, setClassesRaw] = useState(draft?.classesRaw ?? '')
  const [variantsCount, setVariantsCount] = useState(draft?.variantsCount ?? 2)
  const [shuffle, setShuffle] = useState(draft?.shuffle ?? true)

  // --- Умения ---
  const [skills, setSkills] = useState<SkillDraft[]>(draft?.skills ?? [])

  // --- Промт и ответ ИИ ---
  const [grade, setGrade] = useState(draft?.grade ?? '')
  const [topic, setTopic] = useState(draft?.topic ?? '')
  const [aiRaw, setAiRaw] = useState('')
  const [aiErrors, setAiErrors] = useState<string[]>([])
  const [aiLoaded, setAiLoaded] = useState('')
  const [promptCopied, setPromptCopied] = useState(false)

  // --- Генерация через ИИ на сервере ---
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null)
  const [jobId, setJobId] = useState<number | null>(draft?.jobId ?? null)
  const [job, setJob] = useState<AiJob | null>(null)
  // Меняется, когда опрос нужно запустить заново (например, после «повторить»).
  const [pollNonce, setPollNonce] = useState(0)
  const [genError, setGenError] = useState('')
  const [starting, setStarting] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [showManual, setShowManual] = useState(false)

  // --- Задания ---
  const [variants, setVariants] = useState<VariantDraft[]>(draft?.variants ?? [])

  // --- Подсказки ---
  // Поля, из которых учитель уже ушёл: ошибка под пустым полем появляется
  // только после этого — не сразу при открытии страницы.
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  // Учитель нажал «Опубликовать» — теперь показываем все ошибки разом.
  const [submitted, setSubmitted] = useState(false)

  // --- Публикация ---
  const [errors, setErrors] = useState<string[]>([])
  const [publishing, setPublishing] = useState(false)
  const [created, setCreated] = useState<CreatedTest | null>(null)

  usePageTitle('Создать проверочную работу')

  const classes = parseClasses(classesRaw)
  const longClasses = classes.filter((name) => name.length > MAX_CLASS_LEN)

  // Ошибки полей шапки: текст — что сделать и пример.
  const fieldErrors: Record<string, string> = {
    title: title.trim() === '' ? 'Введите название работы, например: «Дроби, 5 класс».' : '',
    subject: subject.trim() === '' ? 'Укажите предмет, например: алгебра.' : '',
    classes:
      classes.length === 0
        ? 'Укажите хотя бы один класс, например: 5А, 5Б.'
        : longClasses.length > 0
          ? `Слишком длинное название класса: «${longClasses[0]}». Пишите коротко, например: 5А.`
          : '',
  }
  /** Ошибка поля, если её уже пора показать. */
  function shownError(key: string): string {
    return submitted || touched[key] ? fieldErrors[key] : ''
  }
  function touch(key: string) {
    setTouched((previous) => (previous[key] ? previous : { ...previous, [key]: true }))
  }
  // В промт уходит «предмет, тема»: ИИ так точнее попадает в программу.
  const promptFields = {
    subject: [subject.trim(), topic.trim()].filter((part) => part !== '').join(', '),
    grade,
  }
  const prompt = buildTestPrompt(promptFields, skills, variantsCount)

  const aiEnabled = aiStatus?.enabled ?? false
  const aiLeft = aiStatus ? Math.max(0, aiStatus.daily_limit - aiStatus.used_today) : 0
  const jobRunning = job?.status === 'running' || (jobId !== null && job === null)
  const aiContext = { subject: subject.trim(), topic: topic.trim(), grade: grade.trim(), skills }

  // Доступна ли генерация. Ошибка запроса = считаем, что нет: ручной путь есть всегда.
  useEffect(() => {
    fetchAiStatus()
      .then(setAiStatus)
      .catch(() => setAiStatus({ enabled: false, daily_limit: 0, used_today: 0 }))
  }, [])

  // Опрос прогресса. Генерация живёт на сервере, страница только смотрит.
  useEffect(() => {
    if (jobId === null) {
      return
    }
    let stopped = false
    let timer = 0

    async function tick(id: number) {
      try {
        const fresh = await fetchAiJob(id)
        if (stopped) {
          return
        }
        setJob(fresh)
        setVariants((previous) => mergeJob(previous, fresh))
        setGenError('')
        if (fresh.status === 'running') {
          timer = window.setTimeout(() => void tick(id), POLL_MS)
        }
      } catch (error: unknown) {
        if (stopped) {
          return
        }
        const message = error instanceof Error ? error.message : 'Не удалось узнать прогресс'
        if (message === 'Генерация не найдена.') {
          // Генерацию удалили (или база другая) — черновик больше не к чему привязывать.
          setJobId(null)
          clearDraft()
          return
        }
        setGenError(message)
        timer = window.setTimeout(() => void tick(id), POLL_MS * 3)
      }
    }

    void tick(jobId)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [jobId, pollNonce])

  // Пока страница связана с генерацией, черновик хранится в браузере.
  useEffect(() => {
    if (jobId === null) {
      return
    }
    saveDraft({
      jobId,
      title,
      subject,
      classesRaw,
      variantsCount,
      shuffle,
      skills,
      topic,
      grade,
      variants,
    })
  }, [jobId, title, subject, classesRaw, variantsCount, shuffle, skills, topic, grade, variants])

  /**
   * Пока в таблице ничего не написано, она повторяет умения и число вариантов:
   * учитель сразу видит, какие задания предстоит заполнить. Как только в ней
   * появился текст (или идёт генерация), таблицу сами не трогаем.
   */
  function changeSkills(next: SkillDraft[]) {
    setSkills(next)
    if (jobId === null && isBlank(variants)) {
      setVariants(next.length > 0 ? buildEmptyVariants(next, variantsCount) : [])
    }
  }

  function changeVariantsCount(next: number) {
    setVariantsCount(next)
    if (jobId === null && isBlank(variants) && skills.length > 0) {
      setVariants(buildEmptyVariants(skills, next))
    }
  }

  /** Отвязывает страницу от генерации: дальше учитель работает вручную. */
  function detachJob() {
    setJobId(null)
    setJob(null)
    clearDraft()
  }

  async function handleGenerate() {
    if (skills.length === 0) {
      setGenError('Сначала добавьте умения — по ним ИИ составит задания.')
      return
    }
    if (skills.some((skill) => skill.title.trim() === '')) {
      setGenError('У каждого умения должно быть название.')
      return
    }
    const hasTasks = variants.some((variant) =>
      variant.tasks.some((task) => task.text.trim() !== ''),
    )
    if (hasTasks && !window.confirm('Задания в таблице будут заменены новыми. Продолжить?')) {
      return
    }

    setStarting(true)
    setGenError('')
    try {
      const { job_id: newJobId } = await startAiJob(aiContext, variantsCount)
      setVariants([])
      setJob(null)
      setAiLoaded('')
      setErrors([])
      setJobId(newJobId)
      setPollNonce((value) => value + 1)
      fetchAiStatus()
        .then(setAiStatus)
        .catch(() => undefined)
    } catch (error: unknown) {
      setGenError(error instanceof Error ? error.message : 'Не удалось запустить генерацию')
    } finally {
      setStarting(false)
    }
  }

  /**
   * «Догенерировать недостающее»: сервер заново составит только те клетки,
   * которых нет (не удались или прерваны перезапуском). Готовые задания и
   * правки учителя в них остаются как есть.
   */
  async function handleResume() {
    if (jobId === null) {
      return
    }
    setResuming(true)
    setGenError('')
    try {
      await resumeAiJob(jobId)
      setPollNonce((value) => value + 1)
    } catch (error: unknown) {
      setGenError(
        error instanceof Error ? error.message : 'Не удалось продолжить генерацию',
      )
    } finally {
      setResuming(false)
    }
  }

  /** Перегенерирует одно задание: то же умение и формат, остальное не трогает. */
  async function handleRegenerate(place: TaskPlace) {
    const variant = variants.find((item) => item.variantNo === place.variantNo)
    const current = variant?.tasks.filter((task) => task.skillIndex === place.skillIndex)[
      place.order
    ]
    // Все задания этого умения по всем вариантам — чтобы ИИ их не повторил.
    const avoidTexts = variants
      .flatMap((item) => item.tasks)
      .filter((task) => task.skillIndex === place.skillIndex && task.text.trim() !== '')
      .map((task) => task.text)

    const { job_id: taskJobId } = await startAiTaskJob(aiContext, {
      skillIndex: place.skillIndex,
      variantNo: place.variantNo,
      variantsCount,
      currentText: current?.text ?? '',
      avoidTexts,
    })

    for (;;) {
      await sleep(1500)
      const result = await fetchAiJob(taskJobId)
      if (result.status === 'running') {
        continue
      }
      if (result.status === 'done' && result.task) {
        const fresh = taskFromAi(result.task)
        setVariants((previous) => replaceTaskAt(previous, place, fresh))
        return
      }
      throw new Error(result.error || 'Не удалось сгенерировать задание.')
    }
  }

  async function handleCopyPrompt() {
    if (skills.length === 0) {
      setAiErrors(['Сначала добавьте умения — из них собирается промт.'])
      return
    }
    try {
      await navigator.clipboard.writeText(prompt)
      setPromptCopied(true)
      setAiErrors([])
      window.setTimeout(() => setPromptCopied(false), 2000)
    } catch {
      setAiErrors(['Браузер не дал скопировать — выделите текст промта вручную.'])
    }
  }

  /** Разбирает ответ ИИ и переносит задания в таблицу. */
  function handleLoadFromAi() {
    const result = parseTestJson(aiRaw)
    if (!result.ok) {
      setAiErrors(result.errors)
      setAiLoaded('')
      return
    }

    // Ответ вставлен вручную — связь с генерацией на сервере больше не нужна.
    detachJob()
    setVariants(result.variants)
    setAiErrors([])
    const tasksTotal = result.variants.reduce((sum, variant) => sum + variant.tasks.length, 0)
    setAiLoaded(
      `Загружено вариантов: ${result.variants.length}, заданий: ${tasksTotal}. ` +
        'Проверьте задания в таблице ниже и нажмите «Опубликовать».',
    )
  }

  /** Создаёт пустую таблицу — для тех, кто заполняет всё руками. */
  function handleBuildEmpty() {
    if (skills.length === 0) {
      setAiErrors(['Сначала добавьте умения — по ним строится таблица заданий.'])
      return
    }
    detachJob()
    setVariants(buildEmptyVariants(skills, variantsCount))
    setErrors([])
    setAiLoaded('')
  }

  async function handlePublish() {
    // Чего-то не хватает — показываем все ошибки и ведём к первой.
    const firstBlocking = checklist.find((item) => !item.soft)
    if (firstBlocking) {
      setSubmitted(true)
      setErrors([])
      goToItem(firstBlocking)
      return
    }

    // Последняя страховка: те же правила, что проверит сервер.
    const found = validateTest(skills, variantsCount, variants)
    if (found.length > 0) {
      setSubmitted(true)
      setErrors(found)
      return
    }

    const reviewCount = countNeedsReview(variants)
    if (
      reviewCount > 0 &&
      !window.confirm(
        `Заданий, которые требуют проверки: ${reviewCount}. ` +
          'У них ответ ИИ при генерации не совпал с ответом при самопроверке. ' +
          'Всё равно опубликовать?',
      )
    ) {
      return
    }

    setErrors([])
    setPublishing(true)

    try {
      const result = await createTest({
        title: title.trim(),
        subject: subject.trim(),
        classes,
        variants_count: variantsCount,
        shuffle,
        skills: skills.map((skill) => ({
          title: skill.title.trim(),
          tasks_per_variant: skill.tasksPerVariant,
          answer_format: skill.answerFormat,
        })),
        variants: variants.map((variant) => ({
          variant_no: variant.variantNo,
          tasks: variant.tasks.map((task) => ({
            skill_index: task.skillIndex,
            text: task.text.trim(),
            answer_format: task.answerFormat,
            options: task.options.map((option) => option.trim()),
            correct: task.correct,
            accepted_answers: task.acceptedAnswers
              .map((answer) => answer.trim())
              .filter((answer) => answer !== ''),
            solution: task.solution.trim(),
          })),
        })),
      })

      setCreated(result)
      detachJob()
    } catch (error: unknown) {
      setErrors([
        error instanceof Error
          ? error.message
          : 'Не удалось опубликовать. Подождите минуту и нажмите «Опубликовать» ещё раз.',
      ])
    } finally {
      setPublishing(false)
    }
  }

  function handleReset() {
    setTitle('')
    setClassesRaw('')
    // Предмет и класс оставляем: учитель обычно делает несколько работ подряд.
    setSkills([])
    setVariants([])
    setAiRaw('')
    setAiErrors([])
    setAiLoaded('')
    setErrors([])
    setCreated(null)
    setTopic('')
    setGenError('')
    setTouched({})
    setSubmitted(false)
    detachJob()
  }

  const checklist = buildChecklist({
    title,
    subject,
    classes,
    longClasses,
    variantsCount,
    skills,
    variants,
    job,
    aiEnabled,
  })
  const blocking = checklist.filter((item) => !item.soft)
  const skillsReady = skills.length > 0 && skills.every((skill) => skill.title.trim() !== '')
  const tableBlank = variants.length === 0 || isBlank(variants)
  const generationDone = job !== null && job.kind === 'test' && job.status !== 'running'
  // Сколько клеток сервер не составил: не справился ИИ или прервал перезапуск.
  const missingCells = job !== null ? job.failed + job.interrupted_cells : 0

  const resultsUrl = created ? `/tests/${created.id}/results` : ''

  // ------------------------------------------------------------------
  // Экран после публикации
  // ------------------------------------------------------------------
  if (created !== null) {
    return (
      <main className="page">
        <h1>Проверочная работа опубликована</h1>
        <p className="lead">
          «{created.title}»: умений {created.skills_count}, вариантов{' '}
          {created.variants_count}, заданий всего {created.tasks_count}.
        </p>

        <section className="card card--success">
          <h2>Ссылки для учеников — своя у каждого класса</h2>
          {created.class_links.map((link) => (
            <CopyLink
              key={link.id}
              label={`Класс ${link.class_name}`}
              url={`${window.location.origin}/t/${link.code}`}
            />
          ))}
          <p className="hint">
            Отправьте каждому классу его ссылку: класс уже задан, ученик вводит только
            фамилию и имя, вариант выдаётся автоматически. Ссылки, приём работ по каждому
            классу и число сдавших — на странице результатов.
          </p>

          <p className="hint">
            Результаты открываются из вашего кабинета — отдельная ссылка больше не нужна,
            доступ есть только у вас и у администратора.
          </p>

          <div className="row">
            <Link className="btn btn--primary" to={resultsUrl}>
              Открыть результаты
            </Link>
            <button type="button" className="btn btn--ghost" onClick={handleReset}>
              Создать ещё одну
            </button>
            <Link className="btn btn--ghost" to="/">
              На главную
            </Link>
          </div>
        </section>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // Основной экран
  // ------------------------------------------------------------------
  return (
    <main className="page page--wide">
      <p>
        <Link className="backlink" to="/">
          ← На главную
        </Link>
      </p>

      <h1>Создать проверочную работу</h1>
      <p className="lead">
        Опишите умения, которые проверяете, — задания по ним составит ИИ или вы сами.
        У каждого ученика будет свой вариант.
      </p>

      {/* ------------------------- 1. Шапка ------------------------- */}
      <section className="card">
        <h2>1. О работе</h2>

        <div className="fields">
          <div className="field">
            <label className="label" htmlFor="test-title">
              Название работы
            </label>
            <input
              id="test-title"
              className={'input' + (shownError('title') ? ' input--invalid' : '')}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              onBlur={() => touch('title')}
              placeholder="Например: Дроби, 5 класс"
              aria-invalid={shownError('title') !== ''}
              aria-describedby="test-title-hint"
            />
            {shownError('title') ? (
              <p className="field-error" id="test-title-hint">
                {shownError('title')}
              </p>
            ) : (
              <p className="hint" id="test-title-hint">
                Так работу увидят ученики. Например: «Дроби, 5 класс».
              </p>
            )}
          </div>

          <div className="field">
            <label className="label" htmlFor="test-subject">
              Предмет
            </label>
            <input
              id="test-subject"
              className={'input' + (shownError('subject') ? ' input--invalid' : '')}
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              onBlur={() => touch('subject')}
              placeholder="Например: алгебра"
              list="subject-options"
              aria-invalid={shownError('subject') !== ''}
              aria-describedby="test-subject-hint"
            />
            {/* Подсказки — но вписать можно любой предмет. */}
            <datalist id="subject-options">
              <option value="алгебра" />
              <option value="геометрия" />
              <option value="математика" />
              <option value="русский язык" />
              <option value="литература" />
              <option value="физика" />
              <option value="химия" />
              <option value="биология" />
              <option value="история" />
              <option value="обществознание" />
              <option value="география" />
              <option value="английский язык" />
              <option value="информатика" />
            </datalist>
            {shownError('subject') ? (
              <p className="field-error" id="test-subject-hint">
                {shownError('subject')}
              </p>
            ) : (
              <p className="hint" id="test-subject-hint">
                Выберите из списка или впишите свой. По предмету считается статистика школы.
              </p>
            )}
          </div>
        </div>

        <div className="fields fields--inline">
          <div className="field">
            <label className="label" htmlFor="test-classes">
              Классы
            </label>
            <input
              id="test-classes"
              className={'input' + (shownError('classes') ? ' input--invalid' : '')}
              value={classesRaw}
              onChange={(event) => setClassesRaw(event.target.value)}
              onBlur={() => touch('classes')}
              placeholder="Например: 5А, 5Б"
              aria-invalid={shownError('classes') !== ''}
              aria-describedby="test-classes-hint"
            />
            {shownError('classes') ? (
              <p className="field-error" id="test-classes-hint">
                {shownError('classes')}
              </p>
            ) : (
              <p className="hint" id="test-classes-hint">
                Через запятую, например: 5А, 5Б. У каждого класса будет своя ссылка.
                {classes.length > 0 && <> Сейчас: {classes.join(', ')}.</>}
              </p>
            )}
          </div>

          <div className="field field--narrow">
            <label className="label" htmlFor="variants-count">
              Вариантов
            </label>
            <input
              id="variants-count"
              className="input"
              type="number"
              min={1}
              max={20}
              value={variantsCount}
              onChange={(event) =>
                changeVariantsCount(Math.min(20, Math.max(1, Number(event.target.value) || 1)))
              }
              aria-describedby="variants-count-hint"
            />
            <p className="hint" id="variants-count-hint">
              от 1 до 20, по очереди
            </p>
          </div>
        </div>

        <label className="check">
          <input
            type="checkbox"
            checked={shuffle}
            onChange={(event) => setShuffle(event.target.checked)}
          />
          <span>
            Перемешивать порядок заданий внутри варианта
            <span className="hint"> — соседям сложнее сверяться</span>
          </span>
        </label>
      </section>

      {/* ------------------------- 2. Умения ------------------------- */}
      <section className="card">
        <h2>2. Какие умения проверяем</h2>
        <p className="muted">
          По каждому умению будет столько заданий в каждом варианте, сколько укажете.
          Именно по умениям потом строится таблица результатов.
        </p>
        <SkillEditor skills={skills} onChange={changeSkills} showErrors={submitted} />
      </section>

      {/* ------------------------- 3. Задания ------------------------- */}
      <section className="card">
        <h2>3. Задания</h2>

        {skillsReady && tableBlank && !jobRunning && (
          <p className="nexthint">
            {aiEnabled
              ? 'Теперь нажмите «Сгенерировать варианты» или заполните задания вручную в таблице ниже.'
              : 'Теперь заполните задания вручную в таблице ниже или получите их у ИИ по промту.'}
          </p>
        )}

        <div className="fields fields--inline">
          <div className="field">
            <label className="label" htmlFor="ai-topic">
              Тема для ИИ
            </label>
            <input
              id="ai-topic"
              className="input"
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="Например: сложение дробей"
            />
            <p className="hint">
              Необязательно, но так задания точнее. Предмет возьмём из шапки:{' '}
              {subject || 'пока не указан'}.
            </p>
          </div>
          <div className="field field--narrow">
            <label className="label" htmlFor="ai-grade">
              Номер класса
            </label>
            <input
              id="ai-grade"
              className="input"
              value={grade}
              onChange={(event) => setGrade(event.target.value)}
              placeholder="5"
            />
          </div>
        </div>

        {aiEnabled && (
          <div className="aigen">
            <div className="row row--tight">
              <button
                id="ai-generate"
                type="button"
                className="btn btn--primary"
                onClick={handleGenerate}
                disabled={starting || jobRunning || aiLeft === 0}
              >
                {jobRunning
                  ? 'Генерация идёт…'
                  : starting
                    ? 'Запускаем…'
                    : 'Сгенерировать варианты'}
              </button>
              <span className="hint">
                {aiLeft > 0
                  ? `Сегодня осталось генераций: ${aiLeft} из ${aiStatus?.daily_limit ?? 0}`
                  : 'Лимит генераций на сегодня исчерпан — воспользуйтесь промтом вручную'}
              </span>
            </div>
            <button
              type="button"
              className="linkbtn"
              onClick={() => setShowManual((value) => !value)}
              aria-expanded={showManual}
            >
              или скопировать промт вручную
            </button>

            {job !== null && job.kind === 'test' && (
              <div className="aiprogress" id="ai-progress" tabIndex={-1} aria-live="polite">
                <div className="progress__bar">
                  <div
                    className="progress__fill"
                    style={{ width: `${job.total ? (job.done * 100) / job.total : 0}%` }}
                  />
                </div>
                <p className="progress__text">
                  {job.status === 'running'
                    ? `Готово ${job.done} из ${job.total} клеток таблицы`
                    : `Генерация закончена: готово ${job.done} из ${job.total} клеток`}
                  {job.status === 'running' && job.generating > 0 && ` · составляется: ${job.generating}`}
                  {job.status === 'running' && job.checking > 0 && ` · проверяется: ${job.checking}`}
                  {job.failed > 0 && ` · не удалось: ${job.failed}`}
                </p>
                {job.status === 'running' && (
                  <p className="hint">
                    Задания появляются в таблице ниже по мере готовности. Можно уйти со
                    страницы — генерация продолжится на сервере. Ничего не публикуется без вас.
                  </p>
                )}
                {job.status !== 'running' && job.interrupted && (
                  <p className="field-error" id="ai-interrupted">
                    Генерация прервалась: сервер перезапускали. Готовые задания сохранены.
                  </p>
                )}
                {job.status !== 'running' && missingCells > 0 && (
                  <div className="row row--tight">
                    <button
                      id={RESUME_ID}
                      type="button"
                      className="btn btn--primary"
                      onClick={() => void handleResume()}
                      disabled={resuming}
                    >
                      {resuming ? 'Запускаем…' : 'Догенерировать недостающее'}
                    </button>
                    <span className="hint">
                      Не {plural(missingCells, 'составлена', 'составлены', 'составлено')}{' '}
                      {missingCells} {plural(missingCells, 'клетка', 'клетки', 'клеток')}. Готовое
                      не изменится.
                    </span>
                  </div>
                )}
                {job.error !== '' && <p className="field-error">{job.error}</p>}
              </div>
            )}

            {genError !== '' && <p className="field-error">{genError}</p>}
          </div>
        )}

        {(!aiEnabled || showManual) && (
        <ol className="steps">
          <li>
            Скопируйте промт и вставьте его в любой чат с ИИ. Промт собран из ваших
            умений и числа вариантов.
            <div className="row row--tight">
              <button type="button" className="btn btn--primary" onClick={handleCopyPrompt}>
                Скопировать промт для ИИ
              </button>
              {promptCopied && <span className="copied">Скопировано</span>}
              <button type="button" className="btn btn--ghost" onClick={handleBuildEmpty}>
                Заполню сам, без ИИ
              </button>
            </div>
            <details className="details">
              <summary>Посмотреть промт</summary>
              <pre className="pre">{prompt}</pre>
            </details>
          </li>

          <li>
            Вставьте ответ ИИ целиком — лишний текст и оформление вокруг JSON уберём сами.
            <textarea
              className="textarea"
              value={aiRaw}
              onChange={(event) => {
                setAiRaw(event.target.value)
                setAiErrors([])
              }}
              placeholder='{ "variants": [ { "variant": 1, "tasks": [ ... ] } ] }'
              spellCheck={false}
              rows={8}
            />
            <div className="row row--tight">
              <button type="button" className="btn btn--primary" onClick={handleLoadFromAi}>
                Загрузить задания
              </button>
            </div>
          </li>
        </ol>
        )}

        {aiLoaded !== '' && <p className="notice">{aiLoaded}</p>}

        {aiErrors.length > 0 && (
          <div className="alert alert--error">
            <h3>Не получилось прочитать ответ ИИ</h3>
            <ul>
              {aiErrors.map((message, index) => (
                <li key={index}>{message}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* ------------------------- 4. Проверка ------------------------- */}
      {variants.length > 0 && skills.length > 0 && (
        <section className="card" id="tasks-section" tabIndex={-1}>
          <h2>4. Проверка заданий</h2>
          {(generationDone || aiLoaded !== '') && !tableBlank && missingCells === 0 ? (
            <p className="nexthint">Проверьте задания и нажмите «Опубликовать».</p>
          ) : (
            <p className="muted">
              Нажмите на клетку, чтобы открыть задание и заполнить его. ИИ ошибается и в
              условиях, и в ответах — проверьте каждое.
            </p>
          )}
          <TaskTable
            skills={skills}
            variants={variants}
            promptFields={promptFields}
            onChange={setVariants}
            aiEnabled={aiEnabled}
            onRegenerate={handleRegenerate}
            showErrors={submitted}
          />
        </section>
      )}

      {/* ------------------------- Что осталось и публикация ------------------------- */}
      <section className="publish" aria-live="polite">
        {checklist.length > 0 ? (
          <div
            className={
              'todo' +
              (blocking.length === 0 ? ' todo--soft' : submitted ? ' todo--alert' : '')
            }
          >
            <h2 className="todo__title">
              {blocking.length > 0
                ? 'Чтобы опубликовать, осталось:'
                : 'Можно публиковать, но сначала посмотрите:'}
            </h2>
            <ul className="todo__list">
              {checklist.map((item) => (
                <li key={item.key}>
                  <button type="button" className="todo__item" onClick={() => goToItem(item)}>
                    {item.text}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="ready">Всё готово, нажмите «Опубликовать».</p>
        )}

        {errors.length > 0 && (
          <div className="alert alert--error">
            <h3>Не получилось опубликовать</h3>
            <ul>
              {errors.map((message, index) => (
                <li key={index}>{message}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="row row--actions">
          <button
            type="button"
            className="btn btn--primary btn--wide"
            onClick={handlePublish}
            disabled={publishing}
          >
            {publishing ? 'Публикуем…' : 'Опубликовать'}
          </button>
          <span className="hint">После публикации вы получите ссылку для учеников.</span>
        </div>
      </section>
    </main>
  )
}
