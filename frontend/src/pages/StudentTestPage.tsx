/**
 * Страница ученика: /t/:code
 *
 * Три экрана, которые сменяют друг друга:
 *   1. «start»   — название контрольной, ФИО и выбор класса из списка, «Начать»;
 *   2. «solving» — все вопросы списком, прогресс «отвечено X из Y», «Сдать работу»;
 *   3. «done»    — результат «Верно X из Y» и список вопросов с ✓/✗.
 *
 * Проверка ответов идёт ТОЛЬКО на сервере: сюда не приходит информация о том,
 * какой вариант правильный. Порядок вопросов и вариантов может быть перемешан —
 * на сервер уходят id, а не номера на экране, поэтому порядок ни на что не влияет.
 */

import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { NETWORK_ERROR, SERVER_ERROR, fetchPublicTest, submitAttempt } from '../api'
import { loadAttempt, saveAttempt } from '../lib/attemptStorage'
import { getOrCreateSeed, shuffleWithSeed } from '../lib/shuffle'
import type { PublicQuestion, PublicTest, StoredAttempt } from '../types'

/** Экран, который показываем прямо сейчас. */
type Phase = 'start' | 'solving' | 'done'

/** Состояние загрузки самой контрольной. */
type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; test: PublicTest }
  | { kind: 'error'; message: string }

