/**
 * Разбор и проверка того, что вернул ИИ.
 *
 * Три задачи:
 *   1. вытащить JSON, даже если вокруг него текст или ```json;
 *   2. превратить его в наши варианты и задания;
 *   3. проверить полноту: в каждом варианте все умения, нужное число заданий,
 *      совпадающий формат ответа.
 *
 * Бэкенд проверяет всё заново — фронтенду доверять нельзя. Здесь проверка
 * нужна, чтобы учитель увидел ошибку сразу и понял, что именно не так.
 */

import type { AnswerFormat, SkillDraft, TaskDraft, VariantDraft } from '../types'
import { FORMAT_NAMES } from '../types'

export type ParseResult =
  | { ok: true; variants: VariantDraft[] }
  | { ok: false; errors: string[] }

export type ParseTaskResult =
  | { ok: true; task: TaskDraft }
  | { ok: false; errors: string[] }

/**
 * Достаёт JSON из ответа ИИ.
 *
 * ИИ часто отвечает так:
 *     Вот ваша проверочная работа:
 *     ```json
 *     { "variants": [...] }
 *     ```
 * Поэтому сначала пробуем содержимое ```-блока, потом — от первой «{»
 * до последней «}». Если не нашли, отдаём текст как есть: пусть JSON.parse
 * сам сообщит об ошибке.
 */
export function extractJsonBlock(raw: string): string {
  const text = raw.trim()

  const fenced = /```(?:json)?\s*([\s\S]*?)```/i.exec(text)
  if (fenced?.[1] !== undefined && fenced[1].trim() !== '') {
    return fenced[1].trim()
  }

  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start !== -1 && end > start) {
    return text.slice(start, end + 1)
  }

  return text
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((item) => asString(item)).filter((item) => item !== '')
}

/** Пустое задание — заготовка для ручного заполнения. */
export function emptyTask(skillIndex: number, format: AnswerFormat): TaskDraft {
  return {
    skillIndex,
    text: '',
    answerFormat: format,
    options: format === 'choice' ? ['', '', '', ''] : [],
    correct: format === 'choice' ? 0 : null,
    acceptedAnswers: format === 'choice' ? [] : [''],
    solution: '',
  }
}

/**
 * Заменяет «*» на школьный знак умножения «·»: «3 * 4», «3*4», «2*x» → «3 · 4».
 * «**» (жирный шрифт в markdown) не трогаем. То же делает сервер при генерации.
 */
export function schoolSigns(text: string): string {
  return text.replace(/(?<=[\p{L}\p{N}_)\]])\s*(?<!\*)\*(?!\*)\s*(?=[\p{L}\p{N}_([])|\s\*\s/gu, ' · ')
}

/** Разбирает одно задание из объекта ИИ. */
function readTask(source: Record<string, unknown>, skillIndex: number): TaskDraft {
  const format: AnswerFormat = asString(source.format) === 'choice' ? 'choice' : 'input'
  const correctRaw = source.correct

  return {
    skillIndex,
    text: schoolSigns(asString(source.text)),
    answerFormat: format,
    options: format === 'choice' ? asStringList(source.options).map(schoolSigns) : [],
    correct:
      format === 'choice' && typeof correctRaw === 'number' && Number.isInteger(correctRaw)
        ? correctRaw
        : format === 'choice'
          ? null
          : null,
    acceptedAnswers: format === 'input' ? asStringList(source.answers) : [],
    solution: schoolSigns(asString(source.solution)),
  }
}

/** Разбирает ответ ИИ на промт замены одного задания. */
export function parseSingleTask(raw: string, skillIndex: number): ParseTaskResult {
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
    return { ok: false, errors: ['Ожидается объект одного задания.'] }
  }

  const task = readTask(data as Record<string, unknown>, skillIndex)
  if (task.text === '') {
    return { ok: false, errors: ['В ответе ИИ нет текста задания (поле "text").'] }
  }

  return { ok: true, task }
}

/** Разбирает ответ ИИ на промт всей проверочной работы. */
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
      errors: ['Ожидается объект вида { "variants": [ ... ] }.'],
    }
  }

  const source = data as Record<string, unknown>
  if (!Array.isArray(source.variants)) {
    return { ok: false, errors: ['В JSON нет списка «variants».'] }
  }

  const errors: string[] = []
  const variants: VariantDraft[] = []

  source.variants.forEach((rawVariant: unknown, index: number) => {
    if (rawVariant === null || typeof rawVariant !== 'object' || Array.isArray(rawVariant)) {
      errors.push(`Вариант ${index + 1}: должен быть объектом с полями variant и tasks.`)
      return
    }

    const variant = rawVariant as Record<string, unknown>
    const variantNo =
      typeof variant.variant === 'number' && Number.isInteger(variant.variant)
        ? variant.variant
        : index + 1

    if (!Array.isArray(variant.tasks)) {
      errors.push(`Вариант ${variantNo}: нет списка заданий «tasks».`)
      return
    }

    const tasks: TaskDraft[] = []
    variant.tasks.forEach((rawTask: unknown, taskIndex: number) => {
      if (rawTask === null || typeof rawTask !== 'object' || Array.isArray(rawTask)) {
        errors.push(`Вариант ${variantNo}, задание ${taskIndex + 1}: должно быть объектом.`)
        return
      }

      const task = rawTask as Record<string, unknown>
      const skillRaw = task.skill
      if (typeof skillRaw !== 'number' || !Number.isInteger(skillRaw) || skillRaw < 1) {
        errors.push(
          `Вариант ${variantNo}, задание ${taskIndex + 1}: ` +
            'не указан номер умения (поле "skill").',
        )
        return
      }

      tasks.push(readTask(task, skillRaw))
    })

    variants.push({ variantNo, tasks })
  })

  if (errors.length > 0) {
    return { ok: false, errors }
  }

  return { ok: true, variants }
}

