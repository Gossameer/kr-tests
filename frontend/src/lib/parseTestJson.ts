/**
 * Разбор и проверка вопросов контрольной.
 *
 * Здесь три задачи:
 *   1. вытащить JSON из ответа ИИ, даже если вокруг него текст или ```json;
 *   2. превратить его в наши объекты;
 *   3. проверить правила (те же, что на бэкенде) и выдать понятные ошибки.
 *
 * Бэкенд проверяет всё заново: фронтенду доверять нельзя, его легко обойти.
 * Проверка здесь нужна только чтобы учитель увидел ошибку сразу.
 */

import type { DraftQuestion } from '../types'

export const MAX_QUESTIONS = 100
export const MAX_OPTIONS = 10

/** Результат разбора вставленного текста. */
export type ParseResult =
  | { ok: true; title: string | null; questions: DraftQuestion[] }
  | { ok: false; errors: string[] }

/** Проверяет, что значение — непустая строка, и возвращает её без пробелов по краям. */
function cleanString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const cleaned = value.trim()
  return cleaned.length > 0 ? cleaned : null
}

/**
 * Достаёт JSON из ответа ИИ.
 *
 * ИИ часто отвечает так:
 *     Вот ваша контрольная:
 *     ```json
 *     { "title": ... }
 *     ```
 *     Готово!
 *
 * Поэтому сначала пробуем содержимое ```-блока, а если его нет — берём всё
 * от первой «{» до последней «}». Если и этого нет, возвращаем исходный текст:
 * пусть JSON.parse сам сообщит об ошибке.
 */
export function extractJsonBlock(raw: string): string {
  const text = raw.trim()

  // Блок в тройных кавычках, с «json» или без.
  const fenced = /```(?:json)?\s*([\s\S]*?)```/i.exec(text)
  if (fenced?.[1] !== undefined && fenced[1].trim() !== '') {
    return fenced[1].trim()
  }

  // Иначе — самый внешний объект { ... }.
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start !== -1 && end > start) {
    return text.slice(start, end + 1)
  }

  return text
}

/**
 * Проверяет список вопросов по правилам контрольной.
 * Возвращает все найденные ошибки — учителю удобнее исправить их сразу.
 */
export function validateQuestions(questions: DraftQuestion[]): string[] {
  const errors: string[] = []

  if (questions.length === 0) {
    errors.push('Добавьте хотя бы один вопрос.')
    return errors
  }
  if (questions.length > MAX_QUESTIONS) {
    errors.push(`Слишком много вопросов (${questions.length}), максимум ${MAX_QUESTIONS}.`)
  }

  questions.forEach((question, index) => {
    // Нумерация для человека — с единицы.
    const where = `Вопрос ${index + 1}`

    if (question.text.trim() === '') {
      errors.push(`${where}: не заполнен текст вопроса.`)
    }

    const options = question.options.map((option) => option.trim())

    if (options.length < 2) {
      errors.push(`${where}: нужно минимум 2 варианта ответа, а есть ${options.length}.`)
    }
    if (options.length > MAX_OPTIONS) {
      errors.push(`${where}: слишком много вариантов (${options.length}), максимум ${MAX_OPTIONS}.`)
    }
    if (options.some((option) => option === '')) {
      errors.push(`${where}: есть пустые варианты ответа.`)
    }
    if (new Set(options).size !== options.length) {
      errors.push(`${where}: варианты ответа повторяются.`)
    }
    if (!Number.isInteger(question.correct) || question.correct < 0) {
      errors.push(`${where}: не отмечен правильный вариант.`)
    } else if (question.correct >= options.length) {
      // Бывает и при загрузке из ИИ (correct больше числа вариантов),
      // и при наборе руками (правильный вариант удалили) — текст годится для обоих.
      errors.push(
        `${where}: правильным отмечен вариант №${question.correct + 1}, ` +
          `а вариантов всего ${options.length}.`,
      )
    }
  })

  return errors
}

/** Разбирает текст (обычно — ответ ИИ) в список вопросов. */
export function parseTestJson(raw: string): ParseResult {
  if (!raw.trim()) {
    return { ok: false, errors: ['Поле пустое — вставьте ответ ИИ.'] }
  }

  let data: unknown
  try {
    data = JSON.parse(extractJsonBlock(raw))
  } catch (error: unknown) {
    const details = error instanceof Error ? error.message : String(error)
    return {
      ok: false,
      errors: [
        'Не удалось прочитать JSON — проверьте запятые, кавычки и скобки.',
        `Подробности от браузера: ${details}`,
      ],
    }
  }

  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    return {
      ok: false,
      errors: ['Ожидается объект вида { "title": "...", "questions": [...] }.'],
    }
  }

  const source = data as Record<string, unknown>
  const errors: string[] = []

  // Название необязательно: учитель мог уже заполнить его в форме.
  const title = cleanString(source.title)

  const rawQuestions = source.questions
  if (!Array.isArray(rawQuestions)) {
    errors.push('В JSON нет списка «questions».')
    return { ok: false, errors }
  }

  // Сначала приводим к нашим типам, потом проверяем общими правилами.
  const questions: DraftQuestion[] = []

  rawQuestions.forEach((rawQuestion: unknown, index: number) => {
    const where = `Вопрос ${index + 1}`

    if (rawQuestion === null || typeof rawQuestion !== 'object' || Array.isArray(rawQuestion)) {
      errors.push(`${where}: должен быть объектом с полями text, options, correct.`)
      return
    }

    const question = rawQuestion as Record<string, unknown>
    const text = typeof question.text === 'string' ? question.text.trim() : ''

    if (!Array.isArray(question.options)) {
      errors.push(`${where}: поле «options» отсутствует или не является списком.`)
      return
    }

    const options = question.options.map((option: unknown) =>
      typeof option === 'string' ? option.trim() : '',
    )

    const correct = question.correct
    if (typeof correct !== 'number' || !Number.isInteger(correct)) {
      errors.push(`${where}: поле «correct» должно быть целым числом (номер с нуля).`)
      return
    }

    questions.push({ text, options, correct })
  })

  errors.push(...validateQuestions(questions))

  if (errors.length > 0) {
    return { ok: false, errors }
  }

  return { ok: true, title, questions }
}
