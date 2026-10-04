/**
 * «Забыли пароль?»: /forgot
 *
 * Учитель вводит email — сервер отправляет письмо со ссылкой на час.
 * Ответ всегда одинаковый, есть такой адрес или нет: иначе по этой форме
 * можно было бы узнавать, кто из учителей пользуется сервисом.
 */

import { useState } from 'react'
import { Link } from 'react-router'
import { forgotPassword } from '../api'
import { usePageTitle } from '../lib/usePageTitle'

export default function ForgotPasswordPage() {
  usePageTitle('Забыли пароль?')

  const [email, setEmail] = useState('')
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const [busy, setBusy] = useState(false)

  const emailError =
    email.trim() === ''
      ? 'Введите email, с которым вы входите в сервис.'
      : !/^\S+@\S+\.\S+$/.test(email.trim())
        ? 'Похоже, в адресе ошибка. Пример: ivanova@school.ru'
        : ''
  const shownEmailError = touched ? emailError : ''

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setTouched(true)
    if (emailError) {
      document.getElementById('forgot-email')?.focus()
      return
    }

    setBusy(true)
    setError('')
    try {
      const result = await forgotPassword(email.trim())
      setDone(result.message)
    } catch (problem: unknown) {
      setError(
        problem instanceof Error
          ? problem.message
          : 'Не получилось. Подождите минуту и попробуйте ещё раз.',
      )
    } finally {
      setBusy(false)
    }
  }

  if (done !== '') {
    return (
      <main className="page page--narrow">
        <h1>Проверьте почту</h1>
        <section className="card card--success">
          <p>{done}</p>
          <p className="muted">
            Письма нет даже в «Спаме»? Проверьте адрес — или попросите администратора школы
            выдать вам новый пароль.
          </p>
          <div className="row">
            <Link className="btn btn--primary" to="/login">
              На страницу входа
            </Link>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main className="page page--narrow">
      <h1>Забыли пароль?</h1>
      <p className="lead">Пришлём на почту ссылку — по ней вы зададите новый пароль.</p>

      <section className="card">
        <form onSubmit={handleSubmit}>
          <label className="label" htmlFor="forgot-email">
            Email
          </label>
          <input
            id="forgot-email"
            className={'input' + (shownEmailError ? ' input--invalid' : '')}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            onBlur={() => setTouched(true)}
            autoComplete="username"
            placeholder="Например: ivanova@school.ru"
            aria-invalid={shownEmailError !== ''}
            aria-describedby="forgot-email-hint"
          />
          {shownEmailError ? (
            <p className="field-error" id="forgot-email-hint">
              {shownEmailError}
            </p>
          ) : (
            <p className="hint" id="forgot-email-hint">
              Тот адрес, с которым вы входите в сервис. Ссылка действует один час.
            </p>
          )}

          {error !== '' && (
            <div className="alert alert--error">
              <p>{error}</p>
            </div>
          )}

          <div className="row">
            <button type="submit" className="btn btn--primary btn--wide" disabled={busy}>
              {busy ? 'Отправляем…' : 'Прислать ссылку'}
            </button>
          </div>
        </form>

        <p className="hint">
          Вспомнили пароль? <Link to="/login">Вернуться ко входу</Link>
        </p>
      </section>
    </main>
  )
}
