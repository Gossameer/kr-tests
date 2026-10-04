/**
 * Админка → Учителя: /admin/teachers
 *
 * Учётные записи заводит администратор: вставляет список «ФИО — email»
 * (например, два столбца из Excel), проверяет его и отправляет приглашения.
 * Учитель переходит по ссылке из письма и сам задаёт пароль.
 *
 * Здесь же видно, кто уже активировал учётку, а кому приглашение надо
 * повторить, можно отключить уволившегося и посмотреть журнал писем.
 *
 * Если почта на сервере не настроена, письма не уходят — тогда ссылки
 * администратор копирует отсюда и передаёт учителям сам.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  fetchMailOverview,
  fetchTeachers,
  importTeachers,
  invitePending,
  inviteTeacher,
  previewTeacherImport,
  resetTeacherPassword,
  setTeacherActive,
} from '../api'
import { useAuth } from '../lib/authContext'
import { plural } from '../lib/checklist'
import { usePageTitle } from '../lib/usePageTitle'
import type {
  ImportPreview,
  ImportResult,
  ImportRow,
  InviteLink,
  MailOverview,
  MailStatus,
  PasswordReset,
  TeacherRow,
  TeacherState,
} from '../types'

type Loading =
  | { kind: 'loading' }
  | { kind: 'ready'; teachers: TeacherRow[] }
  | { kind: 'error'; message: string }

const STATE_NAMES: Record<TeacherState, string> = {
  invited: 'приглашён',
  invite_expired: 'приглашение истекло',
  active: 'активен',
  disabled: 'отключён',
}

const STATE_BADGES: Record<TeacherState, string> = {
  invited: 'badge badge--wait',
  invite_expired: 'badge badge--closed',
  active: 'badge badge--open',
  disabled: 'badge badge--muted',
}

const IMPORT_STATUS: Record<ImportRow['status'], { name: string; badge: string }> = {
  new: { name: 'новый', badge: 'badge badge--open' },
  exists: { name: 'уже есть', badge: 'badge badge--muted' },
  duplicate: { name: 'дубль в списке', badge: 'badge badge--wait' },
  invalid: { name: 'ошибка', badge: 'badge badge--closed' },
}

/** Коротко — для таблицы учителей. */
const MAIL_STATUS_SHORT: Record<MailStatus, string> = {
  queued: 'письмо отправляется',
  sent: 'письмо отправлено',
  failed: 'письмо не ушло',
  test: 'письмо не отправлено',
}

const MAIL_STATUS: Record<MailStatus, string> = {
  queued: 'отправляется',
  sent: 'отправлено',
  failed: 'не ушло',
  test: 'не отправлено: почта не настроена',
}

