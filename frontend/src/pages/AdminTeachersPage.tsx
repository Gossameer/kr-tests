/**
 * Админка → Учителя: /admin/teachers
 *
 * Здесь видно, кто пользуется сервисом, и можно закрыть доступ уволившемуся
 * или выдать временный пароль тому, кто свой забыл.
 */

import { useCallback, useEffect, useState } from 'react'
import { fetchTeachers, resetTeacherPassword, setTeacherActive } from '../api'
import { useAuth } from '../lib/authContext'
import type { PasswordReset, TeacherRow } from '../types'
import { usePageTitle } from '../lib/usePageTitle'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; teachers: TeacherRow[] }
  | { kind: 'error'; message: string }

function formatDate(value: string | null): string {
  if (value === null) {
    return 'не входил'
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export default function AdminTeachersPage() {
  usePageTitle('Учителя')
  const { user } = useAuth()
  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)
  // Временный пароль показывается ОДИН раз — храним его только на экране.
  const [reset, setReset] = useState<PasswordReset | null>(null)

  const load = useCallback(() => {
    fetchTeachers()
      .then((teachers) => setLoading({ kind: 'ready', teachers }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить список',
        }),
      )
  }, [])

  useEffect(load, [load])

  async function handleToggle(teacher: TeacherRow) {
    const action = teacher.is_active ? 'Заблокировать' : 'Разблокировать'
    const confirmed = window.confirm(
      `${action} учётную запись «${teacher.full_name}»?\n\n` +
        (teacher.is_active
          ? 'Человек не сможет войти, но его проверочные работы и результаты сохранятся.'
          : 'Человек снова сможет входить в сервис.'),
    )
    if (!confirmed) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      await setTeacherActive(teacher.id, !teacher.is_active)
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось изменить доступ')
    } finally {
      setBusy(false)
    }
  }

  async function handleReset(teacher: TeacherRow) {
    const confirmed = window.confirm(
      `Сбросить пароль «${teacher.full_name}»?\n\n` +
        'Старый пароль перестанет работать сразу. Новый показывается один раз — ' +
        'запишите его и передайте лично.',
    )
    if (!confirmed) {
      return
    }

    setBusy(true)
    setActionError('')
    try {
      setReset(await resetTeacherPassword(teacher.id))
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : 'Не удалось сбросить пароль')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page page--wide">
      <h1>Учителя</h1>
      <p className="lead">
        Учётные записи школы. Регистрируются самостоятельно по школьному коду.
      </p>

      {actionError !== '' && (
        <section className="alert alert--error">
          <p>{actionError}</p>
        </section>
      )}

      {reset !== null && (
        <section className="card card--success">
          <h2>Временный пароль выдан</h2>
          <p className="muted">
            Для <b>{reset.email}</b>. Показывается один раз — на сервере хранится только
            зашифрованный отпечаток.
          </p>
          <p className="temppass">{reset.temporary_password}</p>
          <div className="row">
            <button type="button" className="btn btn--ghost" onClick={() => setReset(null)}>
              Я записал(а), закрыть
            </button>
          </div>
        </section>
      )}

      {loading.kind === 'loading' && <p className="loading">Загружаем список…</p>}

      {loading.kind === 'error' && (
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
      )}

      {loading.kind === 'ready' && (
        <section className="card">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>ФИО</th>
                  <th>Email</th>
                  <th>Роль</th>
                  <th>Регистрация</th>
                  <th>Последний вход</th>
                  <th>Работ</th>
                  <th>Работ</th>
                  <th>Доступ</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {loading.teachers.map((teacher) => (
                  <tr key={teacher.id}>
                    <td>{teacher.full_name}</td>
                    <td>{teacher.email}</td>
                    <td>{teacher.role === 'admin' ? 'админ' : 'учитель'}</td>
                    <td>{formatDate(teacher.created_at)}</td>
                    <td>{formatDate(teacher.last_login_at)}</td>
                    <td>{teacher.tests_count}</td>
                    <td>{teacher.attempts_count}</td>
                    <td>
                      <span
                        className={teacher.is_active ? 'badge badge--open' : 'badge badge--closed'}
                      >
                        {teacher.is_active ? 'активен' : 'заблокирован'}
                      </span>
                    </td>
                    <td>
                      <div className="row row--tight">
                        <button
                          type="button"
                          className="btn btn--small btn--ghost"
                          onClick={() => handleToggle(teacher)}
                          // Себя блокировать нельзя — сервер это тоже не разрешит.
                          disabled={busy || teacher.id === user?.id}
                        >
                          {teacher.is_active ? 'Заблокировать' : 'Разблокировать'}
                        </button>
                        <button
                          type="button"
                          className="btn btn--small btn--ghost"
                          onClick={() => handleReset(teacher)}
                          disabled={busy}
                        >
                          Сбросить пароль
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  )
}
