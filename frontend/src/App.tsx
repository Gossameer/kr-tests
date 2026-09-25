/**
 * Страница-заглушка. Её единственная задача сейчас —
 * показать, что фронтенд запустился и видит бэкенд.
 */

import { useEffect, useState } from 'react'
import { API_URL, fetchHealth, type HealthResponse } from './api'

// Возможные состояния проверки связи с бэкендом.
type Status =
  | { kind: 'loading' }
  | { kind: 'ok'; data: HealthResponse }
  | { kind: 'error'; message: string }

export default function App() {
  const [status, setStatus] = useState<Status>({ kind: 'loading' })

  // useEffect с пустым списком зависимостей = «выполнить один раз при открытии страницы».
  useEffect(() => {
    fetchHealth()
      .then((data) => setStatus({ kind: 'ok', data }))
      .catch((error: unknown) =>
        setStatus({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Неизвестная ошибка',
        }),
      )
  }, [])

  return (
    <main className="page">
      <h1>КР — сервис контрольных</h1>
      <p className="lead">
        Учитель создаёт контрольную и получает ссылку, ученики решают тест по ссылке,
        результаты собираются автоматически.
      </p>

      <section className="card">
        <h2>Связь с бэкендом</h2>
        <p className="muted">
          API: <code>{API_URL}</code>
        </p>

        {status.kind === 'loading' && <p>Проверяем…</p>}

        {status.kind === 'ok' && (
          <ul className="checks">
            <li>
              <span className="dot dot--ok" /> Бэкенд отвечает: <b>{status.data.status}</b>
            </li>
            <li>
              <span className={status.data.database === 'up' ? 'dot dot--ok' : 'dot dot--bad'} />
              База данных: <b>{status.data.database === 'up' ? 'подключена' : 'недоступна'}</b>
            </li>
          </ul>
        )}

        {status.kind === 'error' && (
          <div className="error">
            <p>
              <span className="dot dot--bad" /> Бэкенд недоступен: {status.message}
            </p>
            <p className="muted">
              Запусти его командой <code>uvicorn app.main:app --reload</code> из папки{' '}
              <code>backend</code>.
            </p>
          </div>
        )}
      </section>

      <p className="muted">Дальше: создание контрольной из JSON, страница ученика, результаты.</p>
    </main>
  )
}