export default function StudentTestPage() {
  // Код контрольной берётся прямо из адреса: /t/ahyg5gnk
  const { code = '' } = useParams()

  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })

  // Сданная работа: либо найденная в localStorage, либо только что отправленная.
  // Функция в useState выполняется ОДИН раз при первом показе страницы — читаем
  // localStorage сразу здесь, а не в эффекте, чтобы не вызывать лишнюю перерисовку.
  const [stored, setStored] = useState<StoredAttempt | null>(() => loadAttempt(code))

  // Если работа уже сдана с этого устройства, сразу открываем экран результата.
  const [phase, setPhase] = useState<Phase>(stored !== null ? 'done' : 'start')

  const [studentName, setStudentName] = useState('')
  const [studentClass, setStudentClass] = useState('')
  const [formError, setFormError] = useState('')

  // Выбранные варианты: {id вопроса: id варианта}.
  const [chosen, setChosen] = useState<Record<number, number>>({})

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  // Загружаем контрольную с сервера. Название и вопросы нужны и на экране «Начать».
  useEffect(() => {
    fetchPublicTest(code)
      .then((test) => setLoading({ kind: 'ready', test }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить контрольную',
        }),
      )
  }, [code])

  /**
   * Порядок вопросов и вариантов для ЭТОГО ученика.
   *
   * useMemo пересчитывает список только при смене теста, а не на каждую
   * перерисовку: иначе порядок менялся бы после каждого выбора варианта.
   */
  const questions: PublicQuestion[] = useMemo(() => {
    if (loading.kind !== 'ready') {
      return []
    }
    if (!loading.test.shuffle) {
      return loading.test.questions
    }

    const seed = getOrCreateSeed(code)
    // Вариантам даём соседние seed'ы, чтобы их порядок тоже был свой у каждого.
    return shuffleWithSeed(loading.test.questions, seed).map((question, index) => ({
      ...question,
      options: shuffleWithSeed(question.options, seed + index + 1),
    }))
  }, [loading, code])

  function handleStart() {
    if (!studentName.trim()) {
      setFormError('Введите фамилию и имя.')
      return
    }
    if (!studentClass.trim()) {
      setFormError('Выберите класс.')
      return
    }

    setFormError('')
    setPhase('solving')
  }

  function handleChoose(questionId: number, optionId: number) {
    // Копируем объект, а не меняем существующий: React замечает только новые значения.
    setChosen((previous) => ({ ...previous, [questionId]: optionId }))
  }

  async function handleSubmit() {
    if (loading.kind !== 'ready') {
      return
    }

    const total = questions.length
    const answered = Object.keys(chosen).length

    // Подтверждение: видно, сколько вопросов осталось без ответа.
    const confirmed = window.confirm(`Вы ответили на ${answered} из ${total}. Сдать?`)
    if (!confirmed) {
      return
    }

    setSubmitting(true)
    setSubmitError('')

    try {
      const result = await submitAttempt(code, {
        student_name: studentName.trim(),
        student_class: studentClass.trim(),
        answers: chosen,
      })

      const attempt: StoredAttempt = {
        savedAt: new Date().toISOString(),
        code,
        title: loading.test.title,
        result,
        // Тексты вопросов кладём рядом с результатом, чтобы экран результата
        // работал и без повторного запроса к серверу.
        questions: questions.map((question) => ({
          id: question.id,
          text: question.text,
          position: question.position,
        })),
      }

      saveAttempt(code, attempt)
      setStored(attempt)
      setPhase('done')
    } catch (error: unknown) {
      setSubmitError(error instanceof Error ? error.message : 'Не удалось сдать работу')
    } finally {
      setSubmitting(false)
    }
  }

  // ------------------------------------------------------------------
  // Экран 3: результат. Показывается первым, если работа уже сдана.
  // ------------------------------------------------------------------
  if (phase === 'done' && stored !== null) {
    const { result } = stored
    // Тексты вопросов ищем по id — порядок на сервере и на экране может различаться.
    const textById = new Map(stored.questions.map((question) => [question.id, question.text]))

    return (
      <main className="page page--student">
        <h1>{stored.title}</h1>
        <p className="lead">
          {result.student_name}, {result.student_class}
        </p>

        <section className="card card--success">
          <p className="score">
            Верно {result.score} из {result.max_score}
          </p>
        </section>

        <ol className="questions">
          {result.results.map((item) => (
            <li key={item.question_id} className="question">
              <p className="question__text">
                <span className={item.is_correct ? 'mark mark--ok' : 'mark mark--bad'}>
                  {item.is_correct ? '✓' : '✗'}
                </span>{' '}
                {textById.get(item.question_id) ?? `Вопрос ${item.position}`}
              </p>
              {!item.answered && <p className="muted">Ответ не выбран</p>}
            </li>
          ))}
        </ol>

        <p className="muted">
          Работа сдана и сохранена. Повторно пройти эту контрольную нельзя — если нужна
          пересдача, обратитесь к учителю.
        </p>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // Загрузка и ошибки
  // ------------------------------------------------------------------
  if (loading.kind === 'loading') {
    return (
      <main className="page page--student">
        <p className="loading">Загружаем контрольную…</p>
      </main>
    )
  }

  if (loading.kind === 'error') {
    // При сетевой ошибке и поломке сервера ссылка ни при чём — совет её проверить
    // только запутает. Показываем его лишь тогда, когда дело может быть в ссылке.
    const isTechnical =
      loading.message === NETWORK_ERROR || loading.message === SERVER_ERROR

    return (
      <main className="page page--student">
        <h1>Контрольная не открылась</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
        {!isTechnical && (
          <p className="muted">Проверьте ссылку — её должен дать учитель.</p>
        )}
      </main>
    )
  }

  const test = loading.test

  // ------------------------------------------------------------------
  // Приём работ закрыт
  // ------------------------------------------------------------------
  if (!test.is_open) {
    return (
      <main className="page page--student">
        <h1>{test.title}</h1>
        <section className="card">
          <h2>Приём работ закрыт</h2>
          <p>Учитель больше не принимает ответы по этой контрольной.</p>
          <p className="muted">
            Если вы должны были её сдать, подойдите к учителю: {test.teacher_name}.
          </p>
        </section>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // У контрольной не указаны классы
  //
  // Так выглядят контрольные, созданные до обновления сервиса: выбрать класс
  // не из чего, а без класса работа не сдаётся. Показываем понятное объяснение
  // вместо тупика «Выберите класс» при пустом списке.
  // ------------------------------------------------------------------
  if (test.classes.length === 0) {
    return (
      <main className="page page--student">
        <h1>{test.title}</h1>
        <section className="card">
          <h2>Эту контрольную пока нельзя пройти</h2>
          <p>В ней не указаны классы, поэтому отметить свой класс не получится.</p>
          <p className="muted">
            Сообщите учителю ({test.teacher_name}) — контрольную нужно создать заново,
            указав классы.
          </p>
        </section>
      </main>
    )
  }

  const total = questions.length
  const answered = Object.keys(chosen).length

  // ------------------------------------------------------------------
  // Экран 1: имя и класс
  // ------------------------------------------------------------------
  if (phase === 'start') {
    return (
      <main className="page page--student">
        <h1>{test.title}</h1>
        <p className="lead">
          Вопросов: {total}. Учитель: {test.teacher_name}.
        </p>

        <section className="card">
          <label className="label" htmlFor="student-name">
            Фамилия и имя
          </label>
          <input
            id="student-name"
            className="input"
            value={studentName}
            onChange={(event) => setStudentName(event.target.value)}
            placeholder="Иванов Иван"
            autoComplete="name"
          />

          <label className="label label--spaced" htmlFor="student-class">
            Класс
          </label>
          {/* Список задаёт учитель — ученик не может написать класс с ошибкой. */}
          <select
            id="student-class"
            className="input select"
            value={studentClass}
            onChange={(event) => setStudentClass(event.target.value)}
          >
            <option value="">— выберите класс —</option>
            {test.classes.map((className) => (
              <option key={className} value={className}>
                {className}
              </option>
            ))}
          </select>

          {formError !== '' && <p className="field-error">{formError}</p>}

          <div className="row">
            <button type="button" className="btn btn--primary btn--wide" onClick={handleStart}>
              Начать
            </button>
          </div>
        </section>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // Экран 2: вопросы
  // ------------------------------------------------------------------
  return (
    <main className="page page--student">
      <h1>{test.title}</h1>
      <p className="lead">
        {studentName}, {studentClass}
      </p>

      {/* Прогресс виден всё время: сколько вопросов уже отвечено. */}
      <div className="progress" role="status">
        <div className="progress__bar" aria-hidden="true">
          <div
            className="progress__fill"
            style={{ width: total === 0 ? '0%' : `${(answered * 100) / total}%` }}
          />
        </div>
        <p className="progress__text">
          Отвечено {answered} из {total}
        </p>
      </div>

      <ol className="questions">
        {questions.map((question, questionIndex) => (
          <li key={question.id} className="question">
            <p className="question__text">
              {questionIndex + 1}. {question.text}
            </p>
            <ul className="options">
              {question.options.map((option) => (
                <li key={option.id}>
                  {/* Вся строка — это <label>: нажать можно куда угодно,
                      это важно для телефона, где попасть в кружок трудно. */}
                  <label
                    className={
                      chosen[question.id] === option.id ? 'choice choice--picked' : 'choice'
                    }
                  >
                    <input
                      type="radio"
                      name={`question-${question.id}`}
                      value={option.id}
                      checked={chosen[question.id] === option.id}
                      onChange={() => handleChoose(question.id, option.id)}
                    />
                    <span>{option.text}</span>
                  </label>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>

      {submitError !== '' && (
        <section className="alert alert--error">
          <p>{submitError}</p>
        </section>
      )}

      <div className="row row--actions">
        <button
          type="button"
          className="btn btn--primary btn--wide"
          onClick={handleSubmit}
          disabled={submitting}
        >
          {submitting ? 'Отправляем…' : 'Сдать работу'}
        </button>
      </div>

      <p className="muted">
        Можно оставить вопрос без ответа — он будет считаться неверным.
      </p>
    </main>
  )
}
