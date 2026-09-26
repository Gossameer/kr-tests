/**
 * Страница «Создать контрольную».
 *
 * Порядок работы учителя:
 *   1. заполняет своё имя, название, классы, решает — перемешивать ли вопросы;
 *   2. набирает вопросы вручную ИЛИ приносит их из ИИ (вкладки);
 *   3. смотрит превью с подсвеченными верными ответами;
 *   4. публикует и получает две ссылки: ученикам и себе на результаты.
 *
 * Знать формат JSON учителю не нужно: вкладка «Из ИИ» даёт готовый промт,
 * а загруженные вопросы попадают в тот же редактор, что и при наборе руками.
 */

import { useState } from 'react'
import { Link } from 'react-router'
import { createTest } from '../api'
import CopyLink from '../components/CopyLink'
import QuestionEditor from '../components/QuestionEditor'
import { buildAiPrompt } from '../lib/aiPrompt'
import { parseTestJson, validateQuestions } from '../lib/parseTestJson'
import { rememberTest } from '../lib/myTestsStorage'
import type { CreatedTest, DraftQuestion } from '../types'

/** Какая вкладка наполнения вопросов открыта. */
type Tab = 'manual' | 'ai'

/** Разбирает «6А, 6Б 6В» в ['6А','6Б','6В'] — учителю удобно писать как привычно. */
function parseClasses(raw: string): string[] {
  return raw
    .split(/[,;\n]+/)
    .flatMap((part) => part.split(/\s+/))
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

export default function CreateTestPage() {
  // --- Шапка контрольной ---
  const [teacherName, setTeacherName] = useState('')
  const [title, setTitle] = useState('')
  const [classesRaw, setClassesRaw] = useState('')
  const [shuffle, setShuffle] = useState(true)

  // --- Вопросы ---
  const [tab, setTab] = useState<Tab>('manual')
  const [questions, setQuestions] = useState<DraftQuestion[]>([])

  // --- Вкладка «Из ИИ» ---
  // Три поля, которые подставляются в промт вместо [ТЕМА], [КЛАСС], [СКОЛЬКО].
  const [aiTopic, setAiTopic] = useState('')
  const [aiGrade, setAiGrade] = useState('')
  const [aiCount, setAiCount] = useState('10')
  const [aiRaw, setAiRaw] = useState('')
  const [aiErrors, setAiErrors] = useState<string[]>([])
  const [aiLoaded, setAiLoaded] = useState('')
  const [promptCopied, setPromptCopied] = useState(false)

  // --- Публикация ---
  const [errors, setErrors] = useState<string[]>([])
  const [publishing, setPublishing] = useState(false)
  const [created, setCreated] = useState<CreatedTest | null>(null)

  const classes = parseClasses(classesRaw)

  // Промт пересобирается на каждый ввод — учитель видит ровно то, что скопирует.
  const prompt = buildAiPrompt({ topic: aiTopic, grade: aiGrade, count: aiCount })

  async function handleCopyPrompt() {
    try {
      await navigator.clipboard.writeText(prompt)
      setPromptCopied(true)
      window.setTimeout(() => setPromptCopied(false), 2000)
    } catch {
      setAiErrors(['Браузер не дал скопировать — выделите текст промта и скопируйте вручную.'])
    }
  }

  /** «Загрузить» на вкладке «Из ИИ»: разбираем ответ и переносим вопросы в редактор. */
  function handleLoadFromAi() {
    const result = parseTestJson(aiRaw)

    if (!result.ok) {
      setAiErrors(result.errors)
      setAiLoaded('')
      return
    }

    setQuestions(result.questions)
    setAiErrors([])
    // Название берём из ответа ИИ, только если учитель его ещё не вписал сам.
    if (result.title !== null && title.trim() === '') {
      setTitle(result.title)
    }
    setAiLoaded(
      `Загружено вопросов: ${result.questions.length}. Проверьте их на вкладке «Вручную».`,
    )
    setTab('manual')
  }

  /** Проверяет всю форму целиком. Возвращает список ошибок. */
  function validateForm(): string[] {
    const found: string[] = []

    if (teacherName.trim() === '') {
      found.push('Укажите своё имя — оно будет видно ученикам.')
    }
    if (title.trim() === '') {
      found.push('Укажите название контрольной.')
    }
    if (classes.length === 0) {
      found.push('Укажите хотя бы один класс, например: 6А, 6Б.')
    }
    found.push(...validateQuestions(questions))

    return found
  }

  async function handlePublish() {
    const found = validateForm()
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
        shuffle,
        // Пробелы по краям убираем здесь, чтобы в базу не попало « 56 ».
        questions: questions.map((question) => ({
          text: question.text.trim(),
          options: question.options.map((option) => option.trim()),
          correct: question.correct,
        })),
      })

      setCreated(result)
      // Запоминаем контрольную в этом браузере, чтобы ссылки не потерялись.
      rememberTest({
        code: result.code,
        resultsToken: result.results_token,
        title: result.title,
        questionsCount: result.questions_count,
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
    setQuestions([])
    setAiTopic('')
    setAiGrade('')
    setAiCount('10')
    setAiRaw('')
    setAiErrors([])
    setAiLoaded('')
    setErrors([])
    setCreated(null)
    setTab('manual')
    // Имя учителя намеренно оставляем: он создаёт контрольные подряд.
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
          «{created.title}», вопросов: {created.questions_count}. Код: <code>{created.code}</code>
        </p>

        <section className="card card--success">
          <CopyLink
            label="Для учеников"
            url={studentUrl}
            hint="Эту ссылку отправьте классу — по ней открывается сам тест."
          />

          <CopyLink
            secret
            label="Результаты — только для вас, не отправляйте ученикам"
            url={resultsUrl}
            hint={
              'По этой ссылке видны все работы и правильные ответы. ' +
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
  // Основной экран создания
  // ------------------------------------------------------------------
  return (
    <main className="page">
      <p>
        <Link className="backlink" to="/">
          ← На главную
        </Link>
      </p>

      <h1>Создать контрольную</h1>
      <p className="lead">
        Заполните шапку, наберите вопросы вручную или принесите их из ИИ, проверьте
        правильные ответы и опубликуйте.
      </p>

      {/* ------------------------- Шапка ------------------------- */}
      <section className="card">
        <h2>О контрольной</h2>

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
            <p className="hint">Ученики увидят это имя на странице теста.</p>
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
              placeholder="Дроби. Контрольная №2"
            />
          </div>

          <div className="field">
            <label className="label" htmlFor="test-classes">
              Классы
            </label>
            <input
              id="test-classes"
              className="input"
              value={classesRaw}
              onChange={(event) => setClassesRaw(event.target.value)}
              placeholder="6А, 6Б"
            />
            <p className="hint">
              Через запятую. Ученик выберет свой класс из этого списка.
              {classes.length > 0 && <> Сейчас: {classes.join(', ')}.</>}
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
            Перемешивать вопросы и варианты
            <span className="hint"> — у каждого ученика свой порядок, списать сложнее</span>
          </span>
        </label>
      </section>

      {/* ------------------------- Вопросы ------------------------- */}
      <section className="card">
        <h2>Вопросы</h2>

        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'manual'}
            className={tab === 'manual' ? 'tab tab--active' : 'tab'}
            onClick={() => setTab('manual')}
          >
            Вручную
            {questions.length > 0 && <span className="tab__badge">{questions.length}</span>}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'ai'}
            className={tab === 'ai' ? 'tab tab--active' : 'tab'}
            onClick={() => setTab('ai')}
          >
            Из ИИ
          </button>
        </div>

        {tab === 'manual' && (
          <>
            {aiLoaded !== '' && <p className="notice">{aiLoaded}</p>}
            <QuestionEditor questions={questions} onChange={setQuestions} />
          </>
        )}

        {tab === 'ai' && (
          <div className="aitab">
            <ol className="steps">
              <li>
                Заполните три поля — они подставятся в промт автоматически.
                <div className="fields fields--inline">
                  <div className="field">
                    <label className="label" htmlFor="ai-topic">
                      Тема
                    </label>
                    <input
                      id="ai-topic"
                      className="input"
                      value={aiTopic}
                      onChange={(event) => setAiTopic(event.target.value)}
                      placeholder="Дроби и проценты"
                    />
                  </div>

                  <div className="field field--narrow">
                    <label className="label" htmlFor="ai-grade">
                      Класс
                    </label>
                    <input
                      id="ai-grade"
                      className="input"
                      value={aiGrade}
                      onChange={(event) => setAiGrade(event.target.value)}
                      placeholder="6"
                    />
                  </div>

                  <div className="field field--narrow">
                    <label className="label" htmlFor="ai-count">
                      Вопросов
                    </label>
                    <input
                      id="ai-count"
                      className="input"
                      type="number"
                      min={1}
                      max={100}
                      value={aiCount}
                      onChange={(event) => setAiCount(event.target.value)}
                    />
                  </div>
                </div>
                <p className="hint">
                  Незаполненное поле останется в промте подсказкой в квадратных скобках —
                  её можно дописать прямо в чате с ИИ.
                </p>

                <div className="row">
                  <button type="button" className="btn btn--primary" onClick={handleCopyPrompt}>
                    Скопировать промт для ИИ
                  </button>
                  {promptCopied && <span className="copied">Скопировано</span>}
                </div>
                <details className="details">
                  <summary>Посмотреть промт</summary>
                  <pre className="pre">{prompt}</pre>
                </details>
              </li>
              <li>
                Скопируйте промт и вставьте его в любой чат с ИИ.
              </li>
              <li>
                Вставьте сюда ответ ИИ целиком — лишний текст и оформление вокруг JSON
                мы уберём сами.
                <textarea
                  className="textarea"
                  value={aiRaw}
                  onChange={(event) => {
                    setAiRaw(event.target.value)
                    setAiErrors([])
                  }}
                  placeholder='{ "title": "...", "questions": [ ... ] }'
                  spellCheck={false}
                  rows={10}
                />
                <div className="row">
                  <button type="button" className="btn btn--primary" onClick={handleLoadFromAi}>
                    Загрузить
                  </button>
                  <span className="hint">Вопросы попадут в редактор на вкладке «Вручную».</span>
                </div>
              </li>
            </ol>

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
          </div>
        )}
      </section>

      {/* ------------------------- Превью ------------------------- */}
      {questions.length > 0 && (
        <section className="card">
          <h2>Превью</h2>
          <p className="muted">
            Так контрольную увидит ученик{shuffle ? ' (порядок у него будет другим)' : ''}.
            Правильный ответ подсвечен зелёным — проверьте каждый.
          </p>

          <ol className="questions">
            {questions.map((question, questionIndex) => (
              <li key={questionIndex} className="question">
                <p className="question__text">
                  {questionIndex + 1}. {question.text || <i>без текста</i>}
                </p>
                <ul className="options">
                  {question.options.map((option, optionIndex) => {
                    const isCorrect = optionIndex === question.correct
                    return (
                      <li
                        key={optionIndex}
                        className={isCorrect ? 'option option--correct' : 'option'}
                      >
                        <span className="option__letter">
                          {'АБВГДЕЖЗИК'[optionIndex] ?? optionIndex + 1}
                        </span>
                        <span className="option__text">{option || <i>пусто</i>}</span>
                        {isCorrect && <span className="option__mark">✓ верный</span>}
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ol>
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
