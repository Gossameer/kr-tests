/**
 * Страница ученика: /t/:code
 *
 * Три экрана:
 *   1. «start»   — название, ФИО и выбор класса, кнопка «Начать»;
 *   2. «solving» — задания выданного варианта, прогресс «отвечено X из Y»;
 *   3. «done»    — «Верно X из Y» и ✓/✗ по заданиям.
 *
 * Вариант выдаёт сервер при нажатии «Начать» — до этого заданий здесь нет.
 * Правильные ответы сюда не приходят: проверка целиком серверная.
 */

import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router'
import {
  NETWORK_ERROR,
  SERVER_ERROR,
  fetchPublicTest,
  startAttempt,
  submitAttempt,
} from '../api'
import {
  clearProgress,
  loadAttempt,
  loadProgress,
  saveAttempt,
  saveProgress,
} from '../lib/attemptStorage'
import { getOrCreateSeed, shuffleWithSeed } from '../lib/shuffle'
import type { PublicTask, PublicTestInfo, StartedAttempt, StoredAttempt } from '../types'

type Phase = 'start' | 'solving' | 'done'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; info: PublicTestInfo }
  | { kind: 'error'; message: string }

export default function StudentTestPage() {
  const { code = '' } = useParams()

  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })

  // Сданная работа: читаем localStorage сразу при первом показе страницы.
  const [stored, setStored] = useState<StoredAttempt | null>(() => loadAttempt(code))
  const [phase, setPhase] = useState<Phase>(stored !== null ? 'done' : 'start')

  // Начатая работа: вариант и задания приходят с сервера.
  const [attempt, setAttempt] = useState<StartedAttempt | null>(null)

  const [studentName, setStudentName] = useState(() => loadProgress(code)?.studentName ?? '')
  const [studentClass, setStudentClass] = useState(
    () => loadProgress(code)?.studentClass ?? '',
  )
  const [formError, setFormError] = useState('')
  const [starting, setStarting] = useState(false)

  // Ответы: выбранные варианты и введённый текст.
  const [choices, setChoices] = useState<Record<number, number>>({})
  const [inputs, setInputs] = useState<Record<number, string>>({})

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  useEffect(() => {
    fetchPublicTest(code)
      .then((info) => setLoading({ kind: 'ready', info }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить контрольную',
        }),
      )
  }, [code])

  /**
   * Порядок заданий для этого ученика.
   * useMemo — чтобы порядок не менялся на каждую перерисовку, а seed из
   * localStorage делает его одинаковым и после перезагрузки страницы.
   */
  const tasks: PublicTask[] = useMemo(() => {
    if (attempt === null) {
      return []
    }
    if (!attempt.shuffle) {
      return attempt.tasks
    }

    const seed = getOrCreateSeed(code)
    return shuffleWithSeed(attempt.tasks, seed).map((task, index) => ({
      ...task,
      options: shuffleWithSeed(task.options, seed + index + 1),
    }))
  }, [attempt, code])

  async function handleStart() {
    if (!studentName.trim()) {
      setFormError('Введите фамилию и имя.')
      return
    }
    if (!studentClass.trim()) {
      setFormError('Выберите класс.')
      return
    }

    setFormError('')
    setStarting(true)

    try {
      const started = await startAttempt(code, studentName.trim(), studentClass.trim())
      setAttempt(started)
      // Запоминаем попытку: после перезагрузки вернёмся в тот же вариант.
      saveProgress(code, {
        code,
        attemptId: started.attempt_id,
        attemptToken: started.attempt_token,
        variantNo: started.variant_no,
        studentName: started.student_name,
        studentClass: started.student_class,
      })
      setPhase('solving')
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Не удалось начать работу')
    } finally {
      setStarting(false)
    }
  }

  function handleChoose(taskId: number, optionId: number) {
    setChoices((previous) => ({ ...previous, [taskId]: optionId }))
  }

  function handleType(taskId: number, value: string) {
    setInputs((previous) => ({ ...previous, [taskId]: value }))
  }

  /** Сколько заданий уже отвечено: выбран вариант или введён непустой текст. */
  const answeredCount = tasks.filter(
    (task) =>
      (task.answer_format === 'choice' && choices[task.id] !== undefined) ||
      (task.answer_format === 'input' && (inputs[task.id] ?? '').trim() !== ''),
  ).length

  async function handleSubmit() {
    if (attempt === null) {
      return
    }

    const confirmed = window.confirm(
      `Вы ответили на ${answeredCount} из ${tasks.length}. Сдать?`,
    )
    if (!confirmed) {
      return
    }

    setSubmitting(true)
    setSubmitError('')

    try {
      // Отправляем только непустые ответы: пустое поле — это «не решал».
      const cleanInputs: Record<number, string> = {}
      for (const [taskId, value] of Object.entries(inputs)) {
        if (value.trim() !== '') {
          cleanInputs[Number(taskId)] = value.trim()
        }
      }

      const result = await submitAttempt(code, attempt.attempt_id, {
        attempt_token: attempt.attempt_token,
        choices,
        inputs: cleanInputs,
      })

      const saved: StoredAttempt = {
        savedAt: new Date().toISOString(),
        code,
        title: attempt.title,
        result,
        // Тексты заданий храним рядом с результатом, чтобы экран открывался
        // и без связи с сервером.
        tasks: tasks.map((task) => ({
          id: task.id,
          text: task.text,
          position: task.position,
        })),
      }

      saveAttempt(code, saved)
      clearProgress(code)
      setStored(saved)
      setPhase('done')
    } catch (error: unknown) {
      setSubmitError(error instanceof Error ? error.message : 'Не удалось сдать работу')
    } finally {
      setSubmitting(false)
    }
  }

  // ------------------------------------------------------------------
  // Экран 3: результат
  // ------------------------------------------------------------------
  if (phase === 'done' && stored !== null) {
    const { result } = stored
    const textById = new Map(stored.tasks.map((task) => [task.id, task.text]))

    return (
      <main className="page page--student">
        <h1>{stored.title}</h1>
        <p className="lead">
          {result.student_name}, {result.student_class} · вариант {result.variant_no}
        </p>

        <section className="card card--success">
          <p className="score">
            Верно {result.score} из {result.max_score}
          </p>
        </section>

        <ol className="questions">
          {result.results.map((item, index) => (
            <li key={item.task_id} className="question">
              <p className="question__text">
                <span className={item.is_correct ? 'mark mark--ok' : 'mark mark--bad'}>
                  {item.is_correct ? '✓' : '✗'}
                </span>{' '}
                {index + 1}. {textById.get(item.task_id) ?? `Задание ${item.position}`}
              </p>
              {!item.answered && <p className="muted">Ответ не дан</p>}
            </li>
          ))}
        </ol>

        <p className="muted">
          Работа сдана. Повторно пройти её нельзя — если нужна пересдача, обратитесь
          к учителю.
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
    // При сетевой ошибке и поломке сервера ссылка ни при чём — совет её
    // проверить только запутает.
    const isTechnical = loading.message === NETWORK_ERROR || loading.message === SERVER_ERROR

    return (
      <main className="page page--student">
        <h1>Контрольная не открылась</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
        {!isTechnical && <p className="muted">Проверьте ссылку — её должен дать учитель.</p>}
      </main>
    )
  }

  const info = loading.info

  // ------------------------------------------------------------------
  // Приём работ закрыт
  // ------------------------------------------------------------------
  if (!info.is_open && phase !== 'solving') {
    return (
      <main className="page page--student">
        <h1>{info.title}</h1>
        <section className="card">
          <h2>Приём работ закрыт</h2>
          <p>Учитель больше не принимает ответы по этой контрольной.</p>
          <p className="muted">
            Если вы должны были её сдать, подойдите к учителю: {info.teacher_name}.
          </p>
        </section>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // У контрольной не указаны классы — пройти её нельзя
  // ------------------------------------------------------------------
  if (info.classes.length === 0 && phase === 'start') {
    return (
      <main className="page page--student">
        <h1>{info.title}</h1>
        <section className="card">
          <h2>Эту контрольную пока нельзя пройти</h2>
          <p>В ней не указаны классы, поэтому отметить свой класс не получится.</p>
          <p className="muted">Сообщите учителю: {info.teacher_name}.</p>
        </section>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // Экран 1: имя и класс
  // ------------------------------------------------------------------
  if (phase === 'start' || attempt === null) {
    return (
      <main className="page page--student">
        <h1>{info.title}</h1>
        <p className="lead">
          Заданий: {info.tasks_count}. Учитель: {info.teacher_name}.
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
          <select
            id="student-class"
            className="input select"
            value={studentClass}
            onChange={(event) => setStudentClass(event.target.value)}
          >
            <option value="">— выберите класс —</option>
            {info.classes.map((className) => (
              <option key={className} value={className}>
                {className}
              </option>
            ))}
          </select>

          {formError !== '' && <p className="field-error">{formError}</p>}

          <div className="row">
            <button
              type="button"
              className="btn btn--primary btn--wide"
              onClick={handleStart}
              disabled={starting}
            >
              {starting ? 'Готовим вариант…' : 'Начать'}
            </button>
          </div>

          <p className="hint">
            Вариант выдаётся автоматически. Если вы уже начинали — продолжите свой.
          </p>
        </section>
      </main>
    )
  }

  // ------------------------------------------------------------------
  // Экран 2: задания
  // ------------------------------------------------------------------
  return (
    <main className="page page--student">
      <h1>{attempt.title}</h1>
      <p className="lead">
        {attempt.student_name}, {attempt.student_class} · вариант {attempt.variant_no}
      </p>

      <div className="progress" role="status">
        <div className="progress__bar" aria-hidden="true">
          <div
            className="progress__fill"
            style={{
              width: tasks.length === 0 ? '0%' : `${(answeredCount * 100) / tasks.length}%`,
            }}
          />
        </div>
        <p className="progress__text">
          Отвечено {answeredCount} из {tasks.length}
        </p>
      </div>

      <ol className="questions">
        {tasks.map((task, index) => (
          <li key={task.id} className="question">
            <p className="question__text">
              {index + 1}. {task.text}
            </p>

            {task.answer_format === 'choice' ? (
              <ul className="options">
                {task.options.map((option) => (
                  <li key={option.id}>
                    {/* Вся строка — label: нажать можно куда угодно, это важно
                        на телефоне, где попасть в кружок трудно. */}
                    <label
                      className={
                        choices[task.id] === option.id ? 'choice choice--picked' : 'choice'
                      }
                    >
                      <input
                        type="radio"
                        name={`task-${task.id}`}
                        checked={choices[task.id] === option.id}
                        onChange={() => handleChoose(task.id, option.id)}
                      />
                      <span>{option.text}</span>
                    </label>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="answerbox">
                <label className="label" htmlFor={`answer-${task.id}`}>
                  Ваш ответ
                </label>
                <input
                  id={`answer-${task.id}`}
                  className="input input--answer"
                  value={inputs[task.id] ?? ''}
                  onChange={(event) => handleType(task.id, event.target.value)}
                  placeholder="Введите ответ"
                  autoComplete="off"
                />
              </div>
            )}
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
        Можно оставить задание без ответа — оно будет считаться невыполненным.
      </p>
    </main>
  )
}
