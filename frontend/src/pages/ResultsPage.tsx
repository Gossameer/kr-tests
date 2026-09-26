/**
 * Страница результатов для учителя: /r/:token
 *
 * Доступ по секретной ссылке — авторизации пока нет. Поэтому адрес нельзя
 * отправлять ученикам: по нему видны все работы и правильные ответы.
 *
 * Что на экране:
 *   - переключатель «приём работ открыт / закрыт»;
 *   - обе ссылки (ученикам и на эту страницу) с кнопками «Скопировать»;
 *   - таблица сдавших; клик по строке раскрывает разбор работы;
 *   - удаление работы (это и есть разрешение на пересдачу);
 *   - сводка по вопросам, от самых проваленных;
 *   - выгрузка в Excel и удаление контрольной.
 */

import { useCallback, useEffect, useState } from 'react'
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
import { forgetTest } from '../lib/myTestsStorage'
import type { AttemptDetail, QuestionStat, ResultsOverview } from '../types'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ResultsOverview }
  | { kind: 'error'; message: string }

/** Дата и время в привычном виде: 26.09.2026, 16:32 */
function formatDateTime(value: string | null): string {
  if (value === null) {
    return '—'
  }
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Доля верных ответов по вопросу — по ней сортируем сводку.
 * Вопрос, который никто не решал, считаем решённым на 100%,
 * чтобы он не всплыл наверх как «самый сложный».
 */
function successRate(stat: QuestionStat): number {
  const total = stat.correct_count + stat.wrong_count + stat.skipped_count
  return total === 0 ? 1 : stat.correct_count / total
}

export default function ResultsPage() {
  const { token = '' } = useParams()
  const navigate = useNavigate()

  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  // Раскрытая строка: id работы или null.
  const [openAttemptId, setOpenAttemptId] = useState<number | null>(null)
  // Разборы работ, уже загруженные с сервера: {id работы: детали}.
  const [details, setDetails] = useState<Record<number, AttemptDetail>>({})
  const [detailError, setDetailError] = useState('')
  // Общие ошибки действий: переключение приёма, удаление.
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)

  // useCallback — чтобы функцию можно было безопасно указать в зависимостях useEffect.
  // Состояние «загружаем» здесь НЕ ставим: при первом показе оно уже такое,
  // а при нажатии «Обновить» его ставит сама кнопка.
  const load = useCallback(() => {
    fetchResults(token)
      .then((data) => setLoading({ kind: 'ready', data }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить результаты',
        }),
      )
  }, [token])

  useEffect(load, [load])

  function handleRefresh() {
    // Разборы тоже сбрасываем: работы могли измениться.
    setDetails({})
    setOpenAttemptId(null)
    setActionError('')
    setLoading({ kind: 'loading' })
    load()
  }

  function handleRowClick(attemptId: number) {
    // Повторный клик по той же строке — закрыть.
    if (openAttemptId === attemptId) {
      setOpenAttemptId(null)
      return
    }

    setOpenAttemptId(attemptId)
    setDetailError('')

    // Уже загруженный разбор второй раз не запрашиваем.
    if (details[attemptId] !== undefined) {
      return
    }

    fetchAttemptDetail(token, attemptId)
      .then((detail) => setDetails((previous) => ({ ...previous, [attemptId]: detail })))
      .catch((error: unknown) =>
        setDetailError(
          error instanceof Error ? error.message : 'Не удалось загрузить разбор работы',
        ),
      )
  }

  /** Переключатель приёма работ. */
  async function handleToggleOpen(isOpen: boolean) {
    setBusy(true)
    setActionError('')
    try {
      await updateTestSettings(token, isOpen)
      load()
    } catch (error: unknown) {
      setActionError(
        error instanceof Error ? error.message : 'Не удалось изменить приём работ',
      )
    } finally {
      setBusy(false)
    }
  }

  /** Удаление одной работы — так учитель разрешает ученику пересдать. */
  async function handleDeleteAttempt(attemptId: number, studentName: string) {
    const confirmed = window.confirm(
      `Удалить работу «${studentName}»?\n\n` +
        'Ответы будут удалены безвозвратно, зато ученик сможет сдать контрольную заново.',
    )
    if (!confirmed) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      await deleteAttempt(token, attemptId)
      setOpenAttemptId(null)
      setDetails({})
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось удалить работу')
    } finally {
      setBusy(false)
    }
  }

  /** Удаление всей контрольной: подтверждение вводом названия. */
  async function handleDeleteTest(code: string) {
    const typed = window.prompt(
      'Удалить контрольную вместе со всеми работами?\n\n' +
        'Это действие необратимо. Для подтверждения введите название контрольной:',
    )
    // Нажали «Отмена» — ничего не делаем.
    if (typed === null) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      // Сервер сверяет название сам, поэтому опечатку он и остановит.
      await deleteTest(token, typed)
      // Убираем из списка «Мои контрольные» в этом браузере.
      forgetTest(code)
      navigate('/')
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось удалить контрольную')
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
    return (
      <main className="page page--wide">
        <h1>Результаты не открылись</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
        {loading.message !== NETWORK_ERROR && loading.message !== SERVER_ERROR && (
          <p className="muted">
            Ссылка на результаты длинная и отличается от ученической — проверьте, что
            скопировали её целиком.
          </p>
        )}
      </main>
    )
  }

  const data = loading.data
  // Копию массива сортируем: исходный из состояния менять нельзя.
  const hardestFirst = [...data.question_stats].sort((a, b) => successRate(a) - successRate(b))

  const studentUrl = `${window.location.origin}/t/${data.code}`
  const resultsUrl = `${window.location.origin}/r/${token}`

  return (
    <main className="page page--wide">
      <h1>{data.title}</h1>
      <p className="lead">
        {data.teacher_name} · {data.classes.join(', ') || 'классы не указаны'} · вопросов:{' '}
        {data.questions_count} · сдали: {data.attempts_count}
      </p>

      {actionError !== '' && (
        <section className="alert alert--error">
          <p>{actionError}</p>
        </section>
      )}

      {/* ------------------------- Приём работ ------------------------- */}
      <section className="card">
        <h2>Приём работ</h2>
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
              ? 'Ученики могут сдавать работы по ссылке.'
              : 'Ученики видят сообщение «приём работ закрыт». Сданные работы сохранены.'}
          </span>
        </div>
      </section>

      {/* ------------------------- Ссылки ------------------------- */}
      <section className="card">
        <h2>Ссылки</h2>
        <CopyLink
          label="Для учеников"
          url={studentUrl}
          hint="Эту ссылку отправьте классу."
        />
        <CopyLink
          secret
          label="Эта страница результатов — только для вас"
          url={resultsUrl}
          hint="Сохраните страницу в закладки: без ссылки результаты не открыть, а восстановить её негде."
        />
      </section>

      {/* ------------------------- Таблица сдавших ------------------------- */}
      <section className="card">
        <div className="card__head">
          <h2>Сдавшие работу</h2>
          <div className="row row--tight">
            <button type="button" className="btn btn--ghost" onClick={handleRefresh}>
              Обновить
            </button>
            {/* Обычная ссылка, а не fetch: браузер сам возьмёт имя файла из заголовка. */}
            <a className="btn btn--primary" href={resultsExportUrl(token)}>
              Скачать Excel
            </a>
          </div>
        </div>

        {data.attempts.length === 0 ? (
          <p className="empty">
            Пока никто не сдал. Отправьте ученикам ссылку и нажмите «Обновить».
          </p>
        ) : (
          <>
            <p className="muted">Нажмите на строку, чтобы посмотреть ответы ученика.</p>

            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Класс</th>
                    <th>ФИО</th>
                    <th>Балл</th>
                    <th>%</th>
                    <th>Время сдачи</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.attempts.map((attempt) => {
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
                          <td colSpan={6}>
                            {detailError !== '' && <p className="field-error">{detailError}</p>}

                            {detail === undefined && detailError === '' && (
                              <p className="loading">Загружаем…</p>
                            )}

                            {detail !== undefined && (
                              <ol className="answers">
                                {detail.items.map((item) => (
                                  <li key={item.question_id} className="answer">
                                    <p className="answer__question">
                                      <span
                                        className={
                                          item.is_correct ? 'mark mark--ok' : 'mark mark--bad'
                                        }
                                      >
                                        {item.is_correct ? '✓' : '✗'}
                                      </span>{' '}
                                      {item.position}. {item.question_text}
                                    </p>
                                    <p className="answer__line">
                                      Ответ ученика:{' '}
                                      {item.answered ? (
                                        <b>{item.chosen_option_text}</b>
                                      ) : (
                                        <i>не отвечал</i>
                                      )}
                                    </p>
                                    {/* Правильный вариант показываем, только если ученик ошибся —
                                        когда верно, он и так совпадает с ответом. */}
                                    {!item.is_correct && (
                                      <p className="answer__line answer__line--correct">
                                        Правильно: <b>{item.correct_option_text}</b>
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

      {/* ------------------------- Сводка по вопросам ----------------------- */}
      <section className="card">
        <h2>По вопросам</h2>

        {data.attempts.length === 0 ? (
          <p className="empty">Сводка появится, когда работы начнут поступать.</p>
        ) : (
          <>
            <p className="muted">Сверху — те, с которыми справились хуже всего.</p>

            <ol className="stats">
              {hardestFirst.map((stat) => {
                const total = stat.correct_count + stat.wrong_count + stat.skipped_count
                const percent = total === 0 ? 0 : Math.round((stat.correct_count * 100) / total)

                return (
                  <li key={stat.question_id} className="stat">
                    <p className="stat__text">
                      {stat.position}. {stat.text}
                    </p>
                    {/* Полоса — наглядная доля верных ответов. Цифры рядом обязательны:
                        по одной полосе точное значение не прочитать. */}
                    <div className="bar" aria-hidden="true">
                      <div className="bar__fill" style={{ width: `${percent}%` }} />
                    </div>
                    <p className="stat__numbers">
                      верно {stat.correct_count} ({percent}%) · неверно {stat.wrong_count} ·
                      пропустили {stat.skipped_count}
                    </p>
                  </li>
                )
              })}
            </ol>
          </>
        )}
      </section>

      {/* ------------------------- Опасная зона ----------------------- */}
      <section className="card card--danger">
        <h2>Удалить контрольную</h2>
        <p className="muted">
          Вместе с контрольной удалятся все вопросы и все сданные работы. Отменить это
          нельзя. Для подтверждения потребуется ввести название.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn--danger"
            onClick={() => handleDeleteTest(data.code)}
            disabled={busy}
          >
            Удалить контрольную
          </button>
        </div>
      </section>

      <p className="muted">
        Эта страница защищена только секретом в адресе. Не пересылайте ссылку ученикам.
      </p>
    </main>
  )
}