function formatDate(value: string | null, empty = '—'): string {
  if (value === null) {
    return empty
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function formatDateTime(value: string | null): string {
  if (value === null) {
    return '—'
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
}

/** Копирует текст; если браузер не дал — показывает его в окне, чтобы скопировать вручную. */
async function copyText(text: string, promptTitle: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    window.prompt(promptTitle, text)
  }
}

export default function AdminTeachersPage() {
  usePageTitle('Учителя')
  const { user } = useAuth()
  const [loading, setLoading] = useState<Loading>({ kind: 'loading' })
  const [mail, setMail] = useState<MailOverview | null>(null)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  // Временный пароль показывается ОДИН раз — храним его только на экране.
  const [reset, setReset] = useState<PasswordReset | null>(null)

  // --- Добавление списком ---
  const [importOpen, setImportOpen] = useState(false)
  const [listText, setListText] = useState('')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [importError, setImportError] = useState('')
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  // Ссылки, выданные только что (после создания или массовой отправки):
  // показываем, когда почта не настроена и передавать их надо вручную.
  const [links, setLinks] = useState<InviteLink[]>([])

  const load = useCallback(() => {
    fetchTeachers()
      .then((teachers) => setLoading({ kind: 'ready', teachers }))
      .catch((error: unknown) =>
        setLoading({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Не удалось загрузить список',
        }),
      )
    // Журнал писем — дополнение: если он не загрузился, список учителей всё равно нужен.
    fetchMailOverview()
      .then(setMail)
      .catch(() => setMail(null))
  }, [])

  useEffect(load, [load])

  const mailConfigured = mail?.configured ?? false
  const teachers = loading.kind === 'ready' ? loading.teachers : []
  const pendingCount = teachers.filter(
    (teacher) => teacher.state === 'invited' || teacher.state === 'invite_expired',
  ).length

  /** Общая обёртка действий: блокирует кнопки, показывает ошибку, обновляет список. */
  async function run(action: () => Promise<string | void>, fallback: string) {
    setBusy(true)
    setActionError('')
    setNotice('')
    try {
      const message = await action()
      if (message) {
        setNotice(message)
      }
      load()
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : fallback)
    } finally {
      setBusy(false)
    }
  }

  // ---------------------------------------------------------------- список

  async function handlePreview() {
    if (listText.trim() === '') {
      setImportError('Вставьте список: в каждой строке ФИО и email.')
      return
    }
    setBusy(true)
    setImportError('')
    try {
      setPreview(await previewTeacherImport(listText))
    } catch (error: unknown) {
      setPreview(null)
      setImportError(error instanceof Error ? error.message : 'Не удалось проверить список')
    } finally {
      setBusy(false)
    }
  }

  async function handleImport() {
    if (preview === null || preview.summary.new === 0) {
      return
    }
    setBusy(true)
    setImportError('')
    try {
      const result = await importTeachers(listText)
      setImportResult(result)
      setLinks(result.mail_configured ? [] : result.created)
      setPreview(null)
      setListText('')
      load()
    } catch (error: unknown) {
      setImportError(error instanceof Error ? error.message : 'Не удалось создать учётные записи')
    } finally {
      setBusy(false)
    }
  }

  // ---------------------------------------------------------------- приглашения

  function handleResend(teacher: TeacherRow) {
    void run(async () => {
      const link = await inviteTeacher(teacher.id, true)
      if (!mailConfigured) {
        setLinks([link])
        return (
          `Почта не настроена — письмо для «${teacher.full_name}» не отправлено. ` +
          'Скопируйте ссылку ниже и передайте её сами.'
        )
      }
      return `Приглашение для «${teacher.full_name}» отправляется на ${teacher.email}. Прежняя ссылка больше не работает.`
    }, 'Не удалось отправить приглашение')
  }

  function handleCopyLink(teacher: TeacherRow) {
    void run(async () => {
      const link = await inviteTeacher(teacher.id, false)
      await copyText(link.invite_url, `Ссылка-приглашение для «${teacher.full_name}»:`)
      return (
        `Ссылка для «${teacher.full_name}» скопирована — действует до ` +
        `${formatDateTime(link.expires_at)}. Прежняя ссылка этого учителя больше не работает.`
      )
    }, 'Не удалось получить ссылку')
  }

  function handleResendAll() {
    const confirmed = window.confirm(
      `Отправить приглашение ещё раз всем, кто не задал пароль (${pendingCount})?\n\n` +
        'Ссылки из прежних писем перестанут работать — действовать будут только новые.',
    )
    if (!confirmed) {
      return
    }
    void run(async () => {
      const result = await invitePending()
      const count = result.invited.length
      if (!mailConfigured) {
        setLinks(result.invited)
        return `Почта не настроена — письма не отправлены. Новые ссылки (${count}) — ниже, передайте их сами.`
      }
      return `Приглашения отправляются: ${count} ${plural(count, 'письмо', 'письма', 'писем')}.`
    }, 'Не удалось отправить приглашения')
  }

  // ---------------------------------------------------------------- доступ

  function handleToggle(teacher: TeacherRow) {
    const action = teacher.is_active ? 'Отключить' : 'Включить'
    const confirmed = window.confirm(
      `${action} учётную запись «${teacher.full_name}»?\n\n` +
        (teacher.is_active
          ? 'Человек не сможет войти, но его проверочные работы и результаты сохранятся.'
          : 'Человек снова сможет входить в сервис.'),
    )
    if (!confirmed) {
      return
    }
    void run(async () => {
      await setTeacherActive(teacher.id, !teacher.is_active)
    }, 'Не удалось изменить доступ')
  }

  function handleReset(teacher: TeacherRow) {
    const confirmed = window.confirm(
      `Сбросить пароль «${teacher.full_name}»?\n\n` +
        'Старый пароль перестанет работать сразу. Новый показывается один раз — ' +
        'запишите его и передайте лично.',
    )
    if (!confirmed) {
      return
    }
    void run(async () => {
      setReset(await resetTeacherPassword(teacher.id))
    }, 'Не удалось сбросить пароль')
  }

  return (
    <main className="page page--wide">
      <h1>Учителя</h1>
      <p className="lead">
        Учётные записи заводит администратор: добавьте учителей списком — каждому придёт
        приглашение со ссылкой, по которой он сам задаст пароль.
      </p>

      {mail !== null && !mail.configured && (
        <section className="alert alert--warn" id="mail-off">
          <p>
            <b>Почта не настроена — ссылки копируйте вручную.</b> Письма не отправляются:
            у каждого приглашённого есть кнопка «Скопировать ссылку» — передайте ссылку
            учителю сами (в мессенджере или лично).
          </p>
        </section>
      )}

      {actionError !== '' && (
        <section className="alert alert--error">
          <p>{actionError}</p>
        </section>
      )}

      {notice !== '' && <p className="notice">{notice}</p>}

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

      {/* ------------------------- Только что выданные ссылки ------------------------- */}
      {links.length > 0 && (
        <section className="card card--success" id="fresh-links">
          <div className="card__head">
            <h2>Ссылки-приглашения</h2>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => setLinks([])}>
              Закрыть
            </button>
          </div>
          <p className="muted">
            Передайте каждому учителю его ссылку. Она действует 7 дней и срабатывает один раз.
            Закроете этот блок — ссылку можно получить заново кнопкой «Скопировать ссылку».
          </p>
          <div className="table-scroll">
            <table className="table">
              <tbody>
                {links.map((link) => (
                  <tr key={link.id}>
                    <td>{link.full_name}</td>
                    <td>
                      <code className="linkcode">{link.invite_url}</code>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--small btn--ghost"
                        onClick={() =>
                          void copyText(link.invite_url, `Ссылка для «${link.full_name}»:`)
                        }
                      >
                        Скопировать
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {links.length > 1 && (
            <div className="row">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() =>
                  void copyText(
                    links.map((link) => `${link.full_name}\t${link.email}\t${link.invite_url}`).join('\n'),
                    'Все ссылки:',
                  )
                }
              >
                Скопировать все (для вставки в Excel)
              </button>
            </div>
          )}
        </section>
      )}

      {/* ------------------------- Добавить списком ------------------------- */}
      <section className="card">
        <div className="card__head">
          <h2>Добавить списком</h2>
          <button
            type="button"
            className="btn btn--primary"
            id="import-toggle"
            onClick={() => setImportOpen((value) => !value)}
            aria-expanded={importOpen}
          >
            {importOpen ? 'Свернуть' : 'Добавить списком'}
          </button>
        </div>

        {importResult !== null && (
          <p className="notice" id="import-result">
            Создано учётных записей: {importResult.created.length}.{' '}
            {importResult.created.length === 0
              ? 'Новых адресов в списке не было.'
              : importResult.mail_configured
                ? 'Приглашения отправляются — примерно по одному письму в секунду. Статус каждого виден в таблице ниже.'
                : 'Почта не настроена, поэтому письма не отправлены — ссылки для учителей выше.'}
            {importResult.skipped.length > 0 && ` Пропущено строк: ${importResult.skipped.length}.`}
          </p>
        )}

        {importOpen && (
          <>
            <label className="label label--spaced" htmlFor="import-text">
              Список учителей
            </label>
            <textarea
              id="import-text"
              className="textarea textarea--question"
              rows={8}
              value={listText}
              onChange={(event) => {
                setListText(event.target.value)
                // Список изменился — прежняя проверка уже не про него.
                setPreview(null)
                setImportError('')
              }}
              placeholder={'Иванова Анна Петровна\tivanova@school.ru\nПетров Пётр Ильич; petrov@school.ru'}
              spellCheck={false}
              aria-describedby="import-text-hint"
            />
            <p className="hint" id="import-text-hint">
              Одна строка — один учитель: ФИО и email через табуляцию, точку с запятой или
              запятую. Проще всего выделить два столбца в Excel и вставить сюда. Пустые
              строки пропустим.
            </p>

            {importError !== '' && <p className="field-error">{importError}</p>}

            <div className="row row--tight">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void handlePreview()}
                disabled={busy}
              >
                Проверить
              </button>
              <span className="hint">Пока вы не подтвердите, ничего не создаётся.</span>
            </div>

            {preview !== null && (
              <div className="importpreview" id="import-preview">
                <p className="muted">
                  Новых: <b>{preview.summary.new}</b> · уже есть: {preview.summary.exists} ·
                  дублей в списке: {preview.summary.duplicate} · с ошибкой:{' '}
                  {preview.summary.invalid}
                </p>
                <div className="table-scroll">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Строка</th>
                        <th>ФИО</th>
                        <th>Email</th>
                        <th>Что будет</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((row) => (
                        <tr key={row.line}>
                          <td>{row.line}</td>
                          <td>{row.full_name || '—'}</td>
                          <td>{row.email || '—'}</td>
                          <td>
                            <span className={IMPORT_STATUS[row.status].badge}>
                              {IMPORT_STATUS[row.status].name}
                            </span>
                            {row.message !== '' && <span className="hint"> {row.message}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {preview.summary.new === 0 ? (
                  <p className="field-error">
                    Создавать некого: новых адресов в списке нет. Исправьте строки с ошибками
                    и нажмите «Проверить» ещё раз.
                  </p>
                ) : (
                  <div className="row row--tight">
                    <button
                      type="button"
                      className="btn btn--primary"
                      id="import-create"
                      onClick={() => void handleImport()}
                      disabled={busy}
                    >
                      {busy
                        ? 'Создаём…'
                        : `Создать и отправить приглашения (${preview.summary.new})`}
                    </button>
                    <span className="hint">
                      Будут созданы только «новые» строки.
                      {!mailConfigured && ' Почта не настроена — вместо писем получите ссылки.'}
                    </span>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </section>

      {loading.kind === 'loading' && <p className="loading">Загружаем список…</p>}

      {loading.kind === 'error' && (
        <section className="alert alert--error">
          <p>{loading.message}</p>
        </section>
      )}

      {/* ------------------------- Список ------------------------- */}
      {loading.kind === 'ready' && (
        <section className="card">
          <div className="card__head">
            <h2>Учётные записи</h2>
            {pendingCount > 0 && (
              <button
                type="button"
                className="btn btn--ghost"
                id="resend-all"
                onClick={handleResendAll}
                disabled={busy}
              >
                Отправить ещё раз всем, кто не активировал ({pendingCount})
              </button>
            )}
          </div>

          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Учитель</th>
                  <th>Статус</th>
                  <th>Приглашение</th>
                  <th>Активность</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {teachers.map((teacher) => {
                  const waiting =
                    teacher.state === 'invited' || teacher.state === 'invite_expired'
                  return (
                    <tr key={teacher.id}>
                      <td>
                        {teacher.full_name}
                        {teacher.role === 'admin' && <span className="tag">админ</span>}
                        <span className="cellsub">{teacher.email}</span>
                      </td>
                      <td>
                        <span className={STATE_BADGES[teacher.state]}>
                          {STATE_NAMES[teacher.state]}
                        </span>
                      </td>
                      <td>
                        {/* Про письмо говорим, пока учётка не активирована: потом это уже история. */}
                        {teacher.invite_sent_at === null || teacher.state === 'active' ? (
                          '—'
                        ) : (
                          <>
                            {formatDateTime(teacher.invite_sent_at)}
                            {teacher.invite_mail_status !== null && (
                              <span
                                className={
                                  'cellsub' +
                                  (teacher.invite_mail_status === 'failed' ? ' hint--bad' : '')
                                }
                                title={teacher.invite_mail_error || undefined}
                              >
                                {MAIL_STATUS_SHORT[teacher.invite_mail_status]}
                              </span>
                            )}
                          </>
                        )}
                      </td>
                      <td>
                        {teacher.last_login_at === null
                          ? 'не входил'
                          : `вход ${formatDate(teacher.last_login_at)}`}
                        <span className="cellsub">
                          работ: {teacher.tests_count} · сдано: {teacher.attempts_count}
                        </span>
                      </td>
                      <td>
                        <div className="rowactions">
                          {waiting && (
                            <>
                              <button
                                type="button"
                                className="btn btn--small btn--ghost"
                                onClick={() => handleResend(teacher)}
                                disabled={busy}
                              >
                                Отправить ещё раз
                              </button>
                              <button
                                type="button"
                                className="btn btn--small btn--ghost"
                                onClick={() => handleCopyLink(teacher)}
                                disabled={busy}
                                title="Выдаёт новую ссылку — прежняя перестаёт работать"
                              >
                                Скопировать ссылку
                              </button>
                            </>
                          )}
                          {teacher.state === 'active' && (
                            <button
                              type="button"
                              className="btn btn--small btn--ghost"
                              onClick={() => handleReset(teacher)}
                              disabled={busy}
                            >
                              Сбросить пароль
                            </button>
                          )}
                          <button
                            type="button"
                            className="btn btn--small btn--ghost"
                            onClick={() => handleToggle(teacher)}
                            // Себя отключать нельзя — сервер это тоже не разрешит.
                            disabled={busy || teacher.id === user?.id}
                          >
                            {teacher.is_active ? 'Отключить' : 'Включить'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <p className="hint">
            «Приглашён» — письмо отправлено, пароль ещё не задан. Приглашение действует 7
            дней; «Отправить ещё раз» и «Скопировать ссылку» выдают новое, а прежнее
            перестаёт работать.
          </p>
        </section>
      )}

      {/* ------------------------- Журнал писем ------------------------- */}
      {mail !== null && (
        <section className="card">
          <details className="details" id="mail-log">
            <summary>Журнал писем ({mail.log.length})</summary>
            {mail.configured && (
              <p className="muted">Письма уходят с адреса {mail.from_address || '—'}.</p>
            )}
            {mail.log.length === 0 ? (
              <p className="empty">Писем пока не было. Они появятся после первого приглашения.</p>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th>Кому</th>
                      <th>Письмо</th>
                      <th>Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mail.log.map((entry) => (
                      <tr key={entry.id}>
                        <td>{formatDateTime(entry.created_at)}</td>
                        <td>
                          {entry.full_name ? `${entry.full_name}, ` : ''}
                          {entry.to_email}
                        </td>
                        <td>{entry.kind === 'invite' ? 'приглашение' : 'сброс пароля'}</td>
                        <td>
                          {MAIL_STATUS[entry.status]}
                          {entry.error !== '' && (
                            <span className="hint hint--bad"> — {entry.error}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </details>
        </section>
      )}
    </main>
  )
}
