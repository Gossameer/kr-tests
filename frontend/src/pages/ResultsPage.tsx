/**
 * Страница результатов для учителя: /r/:token
 *
 * Доступ по секретной ссылке — авторизации пока нет. Поэтому адрес нельзя
 * отправлять ученикам: по нему видны все работы и правильные ответы.
 *
 * Что на экране:
 *   - таблица сдавших (класс, ФИО, балл, процент, время);
 *   - клик по строке раскрывает разбор работы этого ученика;
 *   - сводка по вопросам, отсортированная от самых проваленных;
 *   - кнопки «Обновить» и «Скачать Excel».
 */

import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { fetchAttemptDetail, fetchResults, resultsExportUrl } from '../api'
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

  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  // Раскрытая строка: id работы или null.
  const [openAttemptId, setOpenAttemptId] = useState<number | null>(null)
  // Разборы работ, уже загруженные с сервера: {id работы: детали}.
  const [details, setDetails] = useState<Record<number, AttemptDetail>>({})
  const [detailError, setDetailError] = useState('')

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

  if (loading.kind === 'loading') {
    return (
      <main className="page page--wide">
        <p>Загружаем результаты…</p>
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
        <p className="muted">
          Ссылка на результаты длинная и отличается от ученической — проверьте, что
          скопировали её целиком.
        </p>
      </main>
    )
  }

  const data = loading.data
  // Копию массива сортируем: исходный из состояния менять нельзя.
  const hardestFirst = [...data.question_stats].sort((a, b) => successRate(a) - successRate(b))

  return (
    <main className="page page--wide">
      <h1>{data.title}</h1>
      <p className="lead">
        Вопросов: {data.questions_count}. Сдали: {data.attempts_count}. Код для учеников:{' '}
        <code>{data.code}</code>
      </p>

      <div className="row">
        <button type="button" className="btn btn--ghost" onClick={handleRefresh}>
          Обновить
        </button>
        {/* Обычная ссылка, а не fetch: браузер сам возьмёт имя файла из заголовка ответа. */}
        <a className="btn btn--primary" href={resultsExportUrl(token)}>
          Скачать Excel
        </a>
      </div>

      {/* ------------------------- Таблица сдавших ------------------------- */}
      {data.attempts.length === 0 ? (
        <section className="card">
          <p>Работ пока нет. Отправьте ученикам ссылку на тест и обновите страницу.</p>
        </section>
      ) : (
        <section className="card">
          <h2>Сдавшие работу</h2>
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
                        <span className="caret">{isOpen ? '▾' : '▸'}</span> {attempt.student_name}
                      </td>
                      <td>
                        {attempt.score} / {attempt.max_score}
                      </td>
                      <td>{attempt.percent}%</td>
                      <td>{formatDateTime(attempt.finished_at)}</td>
                    </tr>,

                    isOpen && (
                      <tr key={`${attempt.attempt_id}-detail`} className="table__detail">
                        <td colSpan={5}>
                          {detailError !== '' && <p className="field-error">{detailError}</p>}

                          {detail === undefined && detailError === '' && <p>Загружаем…</p>}

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
        </section>
      )}

      {/* ------------------------- Сводка по вопросам ----------------------- */}
      <section className="card">
        <h2>По вопросам</h2>
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
      </section>

      <p className="muted">
        Эта страница защищена только секретом в адресе. Не пересылайте ссылку ученикам.
      </p>
    </main>
  )
}
