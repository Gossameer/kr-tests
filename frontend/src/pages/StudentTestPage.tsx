/**
 * Страница ученика: /t/:code
 *
 * Три экрана, которые сменяют друг друга:
 *   1. «start»   — название контрольной, поля «Фамилия Имя» и «Класс», кнопка «Начать»;
 *   2. «solving» — все вопросы списком с радиокнопками и кнопкой «Сдать работу»;
 *   3. «done»    — результат «Верно X из Y» и список вопросов с ✓/✗.
 *
 * Проверка ответов идёт ТОЛЬКО на сервере: сюда не приходит информация
 * о том, какой вариант правильный, поэтому подсмотреть ответы в браузере нельзя.
 */

import { useEffect, useState } from 'react'
import { fetchPublicTest, submitAttempt } from '../api'
import { loadAttempt, saveAttempt } from '../lib/attemptStorage'
import type { PublicTest, StoredAttempt } from '../types'
import { useParams } from 'react-router'

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

  function handleStart() {
    if (!studentName.trim()) {
      setFormError('Введите фамилию и имя.')
      return
    }
    if (!studentClass.trim()) {
      setFormError('Введите класс.')
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

    const total = loading.test.questions.length
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
        questions: loading.test.questions.map((question) => ({
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
    // Тексты вопросов ищем по id: порядок в результате и в списке вопросов совпадает,
    // но по id надёжнее.
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
          Работа сдана и сохранена. Повторно пройти эту контрольную с этого устройства нельзя.
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
        <p>Загружаем контрольную…</p>
      </main>
    )
  }

  if (loading.kind === 'error') {
    return (
      <main className="page page--student">
        <h1>Контрольная не открылась</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
        <p className="muted">Проверьте ссылку — её должен дать учитель.</p>
      </main>
    )
  }

  const test = loading.test
  const total = test.questions.length
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
          <input
            id="student-class"
            className="input"
            value={studentClass}
            onChange={(event) => setStudentClass(event.target.value)}
            placeholder="6А"
          />

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
        {studentName}, {studentClass}. Отвечено: {answered} из {total}.
      </p>

      <ol className="questions">
        {test.questions.map((question, questionIndex) => (
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

      <div className="row">
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