/** Чего не хватает заданию: поле редактора и понятная фраза. */
export type TaskProblem = {
  /** Какое поле править: текст, правильный ответ (ввод) или варианты (выбор). */
  field: 'text' | 'answers' | 'options' | 'correct'
  /** Коротко, для списка «осталось»: «нет ответа». */
  short: string
  /** Подробно, для подсказки под полем: что сделать и пример. */
  message: string
}

/**
 * Первая проблема задания или null, если его можно публиковать.
 * Те же правила, что в validateTest и на сервере, — но по одному заданию,
 * чтобы подсказать учителю конкретное поле.
 */
export function taskProblem(task: TaskDraft): TaskProblem | null {
  if (task.text.trim() === '') {
    return {
      field: 'text',
      short: 'нет текста',
      message: 'Введите текст задания, например: «Найдите 3/5 от 20».',
    }
  }

  if (task.answerFormat === 'choice') {
    const options = task.options.map((option) => option.trim())
    if (options.filter((option) => option !== '').length < 2) {
      return {
        field: 'options',
        short: 'нужно хотя бы 2 варианта ответа',
        message: 'Впишите хотя бы два варианта ответа.',
      }
    }
    if (options.some((option) => option === '')) {
      return {
        field: 'options',
        short: 'есть пустой вариант ответа',
        message: 'Заполните пустой вариант ответа или удалите его крестиком.',
      }
    }
    if (new Set(options).size !== options.length) {
      return {
        field: 'options',
        short: 'варианты ответа повторяются',
        message: 'Два варианта ответа одинаковые — сделайте их разными.',
      }
    }
    if (task.correct === null || task.correct < 0 || task.correct >= options.length) {
      return {
        field: 'correct',
        short: 'не отмечен правильный ответ',
        message: 'Отметьте кружком правильный вариант ответа.',
      }
    }
    return null
  }

  if (!task.acceptedAnswers.some((answer) => answer.trim() !== '')) {
    return {
      field: 'answers',
      short: 'нет правильного ответа',
      message: 'Укажите правильный ответ, например: 12. Несколько записей — через «|»: 0,5 | 1/2.',
    }
  }
  return null
}

/**
 * Проверка полноты — те же правила, что и на бэкенде.
 * Возвращает все найденные ошибки: учителю удобнее исправить их разом.
 */
export function validateTest(
  skills: SkillDraft[],
  variantsCount: number,
  variants: VariantDraft[],
): string[] {
  const errors: string[] = []

  if (skills.length === 0) {
    errors.push('Добавьте хотя бы одно умение.')
    return errors
  }

  skills.forEach((skill, index) => {
    if (skill.title.trim() === '') {
      errors.push(`Умение ${index + 1}: не заполнено название.`)
    }
  })

  const numbers = variants.map((variant) => variant.variantNo).sort((a, b) => a - b)
  const expected = Array.from({ length: variantsCount }, (_, index) => index + 1)
  if (numbers.join(',') !== expected.join(',')) {
    errors.push(
      `Вариантов должно быть ${variantsCount} с номерами ${expected.join(', ')}, ` +
        `а сейчас: ${numbers.join(', ') || 'ни одного'}.`,
    )
  }

  variants.forEach((variant) => {
    const whereVariant = `Вариант ${variant.variantNo}`

    variant.tasks.forEach((task) => {
      if (task.skillIndex < 1 || task.skillIndex > skills.length) {
        errors.push(
          `${whereVariant}: задание ссылается на умение №${task.skillIndex}, ` +
            `а умений всего ${skills.length}.`,
        )
      }
    })

    skills.forEach((skill, skillIndex) => {
      const number = skillIndex + 1
      const own = variant.tasks.filter((task) => task.skillIndex === number)
      const where = `${whereVariant}, умение ${number} «${skill.title}»`

      if (own.length !== skill.tasksPerVariant) {
        errors.push(`${where}: заданий ${own.length}, а нужно ${skill.tasksPerVariant}.`)
      }

      own.forEach((task, order) => {
        const place = `${where}, задание ${order + 1}`

        if (task.text.trim() === '') {
          errors.push(`${place}: пустой текст задания.`)
        }

        if (task.answerFormat !== skill.answerFormat) {
          errors.push(
            `${place}: формат «${FORMAT_NAMES[task.answerFormat]}», ` +
              `а у умения — «${FORMAT_NAMES[skill.answerFormat]}».`,
          )
          return
        }

        if (task.answerFormat === 'choice') {
          const options = task.options.map((option) => option.trim())
          if (options.filter((option) => option !== '').length < 2) {
            errors.push(`${place}: нужно минимум 2 непустых варианта ответа.`)
          }
          if (options.some((option) => option === '')) {
            errors.push(`${place}: есть пустые варианты ответа.`)
          }
          if (new Set(options).size !== options.length) {
            errors.push(`${place}: варианты ответа повторяются.`)
          }
          if (
            task.correct === null ||
            task.correct < 0 ||
            task.correct >= task.options.length
          ) {
            errors.push(`${place}: не отмечен правильный вариант.`)
          }
        } else {
          const answers = task.acceptedAnswers
            .map((answer) => answer.trim())
            .filter((answer) => answer !== '')
          if (answers.length === 0) {
            errors.push(`${place}: не указан правильный ответ.`)
          }
        }
      })
    })
  })

  return errors
}
