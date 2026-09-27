/**
 * Страница «Создать контрольную».
 *
 * Порядок работы учителя:
 *   1. шапка: название, своё имя, классы, число вариантов;
 *   2. список умений — что именно проверяем;
 *   3. промт для ИИ собирается из умений, числа вариантов, предмета и класса;
 *      ответ ИИ вставляется в поле и разбирается (можно и не пользоваться ИИ);
 *   4. экран проверки: таблица «варианты × умения», правка любого задания,
 *      замена отдельного задания через ИИ;
 *   5. «Опубликовать» — когда все задания заполнены.
 */

import { useState } from 'react'
import { Link } from 'react-router'
import { createTest } from '../api'
import CopyLink from '../components/CopyLink'
import SkillEditor from '../components/SkillEditor'
import TaskTable from '../components/TaskTable'
import { buildTestPrompt } from '../lib/aiPrompt'
import { emptyTask, parseTestJson, validateTest } from '../lib/parseTestJson'
import { rememberTest } from '../lib/myTestsStorage'
import type { CreatedTest, SkillDraft, VariantDraft } from '../types'

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
  // --- Шапка ---
  const [teacherName, setTeacherName] = useState('')
  const [title, setTitle] = useState('')
  const [classesRaw, setClassesRaw] = useState('')
  const [variantsCount, setVariantsCount] = useState(2)
  const [shuffle, setShuffle] = useState(true)

  // --- Умения ---
  const [skills, setSkills] = useState<SkillDraft[]>([])

  // --- Промт и ответ ИИ ---
  const [subject, setSubject] = useState('')
  const [grade, setGrade] = useState('')
  const [aiRaw, setAiRaw] = useState('')
  const [aiErrors, setAiErrors] = useState<string[]>([])
  const [aiLoaded, setAiLoaded] = useState('')
  const [promptCopied, setPromptCopied] = useState(false)

  // --- Задания ---
  const [variants, setVariants] = useState<VariantDraft[]>([])

  // --- Публикация ---
  const [errors, setErrors] = useState<string[]>([])
  const [publishing, setPublishing] = useState(false)
  const [created, setCreated] = useState<CreatedTest | null>(null)

  const classes = parseClasses(classesRaw)
  const promptFields = { subject, grade }
  const prompt = buildTestPrompt(promptFields, skills, variantsCount)

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

    setVariants(result.variants)
    setAiErrors([])
    const tasksTotal = result.variants.reduce((sum, variant) => sum + variant.tasks.length, 0)
    setAiLoaded(
      `Загружено вариантов: ${result.variants.length}, заданий: ${tasksTotal}. ` +
        'Проверьте их в таблице ниже.',
    )
  }

  /** Создаёт пустую таблицу — для тех, кто заполняет всё руками. */
  function handleBuildEmpty() {
    if (skills.length === 0) {
      setErrors(['Сначала добавьте умения.'])
      return
    }
    setVariants(buildEmptyVariants(skills, variantsCount))
    setErrors([])
    setAiLoaded('')
  }

  function validateAll(): string[] {
    const found: string[] = []

    if (teacherName.trim() === '') {
      found.push('Укажите своё имя — оно будет видно ученикам.')
    }
    if (title.trim() === '') {
      found.push('Укажите название контрольной.')
    }
    if (classes.length === 0) {
      found.push('Укажите хотя бы один класс, например: 8А, 8Б.')
    }
    found.push(...validateTest(skills, variantsCount, variants))

    return found
  }

  async function handlePublish() {
    const found = validateAll()
    if (found.length > 0) {
      setErrors(found)
      return
    }

    setErrors([])
    setPublishing(true)

    try {
      const result = await createTest({
        title: title.trim(),
        teacher_name: teacherName.trim(),
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
      rememberTest({
        code: result.code,
        resultsToken: result.results_token,
        title: result.title,
        questionsCount: result.tasks_count,
        createdAt: new Date().toISOString(),
      })
    } catch (error: unknown) {
      setErrors([error instanceof Error ? error.message : 'Не удалось опубликовать'])
    } finally {
      setPublishing(false)
    }
  }

  function handleReset() {
    setTitle('')
    setClassesRaw('')
    setSkills([])
    setVariants([])
    setAiRaw('')
    setAiErrors([])
    setAiLoaded('')
    setErrors([])
    setCreated(null)
    // Имя учителя, предмет и класс оставляем: контрольные создают подряд.
  }

  const studentUrl = created ? `${window.location.origin}/t/${created.code}` : ''
  const resultsUrl = created ? `${window.location.origin}/r/${created.results_token}` : ''

  // ------------------------------------------------------------------
  // Экран после публикации
  // ------------------------------------------------------------------
  if (created !== null) {
    return (
      <main className="page">
        <h1>Контрольная опубликована</h1>
        <p className="lead">
          «{created.title}»: умений {created.skills_count}, вариантов{' '}
          {created.variants_count}, заданий всего {created.tasks_count}. Код:{' '}
          <code>{created.code}</code>
        </p>

        <section className="card card--success">
          <CopyLink
            label="Для учеников"
            url={studentUrl}
            hint="Эту ссылку отправьте классу — вариант выдаётся каждому автоматически."
          />
          <CopyLink
            secret
            label="Результаты — только для вас, не отправляйте ученикам"
            url={resultsUrl}
            hint={
              'По этой ссылке видны все работы, правильные ответы и решения. ' +
              'Она сохранена в списке «Мои контрольные» на главной странице этого браузера.'
            }
          />

          <div className="row">
            <a className="btn btn--primary" href={resultsUrl}>
              Открыть результаты
            </a>
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

      <h1>Создать контрольную</h1>
      <p className="lead">
        Опишите умения, которые проверяете, — задания по ним составит ИИ или вы сами.
        У каждого ученика будет свой вариант.
      </p>

      {/* ------------------------- 1. Шапка ------------------------- */}
      <section className="card">
        <h2>1. О контрольной</h2>

        <div className="fields">
          <div className="field">
            <label className="label" htmlFor="teacher-name">
              Ваше имя
            </label>
            <input
              id="teacher-name"
              className="input"
              value={teacherName}
              onChange={(event) => setTeacherName(event.target.value)}
              placeholder="Иванова Анна Петровна"
              autoComplete="name"
            />
          </div>

          <div className="field">
            <label className="label" htmlFor="test-title">
              Название контрольной
            </label>
            <input
              id="test-title"
              className="input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Квадратные уравнения. Контрольная №2"
            />
          </div>
        </div>

        <div className="fields fields--inline">
          <div className="field">
            <label className="label" htmlFor="test-classes">
              Классы
            </label>
            <input
              id="test-classes"
              className="input"
              value={classesRaw}
              onChange={(event) => setClassesRaw(event.target.value)}
              placeholder="8А, 8Б"
            />
            <p className="hint">
              Через запятую. Ученик выберет свой класс из этого списка.
              {classes.length > 0 && <> Сейчас: {classes.join(', ')}.</>}
            </p>
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
                setVariantsCount(Math.min(20, Math.max(1, Number(event.target.value) || 1)))
              }
            />
            <p className="hint">выдаются по кругу</p>
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
        <SkillEditor skills={skills} onChange={setSkills} />
      </section>

      {/* ------------------------- 3. Задания ------------------------- */}
      <section className="card">
        <h2>3. Задания</h2>

        <div className="fields fields--inline">
          <div className="field">
            <label className="label" htmlFor="ai-subject">
              Предмет и тема
            </label>
            <input
              id="ai-subject"
              className="input"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder="Алгебра, квадратные уравнения"
            />
          </div>
          <div className="field field--narrow">
            <label className="label" htmlFor="ai-grade">
              Класс
            </label>
            <input
              id="ai-grade"
              className="input"
              value={grade}
              onChange={(event) => setGrade(event.target.value)}
              placeholder="8"
            />
          </div>
        </div>

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
        <section className="card">
          <h2>4. Проверка заданий</h2>
          <p className="muted">
            Проверьте каждое задание: ИИ ошибается и в условиях, и в ответах. Любое
            можно поправить руками или заменить новым.
          </p>
          <TaskTable
            skills={skills}
            variants={variants}
            promptFields={promptFields}
            onChange={setVariants}
          />
        </section>
      )}

      {/* ------------------------- Ошибки и публикация ------------------------- */}
      {errors.length > 0 && (
        <section className="alert alert--error">
          <h2>Так публиковать нельзя</h2>
          <ul>
            {errors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </section>
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
    </main>
  )
}
