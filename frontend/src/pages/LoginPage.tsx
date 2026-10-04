/**
 * Вход в сервис: /login
 *
 * После входа возвращаем человека туда, куда он шёл. Адрес приходит в state
 * от ProtectedRoute — иначе учитель, нажавший «Результаты» из закладки,
 * попадал бы на главную и искал проверочную работу заново.
 */

import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { fetchAuthOptions } from '../api'
import { useAuth } from '../lib/authContext'
import { usePageTitle } from '../lib/usePageTitle'

export default function LoginPage() {
  usePageTitle('Вход')
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // Регистрацию по школьному коду администратор может выключить — тогда
  // ссылки на неё здесь нет. Пока ответ не пришёл, ссылку не показываем.
  const [selfRegistration, setSelfRegistration] = useState(false)

  useEffect(() => {
    fetchAuthOptions()
      .then((options) => setSelfRegistration(options.self_registration))
      .catch(() => setSelfRegistration(false))
  }, [])

  // Куда вернуться после входа.
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()

    if (email.trim() === '' || password === '') {
      setError('Введите email и пароль.')
      return
    }

    setBusy(true)
    setError('')

    try {
      await signIn(email.trim(), password)
      navigate(from, { replace: true })
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось войти')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page page--narrow">
      <h1>Вход для учителя</h1>
      <p className="lead">Ученикам вход не нужен — они открывают проверочную работу по ссылке.</p>

      <section className="card">
        {/* form, а не просто кнопка: срабатывает Enter и подхватывается
            менеджер паролей браузера. */}
        <form onSubmit={handleSubmit}>
          <label className="label" htmlFor="login-email">
            Email
          </label>
          <input
            id="login-email"
            className="input"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            placeholder="Например: ivanova@school.ru"
          />

          <label className="label label--spaced" htmlFor="login-password">
            Пароль
          </label>
          <input
            id="login-password"
            className="input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />

          {error !== '' && <p className="field-error">{error}</p>}

          <div className="row">
            <button type="submit" className="btn btn--primary btn--wide" disabled={busy}>
              {busy ? 'Входим…' : 'Войти'}
            </button>
          </div>
        </form>

        <p className="hint">
          <Link to="/forgot">Забыли пароль?</Link>
        </p>

        {selfRegistration ? (
          <p className="hint">
            Нет учётной записи? <Link to="/register">Зарегистрируйтесь</Link> — понадобится
            школьный код, его выдаёт администратор.
          </p>
        ) : (
          <p className="hint">
            Нет учётной записи? Её создаёт администратор школы — вам придёт письмо
            с приглашением.
          </p>
        )}
      </section>
    </main>
  )
}
