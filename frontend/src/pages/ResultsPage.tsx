/**
 * Страница результатов для учителя: /r/:token
 *
 * Главное здесь — не баллы, а таблица «ученик × умение»: видно, какое умение
 * не сформировано у конкретного ребёнка и у класса целиком.
 *
 * Доступ по секретной ссылке — авторизации пока нет, поэтому адрес нельзя
 * показывать ученикам: по нему видны ответы, решения и управление работой.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import {
  NETWORK_ERROR,
  SERVER_ERROR,
  deleteAttempt,
  deleteTest,
  fetchAttemptDetail,
  fetchResults,
  resultsExportUrl,
  updateTestSettings,
} from '../api'
import CopyLink from '../components/CopyLink'
import type { AttemptDetail, AttemptRow, ResultsOverview, SkillStat } from '../types'
import { usePageTitle } from '../lib/usePageTitle'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ResultsOverview }
  | { kind: 'error'; message: string }

/** Пороги освоения умения — те же, что в Excel. */
const LEVEL_LOW = 50
const LEVEL_MID = 65

/** Класс ячейки по проценту: красный / жёлтый / зелёный. */
function levelClass(percent: number | undefined): string {
  if (percent === undefined) {
    return 'cellval cellval--none'
  }
  if (percent < LEVEL_LOW) {
    return 'cellval cellval--low'
  }
  if (percent <= LEVEL_MID) {
    return 'cellval cellval--mid'
  }
  return 'cellval cellval--high'
}

/** Дата и время в привычном виде: 26.09.2026, 16:32 */
function formatDateTime(value: string | null): string {
  if (value === null) {
    return 'не сдана'
  }
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Среднее по столбцу умения для выбранных учеников. */
function averagePercent(attempts: AttemptRow[], skillId: number): number | undefined {
  const values = attempts
    .map((attempt) => attempt.skill_percents[skillId])
    .filter((value): value is number => typeof value === 'number')

  if (values.length === 0) {
    return undefined
  }
  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length)
}

