/**
 * Админка → Настройки: /admin/settings
 *
 * Две настройки: можно ли учителю зарегистрироваться самому и школьный код
 * для такой регистрации. По умолчанию самостоятельная регистрация выключена —
 * учётки заводит администратор в разделе «Учителя» и рассылает приглашения.
 */

import { useEffect, useState } from 'react'
import { fetchSchoolSettings, updateSchoolSettings } from '../api'
import { usePageTitle } from '../lib/usePageTitle'

export default function AdminSettingsPage() {
  usePageTitle('Настройки школы')
  const [code, setCode] = useState('')
  const [allowRegistration, setAllowRegistration] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchSchoolSettings()
      .then((settings) => {
        setCode(settings.school_code)
        setAllowRegistration(settings.allow_self_registration)
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
      const settings = await updateSchoolSettings(code.trim(), allowRegistration)
      setCode(settings.school_code)
      setAllowRegistration(settings.allow_self_registration)
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
        <h2>Самостоятельная регистрация</h2>
        <p className="muted">
          Обычно учётные записи создаёт администратор: раздел «Учителя» → «Добавить
          списком». Здесь можно дополнительно разрешить учителям регистрироваться самим
          по школьному коду.
        </p>

        <form onSubmit={handleSave}>
          <label className="check">
            <input
              id="allow-registration"
              type="checkbox"
              checked={allowRegistration}
              onChange={(event) => {
                setAllowRegistration(event.target.checked)
                setSaved(false)
              }}
              disabled={!loaded}
            />
            <span>
              Разрешить самостоятельную регистрацию
              <span className="hint">
                {' '}
                — {allowRegistration
                  ? 'любой, кто знает школьный код, сможет завести учётку учителя'
                  : 'на странице входа нет ссылки на регистрацию, зарегистрироваться самому нельзя'}
              </span>
            </span>
          </label>
          <p className="hint">Уже созданные учётные записи эта настройка не затрагивает.</p>

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
          <p className="hint">
            Не короче 4 символов. Регистр при проверке не важен.{' '}
            {allowRegistration
              ? 'Учитель вводит код при регистрации.'
              : 'Сейчас код не используется: регистрация выключена.'}
          </p>

          {saved && <p className="notice">Настройки сохранены.</p>}

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
