/**
 * Админка → Настройки: /admin/settings
 *
 * Пока настройка одна — школьный код регистрации. Любой, кто его знает,
 * может завести себе учётку учителя, поэтому код стоит менять, когда он
 * «расходится» по школе.
 */

import { useEffect, useState } from 'react'
import { fetchSchoolSettings, updateSchoolSettings } from '../api'

export default function AdminSettingsPage() {
  const [code, setCode] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchSchoolSettings()
      .then((settings) => {
        setCode(settings.school_code)
        setLoaded(true)
      })
      .catch((problem: unknown) =>
        setError(problem instanceof Error ? problem.message : 'Не удалось загрузить настройки'),
      )
  }, [])

  async function handleSave(event: React.FormEvent) {
    event.preventDefault()

    setBusy(true)
    setError('')
    setSaved(false)

    try {
      const settings = await updateSchoolSettings(code.trim())
      setCode(settings.school_code)
      setSaved(true)
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось сохранить')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page page--narrow">
      <h1>Настройки школы</h1>

      {error !== '' && (
        <section className="alert alert--error">
          <p>{error}</p>
        </section>
      )}

      <section className="card">
        <h2>Школьный код регистрации</h2>
        <p className="muted">
          Учитель вводит его при регистрации. Смена кода не влияет на тех, кто уже
          зарегистрировался, — только на новые учётные записи.
        </p>

        <form onSubmit={handleSave}>
          <label className="label label--spaced" htmlFor="school-code">
            Код
          </label>
          <input
            id="school-code"
            className="input"
            value={code}
            onChange={(event) => {
              setCode(event.target.value)
              setSaved(false)
            }}
            disabled={!loaded}
            placeholder="ШКОЛА-2090"
          />
          <p className="hint">Не короче 4 символов. Регистр при проверке не важен.</p>

          {saved && <p className="notice">Код сохранён.</p>}

          <div className="row">
            <button type="submit" className="btn btn--primary" disabled={busy || !loaded}>
              {busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}
