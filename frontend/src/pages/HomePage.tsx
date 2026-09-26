/**
 * Главная страница — рабочий стол учителя.
 *
 * Здесь нет служебной информации вроде состояния бэкенда: она нужна разработчику,
 * а не учителю (проверка осталась на /health у бэкенда).
 *
 * Список «Мои контрольные» берётся из localStorage этого браузера. Авторизации
 * в сервисе пока нет, поэтому связать контрольные с учителем на сервере нельзя —
 * это осознанное ограничение, и на странице оно подписано честно.
 */

import { useState } from 'react'
import { Link } from 'react-router'
import { loadMyTests, forgetTest } from '../lib/myTestsStorage'
import type { MyTest } from '../lib/myTestsStorage'

/** Дата без времени: 26.09.2026 */
function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export default function HomePage() {
  // Читаем localStorage один раз при первом показе страницы.
  const [myTests, setMyTests] = useState<MyTest[]>(() => loadMyTests())
  // Код контрольной, ссылку которой только что скопировали.
  const [copiedCode, setCopiedCode] = useState('')

  async function handleCopyStudentLink(test: MyTest) {
    const url = `${window.location.origin}/t/${test.code}`
    try {
      await navigator.clipboard.writeText(url)
      setCopiedCode(test.code)
      window.setTimeout(() => setCopiedCode(''), 2000)
    } catch {
      // Буфер обмена недоступен — открываем ссылку, пусть учитель скопирует из адресной строки.
      window.prompt('Скопируйте ссылку для учеников:', url)
    }
  }

  function handleForget(test: MyTest) {
    const confirmed = window.confirm(
      `Убрать «${test.title}» из списка?\n\n` +
        'Сама контрольная и результаты останутся на сервере, но ссылки на них ' +
        'исчезнут из этого браузера — восстановить их будет негде.',
    )
    if (!confirmed) {
      return
    }

    forgetTest(test.code)
    setMyTests(loadMyTests())
  }

  return (
    <main className="page">
      <section className="hero">
        <h1>Контрольные работы</h1>
        <p className="hero__text">
          Составьте контрольную с выбором ответа — вручную или с помощью ИИ, — отправьте
          классу ссылку и получите готовую таблицу результатов с выгрузкой в Excel.
        </p>
        <Link className="btn btn--primary btn--large" to="/create">
          Создать контрольную
        </Link>
      </section>

      <section className="card">
        <div className="card__head">
          <h2>Мои контрольные</h2>
          {myTests.length > 0 && <span className="muted">{myTests.length}</span>}
        </div>

        {myTests.length === 0 ? (
          <p className="empty">
            Здесь появятся созданные контрольные со ссылками на результаты.
          </p>
        ) : (
          <ul className="testlist">
            {myTests.map((test) => (
              <li key={test.code} className="testcard">
                <div className="testcard__main">
                  <p className="testcard__title">{test.title}</p>
                  <p className="testcard__meta">
                    {formatDate(test.createdAt)} · вопросов: {test.questionsCount} · код{' '}
                    <code>{test.code}</code>
                  </p>
                </div>

                <div className="testcard__actions">
                  <a className="btn btn--small btn--primary" href={`/r/${test.resultsToken}`}>
                    Результаты
                  </a>
                  <button
                    type="button"
                    className="btn btn--small btn--ghost"
                    onClick={() => handleCopyStudentLink(test)}
                  >
                    {copiedCode === test.code ? 'Скопировано' : 'Ссылка ученикам'}
                  </button>
                  <button
                    type="button"
                    className="iconbtn"
                    title="Убрать из списка"
                    aria-label={`Убрать «${test.title}» из списка`}
                    onClick={() => handleForget(test)}
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <p className="hint">
          Список хранится в этом браузере: в другом браузере или на другом компьютере его
          не будет. Ссылки на результаты лучше дополнительно сохранить в закладки.
        </p>
      </section>
    </main>
  )
}
