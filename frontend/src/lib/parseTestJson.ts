/**
 * Разбор и проверка JSON, который учитель вставляет в поле.
 *
 * Эти же правила проверяет бэкенд — здесь они нужны, чтобы учитель увидел
 * ошибку сразу, не дожидаясь запроса на сервер. Бэкенд остаётся последней
 * инстанцией: фронтенду доверять нельзя, его легко обойти.
 */

import type { DraftQuestion, TestDraft } from '../types'

/** Результат разбора: либо готовая контрольная, либо список ошибок. */
export type ParseResult =
  | { ok: true; draft: TestDraft }
  | { ok: false; errors: string[] }

const MAX_QUESTIONS = 100
const MAX_OPTIONS = 10

/** Проверяет, что значение — непустая строка, и возвращает её без пробелов по краям. */
function cleanString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const cleaned = value.trim()
  return cleaned.length > 0 ? cleaned : null
}

export function parseTestJson(raw: string): ParseResult {
  if (!raw.trim()) {
    return { ok: false, errors: ['Поле пустое — вставьте JSON с вопросами.'] }
  }

  // Шаг 1: превратить текст в объект. Тут ловим пропущенные запятые и кавычки.
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch (error: unknown) {
    const details = error instanceof Error ? error.message : String(error)
    return {
      ok: false,
      errors: [
        'Это не похоже на корректный JSON — проверьте запятые, кавычки и скобки.',
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
  // Собираем ВСЕ ошибки, а не только первую: учителю удобнее исправить всё сразу.
  const errors: string[] = []

  // Шаг 2: название.
  const title = cleanString(source.title)
  if (title === null) {
    errors.push('Поле «title» отсутствует или пустое — укажите название контрольной.')
  }

  // Шаг 3: список вопросов.
  const rawQuestions = source.questions
  if (!Array.isArray(rawQuestions)) {
    errors.push('Поле «questions» отсутствует или не является списком вопросов.')
    return { ok: false, errors }
  }
  if (rawQuestions.length === 0) {
    errors.push('Список «questions» пустой — нужен хотя бы один вопрос.')
  }
  if (rawQuestions.length > MAX_QUESTIONS) {
    errors.push(`Слишком много вопросов (${rawQuestions.length}), максимум ${MAX_QUESTIONS}.`)
  }

  const questions: DraftQuestion[] = []

  rawQuestions.forEach((rawQuestion: unknown, index: number) => {
    // Нумерация для человека — с единицы.
    const where = `Вопрос ${index + 1}`

    if (rawQuestion === null || typeof rawQuestion !== 'object' || Array.isArray(rawQuestion)) {
      errors.push(`${where}: должен быть объектом с полями text, options, correct.`)
      return
    }

    const question = rawQuestion as Record<string, unknown>

    const text = cleanString(question.text)
    if (text === null) {
      errors.push(`${where}: поле «text» отсутствует или пустое.`)
    }

    if (!Array.isArray(question.options)) {
      errors.push(`${where}: поле «options» отсутствует или не является списком.`)
      return
    }

    const options = question.options.map((option: unknown) => cleanString(option))
    if (options.some((option) => option === null)) {
      errors.push(`${where}: среди вариантов ответа есть пустые или не-текстовые.`)
      return
    }

    const cleanOptions = options as string[]

    if (cleanOptions.length < 2) {
      errors.push(
        `${where}: нужно минимум 2 варианта ответа, а указано ${cleanOptions.length}.`,
      )
    }
    if (cleanOptions.length > MAX_OPTIONS) {
      errors.push(
        `${where}: слишком много вариантов (${cleanOptions.length}), максимум ${MAX_OPTIONS}.`,
      )
    }
    if (new Set(cleanOptions).size !== cleanOptions.length) {
      errors.push(`${where}: есть одинаковые варианты ответа — похоже на ошибку ИИ.`)
    }

    // Главная проверка: correct должен указывать на существующий вариант.
    const correct = question.correct
    if (typeof correct !== 'number' || !Number.isInteger(correct)) {
      errors.push(`${where}: поле «correct» должно быть целым числом (номер с нуля).`)
    } else if (correct < 0 || correct >= cleanOptions.length) {
      errors.push(
        `${where}: correct = ${correct}, но вариантов ${cleanOptions.length} — ` +
          `допустимы значения от 0 до ${cleanOptions.length - 1} (нумерация с нуля).`,
      )
    } else if (text !== null) {
      // Вопрос полностью корректен — кладём в результат.
      questions.push({ text, options: cleanOptions, correct })
    }
  })

  if (errors.length > 0) {
    return { ok: false, errors }
  }

  return { ok: true, draft: { title: title as string, questions } }
}
