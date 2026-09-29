/**
 * Регистрация учителя: /register
 *
 * Школьный код — единственное, что отделяет учителей от посторонних: сервис
 * открыт по сети, и без кода завести учётку смог бы кто угодно. Код выдаёт
 * администратор школы.
 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useAuth } from '../lib/authContext'
import { usePageTitle } from '../lib/usePageTitle'

/** Те же требования, что и на сервере. */
const MIN_PASSWORD = 8

export default function RegisterPage() {
  usePageTitle('Регистрация учителя')
  const { signUp } = useAuth()
  const navigate = useNavigate()

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [schoolCode, setSchoolCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (fullName.trim() === '') {
      setError('Укажите фамилию, имя и отчество.')
      return
    }
    if (!email.includes('@')) {
      setError('Укажите email — он же будет логином.')
      return
    }
    if (password.length < MIN_PASSWORD) {
      setError(`Пароль должен быть не короче ${MIN_PASSWORD} символов.`)
      return
    }
    if (schoolCode.trim() === '') {
      setError('Введите школьный код — его выдаёт администратор.')
      return
    }

    setBusy(true)
    setError('')

    try {
      await signUp({
        full_name: fullName.trim(),
        email: email.trim(),
        password,
        school_code: schoolCode.trim(),
      })
      // Регистрация сразу выполняет вход — идём в кабинет.
      navigate('/', { replace: true })
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось зарегистрироваться')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page page--narrow">
      <h1>Регистрация учителя</h1>
      <p className="lead">Занимает минуту. Ученикам регистрироваться не нужно.</p>

      <section className="card">
        <form onSubmit={handleSubmit}>
          <label className="label" htmlFor="reg-name">
            Фамилия, имя, отчество
          </label>
          <input
            id="reg-name"
            className="input"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            placeholder="Иванова Анна Петровна"
            autoComplete="name"
          />
          <p className="hint">Это имя увидят ученики на странице проверочной работы.</p>

          <label className="label label--spaced" htmlFor="reg-email">
            Email
          </label>
          <input
            id="reg-email"
            className="input"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="ivanova@school.ru"
            autoComplete="username"
          />

          <label className="label label--spaced" htmlFor="reg-password">
            Пароль
          </label>
          <input
            id="reg-password"
            className="input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
          />
          <p className="hint">Не короче {MIN_PASSWORD} символов.</p>

          <label className="label label--spaced" htmlFor="reg-code">
            Школьный код
          </label>
          <input
            id="reg-code"
            className="input"
            value={schoolCode}
            onChange={(event) => setSchoolCode(event.target.value)}
            placeholder="его выдаёт администратор"
          />

          {error !== '' && <p className="field-error">{error}</p>}

          <div className="row">
            <button type="submit" className="btn btn--primary btn--wide" disabled={busy}>
              {busy ? 'Создаём…' : 'Зарегистрироваться'}
            </button>
          </div>
        </form>

        <p className="hint">
          Уже есть учётная запись? <Link to="/login">Войдите</Link>.
        </p>
      </section>
    </main>
  )
}
