/**
 * Страница «Создать контрольную».
 *
 * Порядок работы учителя:
 *   1. вставляет JSON из внешнего ИИ в большое поле;
 *   2. нажимает «Проверить» — JSON разбирается ЗДЕСЬ, в браузере, без запроса на сервер;
 *   3. смотрит превью: все вопросы с вариантами, правильный подсвечен зелёным;
 *   4. нажимает «Опубликовать» — только теперь данные уходят на бэкенд;
 *   5. получает ссылку для учеников и копирует её.
 */

import { useState } from 'react'
import { Link } from 'react-router'
import { createTest } from '../api'
import CopyLink from '../components/CopyLink'
import { parseTestJson } from '../lib/parseTestJson'
import type { CreatedTest, TestDraft } from '../types'

export default function CreateTestPage() {
  // Текст, который учитель вставил в поле.
  const [raw, setRaw] = useState('')
  // Разобранная контрольная. null — «ещё не проверяли» или «текст изменился».
  const [draft, setDraft] = useState<TestDraft | null>(null)
  // Ошибки: и от разбора JSON, и от бэкенда.
  const [errors, setErrors] = useState<string[]>([])
  // Результат публикации: id и код ссылки.
  const [created, setCreated] = useState<CreatedTest | null>(null)
  // true, пока идёт запрос на бэкенд — чтобы заблокировать кнопку.
  const [publishing, setPublishing] = useState(false)

  /**
   * Любая правка текста сбрасывает превью и результат.
   * Иначе можно проверить один JSON, а опубликовать другой.
   */
  function handleChange(value: string) {
    setRaw(value)
    setDraft(null)
    setErrors([])
    setCreated(null)
  }

  function handleCheck() {
    const result = parseTestJson(raw)

    if (result.ok) {
      setDraft(result.draft)
      setErrors([])
    } else {
      setDraft(null)
      setErrors(result.errors)
    }
  }

  async function handlePublish() {
    if (draft === null) {
      return
    }

    setPublishing(true)
    setErrors([])

    try {
      const result = await createTest(draft)
      setCreated(result)
    } catch (error: unknown) {
      setErrors([error instanceof Error ? error.message : 'Не удалось опубликовать'])
    } finally {
      setPublishing(false)
    }
  }

  // Ссылки собираем от адреса текущей страницы, чтобы они всегда были верными.
  const studentUrl = created ? `${window.location.origin}/t/${created.code}` : ''
  // Секретная ссылка на результаты:длинный токен вместо пароля, пока входа для учителя нет.
  const resultsUrl = created ? `${window.location.origin}/r/${created.results_token}` : ''

  function handleReset() {
    setRaw('')
    setDraft(null)
    setErrors([])
    setCreated(null)
  }

  return (
    <main className="page">
      <p>
        <Link className="backlink" to="/">
          ← На главную
        </Link>
      </p>

      <h1>Создать контрольную</h1>
      <p className="lead">
        Вставьте JSON с вопросами, нажмите «Проверить» и глазами сверьте правильные ответы —
        ИИ иногда ошибается. Публикуется только то, что вы проверили.
      </p>

      {/* ------------------------- Поле для JSON ------------------------- */}
      <section className="card">
        <label className="label" htmlFor="json-input">
          JSON с вопросами
        </label>
        <textarea
          id="json-input"
          className="textarea"
          value={raw}
          onChange={(event) => handleChange(event.target.value)}
          placeholder={'{\n  "title": "Название",\n  "questions": [\n    { "text": "Вопрос", "options": ["A", "B"], "correct": 0 }\n  ]\n}'}
          spellCheck={false}
          rows={14}
          disabled={created !== null}
        />

        <div className="row">
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleCheck}
            disabled={created !== null}
          >
            Проверить
          </button>

          {(draft !== null || errors.length > 0 || raw !== '') && (
            <button type="button" className="btn btn--ghost" onClick={handleReset}>
              Очистить
            </button>
          )}
        </div>
      </section>

      {/* --------------------------- Ошибки ---------------------------- */}
      {errors.length > 0 && (
        <section className="alert alert--error">
          <h2>Так публиковать нельзя</h2>
          <ul>
            {errors.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </section>
      )}

      {/* --------------------------- Превью ---------------------------- */}
      {draft !== null && created === null && (
        <section className="card">
          <h2>Превью: {draft.title}</h2>
          <p className="muted">
            Вопросов: {draft.questions.length}. Правильный ответ подсвечен зелёным и помечен
            галочкой — проверьте каждый.
          </p>

          <ol className="questions">
            {draft.questions.map((question, questionIndex) => (
              <li key={questionIndex} className="question">
                <p className="question__text">{question.text}</p>
                <ul className="options">
                  {question.options.map((option, optionIndex) => {
                    const isCorrect = optionIndex === question.correct
                    return (
                      <li
                        key={optionIndex}
                        className={isCorrect ? 'option option--correct' : 'option'}
                      >
                        {/* Буква варианта: 0 → А, 1 → Б и так далее. */}
                        <span className="option__letter">
                          {'АБВГДЕЖЗИК'[optionIndex] ?? optionIndex + 1}
                        </span>
                        <span className="option__text">{option}</span>
                        {/* Не только цвет: галочка и подпись — чтобы было видно всем. */}
                        {isCorrect && <span className="option__mark">✓ верный</span>}
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ol>

          <div className="row">
            <button
              type="button"
              className="btn btn--primary"
              onClick={handlePublish}
              disabled={publishing}
            >
              {publishing ? 'Публикуем…' : 'Опубликовать'}
            </button>
            <span className="muted">После публикации появится ссылка для учеников.</span>
          </div>
        </section>
      )}

      {/* ------------------------- Результат --------------------------- */}
      {created !== null && (
        <section className="card card--success">
          <h2>Контрольная опубликована</h2>
          <p className="muted">
            «{created.title}», вопросов: {created.questions_count}. Код: <code>{created.code}</code>
          </p>

          <CopyLink
            label="Для учеников"
            url={studentUrl}
            hint="Эту ссылку отправьте классу — по ней открывается сам тест."
          />

          <CopyLink
            secret
            label="Результаты — только для вас, не отправляйте ученикам"
            url={resultsUrl}
            hint={
              'По этой ссылке видны все работы и правильные ответы. ' +
              'Сохраните её в закладки: заново её показать негде.'
            }
          />

          <p className="row">
            <a className="btn btn--ghost" href={resultsUrl}>
              Открыть результаты
            </a>
          </p>

          <div className="row">
            <button type="button" className="btn btn--ghost" onClick={handleReset}>
              Создать ещё одну
            </button>
          </div>
        </section>
      )}
    </main>
  )
}
