/**
 * «Задайте пароль»: /invite/:token и /reset/:token
 *
 * Одна страница на два случая: учитель пришёл по приглашению от администратора
 * или по ссылке «Забыли пароль?». Вид ссылки определяет сервер по самому токену,
 * адрес страницы на это не влияет.
 *
 * Ссылка одноразовая: после установки пароля учётка становится активной,
 * и человек сразу оказывается в своём кабинете.
 */

import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { fetchTokenInfo, setPasswordByToken } from '../api'
import { useAuth } from '../lib/authContext'
import { usePageTitle } from '../lib/usePageTitle'
import type { TokenInfo } from '../types'

/** Те же требования, что и на сервере (app/security.py). */
const MIN_PASSWORD = 8

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; info: TokenInfo }
  | { kind: 'error'; message: string }

export default function SetPasswordPage() {
  const { token = '' } = useParams()
  const { refresh } = useAuth()
  const navigate = useNavigate()

  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  // Ошибки под полями показываем после «Сохранить» или ухода из поля.
  const [touched, setTouched] = useState({ password: false, repeat: false })
  const [serverError, setServerError] = useState('')
  const [busy, setBusy] = useState(false)

  const isReset = loading.kind === 'ready' && loading.info.kind === 'reset'
  usePageTitle(isReset ? 'Новый пароль' : 'Задайте пароль')

  useEffect(() => {
    fetchTokenInfo(token)
      .then((info) => setLoading({ kind: 'ready', info }))
      .catch((problem: unknown) =>
        setLoading({
          kind: 'error',
          message:
            problem instanceof Error
              ? problem.message
              : 'Ссылка не открылась. Попросите администратора выслать приглашение заново.',
        }),
      )
  }, [token])

  const passwordError =
    password.length < MIN_PASSWORD
      ? `Пароль должен быть не короче ${MIN_PASSWORD} символов.`
      : password.trim() === ''
        ? 'Пароль не может состоять из одних пробелов.'
        : ''
  const repeatError =
    repeat === '' ? 'Введите пароль ещё раз.' : repeat !== password ? 'Пароли не совпадают.' : ''
  const shownPasswordError = touched.password ? passwordError : ''
  const shownRepeatError = touched.repeat ? repeatError : ''

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setTouched({ password: true, repeat: true })
    if (passwordError) {
      document.getElementById('new-password')?.focus()
      return
    }
    if (repeatError) {
      document.getElementById('new-password-repeat')?.focus()
      return
    }

    setBusy(true)
    setServerError('')
    try {
      await setPasswordByToken(token, password)
      // Сервер уже выполнил вход — подхватываем его и идём в кабинет.
      await refresh()
      navigate('/', { replace: true })
    } catch (problem: unknown) {
      setServerError(
        problem instanceof Error
          ? problem.message
          : 'Не удалось сохранить пароль. Подождите минуту и попробуйте ещё раз.',
      )
    } finally {
      setBusy(false)
    }
  }

  if (loading.kind === 'loading') {
    return (
      <main className="page page--narrow">
        <p className="loading">Проверяем ссылку…</p>
      </main>
    )
  }

  if (loading.kind === 'error') {
    return (
      <main className="page page--narrow">
        <h1>Ссылка не работает</h1>
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
        <div className="row">
          <Link className="btn btn--primary" to="/login">
            На страницу входа
          </Link>
          <Link className="btn btn--ghost" to="/forgot">
            Забыли пароль?
          </Link>
        </div>
      </main>
    )
  }

  const { info } = loading

  return (
    <main className="page page--narrow">
      <h1>{isReset ? 'Новый пароль' : 'Задайте пароль'}</h1>
      <p className="lead">
        {isReset
          ? 'Придумайте новый пароль — старый перестанет работать.'
          : 'Администратор школы завёл вам учётную запись. Придумайте пароль — и сразу попадёте в сервис.'}
      </p>

      <section className="card">
        <dl className="whois">
          <dt>Учётная запись</dt>
          <dd>{info.full_name}</dd>
          <dt>Email (он же логин)</dt>
          <dd>{info.email}</dd>
        </dl>

        <form onSubmit={handleSubmit}>
          {/* Скрытое поле с логином: менеджер паролей сохранит пару «email + пароль». */}
          <input
            type="email"
            value={info.email}
            readOnly
            hidden
            autoComplete="username"
            aria-hidden="true"
          />

          <label className="label label--spaced" htmlFor="new-password">
            Пароль
          </label>
          <input
            id="new-password"
            className={'input' + (shownPasswordError ? ' input--invalid' : '')}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onBlur={() => setTouched((value) => ({ ...value, password: true }))}
            autoComplete="new-password"
            aria-invalid={shownPasswordError !== ''}
            aria-describedby="new-password-hint"
          />
          {shownPasswordError ? (
            <p className="field-error" id="new-password-hint">
              {shownPasswordError}
            </p>
          ) : (
            <p className="hint" id="new-password-hint">
              Не короче {MIN_PASSWORD} символов. Запомните его — подсмотреть пароль потом нельзя.
            </p>
          )}

          <label className="label label--spaced" htmlFor="new-password-repeat">
            Пароль ещё раз
          </label>
          <input
            id="new-password-repeat"
            className={'input' + (shownRepeatError ? ' input--invalid' : '')}
            type="password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            onBlur={() => setTouched((value) => ({ ...value, repeat: true }))}
            autoComplete="new-password"
            aria-invalid={shownRepeatError !== ''}
            aria-describedby="new-password-repeat-hint"
          />
          {shownRepeatError ? (
            <p className="field-error" id="new-password-repeat-hint">
              {shownRepeatError}
            </p>
          ) : (
            <p className="hint" id="new-password-repeat-hint">
              Чтобы не ошибиться при наборе.
            </p>
          )}

          {serverError !== '' && (
            <div className="alert alert--error">
              <p>{serverError}</p>
            </div>
          )}

          <div className="row">
            <button type="submit" className="btn btn--primary btn--wide" disabled={busy}>
              {busy ? 'Сохраняем…' : 'Сохранить и войти'}
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}
