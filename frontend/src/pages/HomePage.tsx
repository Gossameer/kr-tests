/**
 * Кабинет учителя: список своих контрольных.
 *
 * Список приходит С СЕРВЕРА, а не из памяти браузера: контрольные привязаны
 * к учётной записи, поэтому они на месте и на другом компьютере.
 *
 * Администратор видит здесь контрольные всей школы — с именем автора.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { fetchMyTests } from '../api'
import { useAuth } from '../lib/authContext'
import type { MyTest } from '../types'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; tests: MyTest[] }
  | { kind: 'error'; message: string }

/** Дата без времени: 27.09.2026 */
function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export default function HomePage() {
  const { user } = useAuth()
  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  const [copiedCode, setCopiedCode] = useState('')

  const load = useCallback(() => {
    fetchMyTests()
      .then((tests) => setLoading({ kind: 'ready', tests }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить список',
        }),
      )
  }, [])

  useEffect(load, [load])

  async function handleCopy(test: MyTest) {
    const url = `${window.location.origin}/t/${test.code}`
    try {
      await navigator.clipboard.writeText(url)
      setCopiedCode(test.code)
      window.setTimeout(() => setCopiedCode(''), 2000)
    } catch {
      window.prompt('Скопируйте ссылку для учеников:', url)
    }
  }

  return (
    <main className="page page--wide">
      <section className="hero">
        <h1>{user?.role === 'admin' ? 'Контрольные школы' : 'Мои контрольные'}</h1>
        <p className="hero__text">
          {user?.role === 'admin'
            ? 'Все контрольные, созданные учителями школы. Можно открыть результаты любой.'
            : 'Составьте контрольную по умениям, отправьте классу ссылку и посмотрите, ' +
              'какие умения освоены, а какие нет.'}
        </p>
        <Link className="btn btn--primary btn--large" to="/create">
          Создать контрольную
        </Link>
      </section>

      {loading.kind === 'loading' && <p className="loading">Загружаем список…</p>}

      {loading.kind === 'error' && (
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
      )}

      {loading.kind === 'ready' && (
        <section className="card">
          <div className="card__head">
            <h2>Список</h2>
            <span className="muted">{loading.tests.length}</span>
          </div>

          {loading.tests.length === 0 ? (
            <p className="empty">
              Контрольных пока нет. Нажмите «Создать контрольную» — это займёт несколько минут.
            </p>
          ) : (
            <ul className="testlist">
              {loading.tests.map((test) => (
                <li key={test.id} className="testcard">
                  <div className="testcard__main">
                    <p className="testcard__title">
                      {test.title}{' '}
                      <span className={test.is_open ? 'badge badge--open' : 'badge badge--closed'}>
                        {test.is_open ? 'приём открыт' : 'приём закрыт'}
                      </span>
                    </p>
                    <p className="testcard__meta">
                      {test.subject} · {test.classes.join(', ')} · вариантов{' '}
                      {test.variants_count} · сдали {test.attempts_count} ·{' '}
                      {formatDate(test.created_at)}
                      {user?.role === 'admin' && <> · {test.teacher_name}</>}
                    </p>
                  </div>

                  <div className="testcard__actions">
                    <Link className="btn btn--small btn--primary" to={`/tests/${test.id}/results`}>
                      Результаты
                    </Link>
                    <button
                      type="button"
                      className="btn btn--small btn--ghost"
                      onClick={() => handleCopy(test)}
                    >
                      {copiedCode === test.code ? 'Скопировано' : 'Ссылка ученикам'}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  )
}