export default function ResultsPage() {
  usePageTitle('Результаты')
  // Проверочная работа определяется её номером, а права проверяет сервер:
  // чужую по прямой ссылке не открыть.
  const { testId: testIdParam = '' } = useParams()
  const testId = Number(testIdParam)
  const navigate = useNavigate()

  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  const [openAttemptId, setOpenAttemptId] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, AttemptDetail>>({})
  const [detailError, setDetailError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)
  // Фильтр по классу: пустая строка — показывать всех.
  const [classFilter, setClassFilter] = useState('')

  const load = useCallback(() => {
    fetchResults(testId)
      .then((data) => setLoading({ kind: 'ready', data }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить результаты',
        }),
      )
  }, [testId])

  useEffect(load, [load])

  // Ученики с учётом фильтра по классу.
  const shown = useMemo(() => {
    if (loading.kind !== 'ready') {
      return []
    }
    if (classFilter === '') {
      return loading.data.attempts
    }
    return loading.data.attempts.filter((attempt) => attempt.student_class === classFilter)
  }, [loading, classFilter])

  function handleRefresh() {
    setDetails({})
    setOpenAttemptId(null)
    setActionError('')
    setLoading({ kind: 'loading' })
    load()
  }

  function handleRowClick(attemptId: number) {
    if (openAttemptId === attemptId) {
      setOpenAttemptId(null)
      return
    }

    setOpenAttemptId(attemptId)
    setDetailError('')

    if (details[attemptId] !== undefined) {
      return
    }

    fetchAttemptDetail(testId, attemptId)
      .then((detail) => setDetails((previous) => ({ ...previous, [attemptId]: detail })))
      .catch((error: unknown) =>
        setDetailError(
          error instanceof Error ? error.message : 'Не удалось загрузить разбор работы',
        ),
      )
  }

  async function handleToggleOpen(isOpen: boolean) {
    setBusy(true)
    setActionError('')
    try {
      await updateTestSettings(testId, isOpen)
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось изменить приём работ')
    } finally {
      setBusy(false)
    }
  }

  async function handleDeleteAttempt(attemptId: number, studentName: string) {
    const confirmed = window.confirm(
      `Удалить работу «${studentName}»?\n\n` +
        'Ответы будут удалены безвозвратно, зато ученик сможет пройти работу заново.',
    )
    if (!confirmed) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      await deleteAttempt(testId, attemptId)
      setOpenAttemptId(null)
      setDetails({})
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось удалить работу')
    } finally {
      setBusy(false)
    }
  }

  async function handleDeleteTest() {
    const typed = window.prompt(
      'Удалить проверочную работу вместе со всеми ответами учеников?\n\n' +
        'Это действие необратимо. Для подтверждения введите название работы:',
    )
    if (typed === null) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      await deleteTest(testId, typed)
      navigate('/')
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось удалить работу. Обновите страницу и попробуйте ещё раз.')
    } finally {
      setBusy(false)
    }
  }

  if (loading.kind === 'loading') {
    return (
      <main className="page page--wide">
        <p className="loading">Загружаем результаты…</p>
      </main>
    )
  }

  if (loading.kind === 'error') {
    const isTechnical = loading.message === NETWORK_ERROR || loading.message === SERVER_ERROR

    return (
      <main className="page page--wide">
        <h1>Результаты не открылись</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
        {!isTechnical && (
          <p className="muted">
            Открывать результаты может только автор работы и администратор школы.
          </p>
        )}
      </main>
    )
  }

  const data = loading.data

  // Сводка: умения от самых проваленных. Считаем по показанным ученикам,
  // чтобы фильтр по классу влиял и на неё.
  const weakestFirst: (SkillStat & { shownPercent: number | undefined })[] = data.skills
    .map((skill) => {
      const stat = data.skill_stats.find((item) => item.skill_id === skill.id)
      return {
        skill_id: skill.id,
        position: skill.position,
        title: skill.title,
        correct: stat?.correct ?? 0,
        total: stat?.total ?? 0,
        percent: stat?.percent ?? 0,
        shownPercent: averagePercent(shown, skill.id),
      }
    })
    .sort((a, b) => (a.shownPercent ?? 101) - (b.shownPercent ?? 101))

  const studentUrl = `${window.location.origin}/t/${data.code}`

  return (
    <main className="page page--wide">
      <h1>{data.title}</h1>
      <p className="lead">
        {data.teacher_name} · {data.subject || 'предмет не указан'} ·{' '}
        {data.classes.join(', ') || 'классы не указаны'} · вариантов{' '}
        {data.variants_count} · умений {data.skills.length} · сдали {data.attempts_count}
      </p>

      {actionError !== '' && (
        <section className="alert alert--error">
          <p>{actionError}</p>
        </section>
      )}

      {/* ------------------------- Управление ------------------------- */}
      <section className="card">
        <div className="card__head">
          <h2>Приём работ</h2>
          <div className="row row--tight">
            <button type="button" className="btn btn--ghost" onClick={handleRefresh}>
              Обновить
            </button>
            <a className="btn btn--primary" href={resultsExportUrl(testId)}>
              Скачать Excel
            </a>
          </div>
        </div>

        <div className="switchrow">
          <span className={data.is_open ? 'badge badge--open' : 'badge badge--closed'}>
            {data.is_open ? 'Открыт' : 'Закрыт'}
          </span>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => handleToggleOpen(!data.is_open)}
            disabled={busy}
          >
            {data.is_open ? 'Закрыть приём' : 'Открыть приём'}
          </button>
          <span className="hint">
            {data.is_open
              ? 'Ученики могут начинать и сдавать работы по ссылке.'
              : 'Ученики видят сообщение «приём работ закрыт». Сданные работы сохранены.'}
          </span>
        </div>
      </section>

      {/* ------------------------- Фильтр по классу ------------------------- */}
      {data.classes.length > 1 && (
        <section className="card">
          <h2>Класс</h2>
          <div className="row row--tight">
            <button
              type="button"
              className={classFilter === '' ? 'chip chip--active' : 'chip'}
              onClick={() => setClassFilter('')}
            >
              Все классы
            </button>
            {data.classes.map((className) => (
              <button
                key={className}
                type="button"
                className={classFilter === className ? 'chip chip--active' : 'chip'}
                onClick={() => setClassFilter(className)}
              >
                {className}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ------------------------- Матрица «ученик × умение» ------------------------- */}
      <section className="card">
        <h2>Умения</h2>

        {shown.length === 0 ? (
          <p className="empty">
            {data.attempts_count === 0
              ? 'Пока никто не сдал. Отправьте ученикам ссылку и нажмите «Обновить».'
              : 'В выбранном классе работ пока нет.'}
          </p>
        ) : (
          <>
            <p className="muted">
              Процент выполнения по каждому умению. Красный — ниже {LEVEL_LOW}%, жёлтый —
              до {LEVEL_MID}%, зелёный — выше.
            </p>

            <div className="table-scroll">
              <table className="table matrix">
                <thead>
                  <tr>
                    <th>Ученик</th>
                    {data.skills.map((skill) => (
                      <th key={skill.id} title={skill.title}>
                        {skill.position}. {skill.title}
                      </th>
                    ))}
                    <th>Итого</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((attempt) => (
                    <tr key={attempt.attempt_id}>
                      <td className="matrix__skill">
                        <span className="matrix__title">{attempt.student_name}</span>
                        <span className="hint">
                          {attempt.student_class} · вариант {attempt.variant_no}
                        </span>
                      </td>
                      {data.skills.map((skill) => {
                        const percent = attempt.skill_percents[skill.id]
                        return (
                          <td key={skill.id}>
                            <span className={levelClass(percent)}>
                              {percent === undefined ? '—' : `${percent}%`}
                            </span>
                          </td>
                        )
                      })}
                      <td>
                        <b>{attempt.percent}%</b>
                      </td>
                    </tr>
                  ))}

                  {/* Строка по классу: среднее по показанным ученикам. */}
                  <tr className="matrix__total">
                    <td>
                      <b>{classFilter === '' ? 'Все классы' : classFilter}</b>{' '}
                      <span className="hint">учеников: {shown.length}</span>
                    </td>
                    {data.skills.map((skill) => {
                      const average = averagePercent(shown, skill.id)
                      return (
                        <td key={skill.id}>
                          <span className={levelClass(average)}>
                            {average === undefined ? '—' : `${average}%`}
                          </span>
                        </td>
                      )
                    })}
                    <td>
                      <b>
                        {shown.length === 0
                          ? '—'
                          : `${Math.round(
                              shown.reduce((sum, item) => sum + item.percent, 0) / shown.length,
                            )}%`}
                      </b>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {/* ------------------------- Не сформированные умения ------------------------- */}
      {shown.length > 0 && (
        <section className="card">
          <h2>Что не сформировано</h2>
          <p className="muted">Сверху — умения, с которыми справились хуже всего.</p>

          <ol className="stats">
            {weakestFirst.map((skill) => (
              <li key={skill.skill_id} className="stat">
                <p className="stat__text">
                  {skill.position}. {skill.title}
                </p>
                <div className="bar" aria-hidden="true">
                  <div
                    className={
                      'bar__fill' +
                      ((skill.shownPercent ?? 0) < LEVEL_LOW
                        ? ' bar__fill--low'
                        : (skill.shownPercent ?? 0) <= LEVEL_MID
                          ? ' bar__fill--mid'
                          : '')
                    }
                    style={{ width: `${skill.shownPercent ?? 0}%` }}
                  />
                </div>
                <p className="stat__numbers">
                  {skill.shownPercent === undefined
                    ? 'нет данных'
                    : `выполнено ${skill.shownPercent}% заданий`}
                </p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* ------------------------- Работы ------------------------- */}
      <section className="card">
        <h2>Работы</h2>

        {shown.length === 0 ? (
          <p className="empty">
            Пока никто не сдал. Когда ученики сдадут работу, она появится в этом списке.
          </p>
        ) : (
          <>
            <p className="muted">Нажмите на строку, чтобы посмотреть разбор работы.</p>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Класс</th>
                    <th>ФИО</th>
                    <th>Вариант</th>
                    <th>Балл</th>
                    <th>%</th>
                    <th>Время сдачи</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((attempt) => {
                    const isOpen = openAttemptId === attempt.attempt_id
                    const detail = details[attempt.attempt_id]

                    return [
                      <tr
                        key={attempt.attempt_id}
                        className={isOpen ? 'table__row table__row--open' : 'table__row'}
                        onClick={() => handleRowClick(attempt.attempt_id)}
                      >
                        <td>{attempt.student_class}</td>
                        <td>
                          <span className="caret">{isOpen ? '▾' : '▸'}</span>{' '}
                          {attempt.student_name}
                        </td>
                        <td>{attempt.variant_no}</td>
                        <td>
                          {attempt.score} / {attempt.max_score}
                        </td>
                        <td>{attempt.percent}%</td>
                        <td>{formatDateTime(attempt.finished_at)}</td>
                        <td>
                          <button
                            type="button"
                            className="iconbtn iconbtn--danger"
                            title="Удалить работу и разрешить пересдачу"
                            aria-label={`Удалить работу ${attempt.student_name}`}
                            disabled={busy}
                            onClick={(event) => {
                              // Иначе клик дойдёт до строки и раскроет разбор.
                              event.stopPropagation()
                              handleDeleteAttempt(attempt.attempt_id, attempt.student_name)
                            }}
                          >
                            ✕
                          </button>
                        </td>
                      </tr>,

                      isOpen && (
                        <tr key={`${attempt.attempt_id}-detail`} className="table__detail">
                          <td colSpan={7}>
                            {detailError !== '' && <p className="field-error">{detailError}</p>}
                            {detail === undefined && detailError === '' && (
                              <p className="loading">Загружаем…</p>
                            )}

                            {detail !== undefined && (
                              <ol className="answers">
                                {detail.items.map((item) => (
                                  <li key={item.task_id} className="answer">
                                    <p className="answer__question">
                                      <span
                                        className={
                                          item.is_correct ? 'mark mark--ok' : 'mark mark--bad'
                                        }
                                      >
                                        {item.is_correct ? '✓' : '✗'}
                                      </span>{' '}
                                      {item.position}. {item.text}
                                    </p>
                                    <p className="answer__line hint">{item.skill_title}</p>
                                    <p className="answer__line">
                                      Ответ ученика:{' '}
                                      {item.answered ? (
                                        <b>{item.student_answer}</b>
                                      ) : (
                                        <i>не отвечал</i>
                                      )}
                                    </p>
                                    {!item.is_correct && (
                                      <p className="answer__line answer__line--correct">
                                        Правильно: <b>{item.correct_answer}</b>
                                      </p>
                                    )}
                                    {item.solution !== '' && (
                                      <p className="answer__line muted">
                                        Решение: {item.solution}
                                      </p>
                                    )}
                                  </li>
                                ))}
                              </ol>
                            )}
                          </td>
                        </tr>
                      ),
                    ]
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {/* ------------------------- Ссылка ученикам ------------------------- */}
      <section className="card">
        <h2>Ссылка для учеников</h2>
        <CopyLink
          label="Отправьте её классу"
          url={studentUrl}
          hint="Вариант выдаётся каждому ученику автоматически. Вход ученикам не нужен."
        />
      </section>

      {/* ------------------------- Опасная зона ------------------------- */}
      <section className="card card--danger">
        <h2>Удалить проверочную работу</h2>
        <p className="muted">
          Вместе с ней удалятся все умения, задания и ответы учеников. Отменить
          это нельзя. Для подтверждения потребуется ввести название.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn--danger"
            onClick={handleDeleteTest}
            disabled={busy}
          >
            Удалить проверочную работу
          </button>
        </div>
      </section>
    </main>
  )
}
