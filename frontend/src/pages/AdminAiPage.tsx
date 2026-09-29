/**
 * Админка → ИИ: /admin/ai
 *
 * Сколько тратит встроенная генерация: за день и за месяц, отдельно
 * составление заданий и самопроверка, по учителям. Здесь же дневной лимит
 * генераций на учителя. Цены провайдера сервис не знает — показываем токены,
 * а сумму видно в личном кабинете AITUNNEL.
 */

import { useEffect, useState } from 'react'
import { fetchAdminAi, updateAiLimit } from '../api'
import type { AdminAiOverview, AiTokens, AiUsage } from '../types'
import { usePageTitle } from '../lib/usePageTitle'

/** 12345 → «12 345»: крупные числа читаются легче. */
function formatNumber(value: number): string {
  return value.toLocaleString('ru-RU')
}

function tokens(value: AiTokens): string {
  return `${formatNumber(value.prompt_tokens)} / ${formatNumber(value.completion_tokens)}`
}

function UsageTiles({ title, usage }: { title: string; usage: AiUsage }) {
  return (
    <div className="aiusage">
      <h3>{title}</h3>
      <ul className="tiles">
        <li className="tile">
          <span className="tile__value">{formatNumber(usage.requests)}</span>
          <span className="tile__label">
            запросов{usage.failed > 0 && `, из них неудачных ${formatNumber(usage.failed)}`}
          </span>
        </li>
        <li className="tile">
          <span className="tile__value">{formatNumber(usage.prompt_tokens)}</span>
          <span className="tile__label">токенов ввода</span>
        </li>
        <li className="tile">
          <span className="tile__value">{formatNumber(usage.completion_tokens)}</span>
          <span className="tile__label">токенов вывода</span>
        </li>
      </ul>
    </div>
  )
}

export default function AdminAiPage() {
  usePageTitle('ИИ')
  const [data, setData] = useState<AdminAiOverview | null>(null)
  const [error, setError] = useState('')
  const [limit, setLimit] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchAdminAi()
      .then((overview) => {
        setData(overview)
        setLimit(String(overview.daily_limit))
      })
      .catch((problem: unknown) =>
        setError(problem instanceof Error ? problem.message : 'Не удалось загрузить данные'),
      )
  }, [])

  async function handleSave(event: React.FormEvent) {
    event.preventDefault()
    const value = Number(limit)
    if (!Number.isInteger(value) || value < 0 || value > 1000) {
      setError('Лимит — целое число от 0 до 1000.')
      return
    }

    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const result = await updateAiLimit(value)
      setLimit(String(result.daily_limit))
      setData((previous) => (previous ? { ...previous, daily_limit: result.daily_limit } : previous))
      setSaved(true)
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось сохранить')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page page--wide">
      <h1>ИИ</h1>
      <p className="lead">
        Встроенная генерация заданий: расход и дневной лимит. Токены ввода / вывода —
        то, за что платит школа; сумму в рублях видно в кабинете ИИ-сервиса.
      </p>

      {error !== '' && (
        <section className="alert alert--error">
          <p>{error}</p>
        </section>
      )}

      {data === null && error === '' && <p className="loading">Загружаем…</p>}

      {data !== null && (
        <>
          <section className="card">
            <h2>Настройка</h2>
            {data.enabled ? (
              <p className="muted">
                Генерация включена. Модель заданий: <code>{data.model}</code>, самопроверка:{' '}
                <code>{data.check_model}</code>.
              </p>
            ) : (
              <p className="muted">
                Генерация выключена: на сервере не задан ключ <code>AI_API_KEY</code>.
                Учителям доступен только ручной путь — копирование промта.
              </p>
            )}

            <form onSubmit={handleSave}>
              <label className="label label--spaced" htmlFor="ai-limit">
                Генераций в день на одного учителя
              </label>
              <div className="row row--tight">
                <input
                  id="ai-limit"
                  className="input input--short"
                  type="number"
                  min={0}
                  max={1000}
                  value={limit}
                  onChange={(event) => {
                    setLimit(event.target.value)
                    setSaved(false)
                  }}
                />
                <button type="submit" className="btn btn--primary" disabled={busy}>
                  {busy ? 'Сохраняем…' : 'Сохранить'}
                </button>
              </div>
              <p className="hint">
                Считается каждое нажатие «Сгенерировать варианты». Повтор неудавшегося
                варианта и замена одного задания в лимит не входят, но видны в расходе.
                0 — генерация выключена для всех.
              </p>
              {saved && <p className="notice">Лимит сохранён.</p>}
            </form>
          </section>

          <section className="card">
            <h2>Расход за сегодня</h2>
            <UsageTiles title="Генерация заданий" usage={data.today.generate} />
            <UsageTiles title="Самопроверка ответов" usage={data.today.check} />
          </section>

          <section className="card">
            <h2>Расход за месяц</h2>
            <UsageTiles title="Генерация заданий" usage={data.month.generate} />
            <UsageTiles title="Самопроверка ответов" usage={data.month.check} />
          </section>

          <section className="card">
            <h2>По учителям</h2>
            {data.by_teacher.length === 0 ? (
              <p className="empty">В этом месяце генерацией ещё никто не пользовался.</p>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Учитель</th>
                      <th>Генераций сегодня</th>
                      <th>Генерация сегодня, ввод / вывод</th>
                      <th>Проверка сегодня, ввод / вывод</th>
                      <th>Генераций за месяц</th>
                      <th>Генерация за месяц</th>
                      <th>Проверка за месяц</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_teacher.map((row) => (
                      <tr key={row.id}>
                        <td title={row.email}>{row.full_name}</td>
                        <td>
                          {row.jobs_today} из {data.daily_limit}
                        </td>
                        <td>{tokens(row.generate_day)}</td>
                        <td>{tokens(row.check_day)}</td>
                        <td>{row.jobs_month}</td>
                        <td>{tokens(row.generate_month)}</td>
                        <td>{tokens(row.check_month)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  )
}
