/**
 * Страница работы для учителя: /tests/:testId/results
 *
 * Главное здесь — не баллы, а таблица «ученик × умение»: видно, какое умение
 * не сформировано у конкретного ребёнка и у класса целиком.
 *
 * Здесь же управление работой: у каждого класса своя ссылка для учеников и свой
 * переключатель «приём открыт»; попытке можно разрешить пересдачу — старая
 * остаётся с отметкой «аннулирована» и в итоги не идёт.
 *
 * Доступ — у автора работы и администратора; права проверяет сервер.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import {
  NETWORK_ERROR,
  SERVER_ERROR,
  addTestClass,
  annulAttempt,
  deleteAttempt,
  deleteTest,
  fetchAttemptDetail,
  fetchResults,
  resultsExportUrl,
  updateTestClass,
  updateTestSettings,
} from '../api'
import CopyLink from '../components/CopyLink'
import MathText from '../components/MathText'
import type { AttemptDetail, AttemptRow, ClassLink, ResultsOverview, SkillStat } from '../types'
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

/** Строка класса: ссылка, «Скопировать», число сдавших, приём работ. */
function ClassLinkRow({
  link,
  busy,
  onToggle,
}: {
  link: ClassLink
  busy: boolean
  onToggle: (link: ClassLink) => void
}) {
  const [copied, setCopied] = useState(false)
  const url = `${window.location.origin}/t/${link.code}`

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Браузер не дал доступ к буферу (например, без https) — показываем ссылку.
      window.prompt(`Скопируйте ссылку для класса ${link.class_name}:`, url)
    }
  }

  return (
    <tr>
      <td>
        <b>{link.class_name}</b>
      </td>
      <td>
        <div className="row row--tight">
          <input
            className="linkbox"
            value={url}
            readOnly
            aria-label={`Ссылка для класса ${link.class_name}`}
            onFocus={(event) => event.target.select()}
          />
          <button type="button" className="btn btn--small btn--primary" onClick={handleCopy}>
            {copied ? 'Скопировано' : 'Скопировать'}
          </button>
        </div>
      </td>
      <td>{link.attempts_count}</td>
      <td>
        <div className="row row--tight">
          <span className={link.is_open ? 'badge badge--open' : 'badge badge--closed'}>
            {link.is_open ? 'открыт' : 'закрыт'}
          </span>
          <button
            type="button"
            className="btn btn--small btn--ghost"
            onClick={() => onToggle(link)}
            disabled={busy}
          >
            {link.is_open ? 'Закрыть приём' : 'Открыть приём'}
          </button>
        </div>
      </td>
    </tr>
  )
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
  const [newClass, setNewClass] = useState('')

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
  // В умения и итоги идут только действующие попытки: аннулированные — нет.
  const counted = useMemo(() => shown.filter((attempt) => !attempt.annulled), [shown])

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

  async function handleToggleClass(link: ClassLink) {
    setBusy(true)
    setActionError('')
    try {
      await updateTestClass(testId, link.id, !link.is_open)
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось изменить приём работ')
    } finally {
      setBusy(false)
    }
  }

  async function handleAddClass() {
    const name = newClass.trim()
    if (name === '') {
      document.getElementById('new-class')?.focus()
      return
    }
    setBusy(true)
    setActionError('')
    try {
      await addTestClass(testId, name)
      setNewClass('')
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось добавить класс')
    } finally {
      setBusy(false)
    }
  }

  async function handleAnnul(attemptId: number, studentName: string) {
    const confirmed = window.confirm(
      `Разрешить пересдачу: «${studentName}»?\n\n` +
        'Эта попытка получит отметку «аннулирована» и перестанет учитываться в умениях, ' +
        'итогах, статистике и Excel. Ученик сможет пройти работу заново по той же ссылке, ' +
        'в том числе с того же устройства.',
    )
    if (!confirmed) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      await annulAttempt(testId, attemptId)
      setOpenAttemptId(null)
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось разрешить пересдачу')
    } finally {
      setBusy(false)
    }
  }

  async function handleDeleteAttempt(attemptId: number, studentName: string) {
    const confirmed = window.confirm(
      `Удалить работу «${studentName}» совсем?\n\n` +
        'Ответы будут удалены безвозвратно. Если нужна только пересдача — нажмите ' +
        '«Разрешить пересдачу»: тогда прежняя работа сохранится в истории.',
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
        shownPercent: averagePercent(counted, skill.id),
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

        {/* ---------- Ссылки по классам ---------- */}
        <div id="class-links" tabIndex={-1}>
          <p className="muted">
            У каждого класса своя ссылка: класс уже задан, ученик вводит только фамилию и
            имя. Приём открывается и закрывается по каждому классу отдельно.
          </p>
          <div className="table-scroll">
            <table className="table classlinks">
              <thead>
                <tr>
                  <th>Класс</th>
                  <th>Ссылка для учеников</th>
                  <th>Сдали</th>
                  <th>Приём работ</th>
                </tr>
              </thead>
              <tbody>
                {data.class_links.map((link) => (
                  <ClassLinkRow key={link.id} link={link} busy={busy} onToggle={handleToggleClass} />
                ))}
              </tbody>
            </table>
          </div>

          <div className="row row--tight">
            <label className="label" htmlFor="new-class">
              Добавить класс
            </label>
            <input
              id="new-class"
              className="input input--short"
              value={newClass}
              onChange={(event) => setNewClass(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  void handleAddClass()
                }
              }}
              placeholder="Например: 6В"
              maxLength={20}
            />
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => void handleAddClass()}
              disabled={busy}
            >
              Добавить — появится новая ссылка
            </button>
          </div>
          <p className="hint">
            С одного устройства по ссылке класса можно сдать одну работу, и один ученик
            сдаёт в своём классе один раз. Другой класс по своей ссылке проходит работу на
            тех же компьютерах свободно. Пересдача — кнопкой «Разрешить пересдачу» в списке
            работ ниже.
          </p>
        </div>

        {/* ---------- Общая ссылка старой работы ---------- */}
        {!data.links_by_class && (
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
              {data.is_open ? 'Закрыть приём целиком' : 'Открыть приём целиком'}
            </button>
            <span className="hint">
              Общая ссылка этой работы (она внизу страницы) и ссылки всех классов разом.
            </span>
          </div>
        )}
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

        {counted.length === 0 ? (
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
                  {counted.map((attempt) => (
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
                      <span className="hint">учеников: {counted.length}</span>
                    </td>
                    {data.skills.map((skill) => {
                      const average = averagePercent(counted, skill.id)
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
                        {counted.length === 0
                          ? '—'
                          : `${Math.round(
                              counted.reduce((sum, item) => sum + item.percent, 0) /
                                counted.length,
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
      {counted.length > 0 && (
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
                        className={
                          'table__row' +
                          (isOpen ? ' table__row--open' : '') +
                          (attempt.annulled ? ' table__row--annulled' : '')
                        }
                        onClick={() => handleRowClick(attempt.attempt_id)}
                      >
                        <td>{attempt.student_class}</td>
                        <td>
                          <span className="caret">{isOpen ? '▾' : '▸'}</span>{' '}
                          {attempt.student_name}
                          {attempt.annulled && (
                            <span
                              className="badge badge--closed"
                              title="Разрешена пересдача: в умения, итоги и статистику эта попытка не идёт"
                            >
                              аннулирована
                            </span>
                          )}
                        </td>
                        <td>{attempt.variant_no}</td>
                        <td>
                          {attempt.score} / {attempt.max_score}
                        </td>
                        <td>{attempt.percent}%</td>
                        <td>{formatDateTime(attempt.finished_at)}</td>
                        <td>
                          <div className="rowactions">
                          {!attempt.annulled && (
                            <button
                              type="button"
                              className="btn btn--small btn--ghost"
                              disabled={busy}
                              onClick={(event) => {
                                // Иначе клик дойдёт до строки и раскроет разбор.
                                event.stopPropagation()
                                handleAnnul(attempt.attempt_id, attempt.student_name)
                              }}
                            >
                              Разрешить пересдачу
                            </button>
                          )}
                          <button
                            type="button"
                            className="iconbtn iconbtn--danger"
                            title="Удалить работу совсем (вместе с ответами)"
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
                          </div>
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
                                      {item.position}. <MathText text={item.text} />
                                    </p>
                                    <p className="answer__line hint">{item.skill_title}</p>
                                    <p className="answer__line">
                                      Ответ ученика:{' '}
                                      {item.answered ? (
                                        <b>
                                          <MathText text={item.student_answer ?? ''} />
                                        </b>
                                      ) : (
                                        <i>не отвечал</i>
                                      )}
                                    </p>
                                    {!item.is_correct && (
                                      <p className="answer__line answer__line--correct">
                                        Правильно:{' '}
                                        <b>
                                          <MathText text={item.correct_answer} />
                                        </b>
                                      </p>
                                    )}
                                    {item.solution !== '' && (
                                      <p className="answer__line muted">
                                        Решение: <MathText text={item.solution} />
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

      {/* ------------------------- Общая ссылка (старые работы) ------------------------- */}
      {!data.links_by_class && (
        <section className="card">
          <h2>Общая ссылка для учеников</h2>
          <CopyLink
            label="Работает как раньше: ученик сам выбирает класс из списка"
            url={studentUrl}
            hint="Эта работа создана до ссылок по классам. Удобнее раздавать ссылки классов — они выше."
          />
        </section>
      )}

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
