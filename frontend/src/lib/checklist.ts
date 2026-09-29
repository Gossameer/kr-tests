/**
 * «Чтобы опубликовать, осталось:» — что ещё мешает опубликовать работу.
 *
 * Список живой: пересчитывается при каждом изменении страницы. Каждый пункт
 * знает, куда вести учителя: id поля (прокрутить и поставить фокус), а для
 * задания — id клетки таблицы (открыть редактор) и поле внутри редактора.
 */

import { needsReview } from './aiJob'
import { taskProblem } from './parseTestJson'
import type { AiJob, SkillDraft, VariantDraft } from '../types'

export type TodoItem = {
  key: string
  text: string
  /** id элемента, к которому прокрутить и где поставить фокус. */
  target: string
  /** Для клетки таблицы: после открытия редактора поставить фокус сюда. */
  then?: string
  /**
   * «Мягкий» пункт: публиковать можно, но лучше сначала посмотреть
   * (задания, где самопроверка ИИ не сошлась, — перед публикацией спросим).
   */
  soft?: boolean
}

/** id клетки задания в таблице проверки. */
export function cellId(variantNo: number, skillIndex: number, order: number): string {
  return `cell-${variantNo}-${skillIndex}-${order}`
}

/** id кнопки «повторить» у варианта, который не составился. */
export function retryId(variantNo: number): string {
  return `retry-${variantNo}`
}

/** 1 задание, 2 задания, 5 заданий. */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) {
    return one
  }
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    return few
  }
  return many
}

/** Сколько незаполненных заданий показываем поимённо — дальше «и ещё N». */
const MAX_TASK_ITEMS = 5

export type ChecklistInput = {
  title: string
  subject: string
  classes: string[]
  /** Классы, которые длиннее допустимого (сервер их не примет). */
  longClasses: string[]
  variantsCount: number
  skills: SkillDraft[]
  variants: VariantDraft[]
  job: AiJob | null
  /** Есть ли на странице кнопка «Сгенерировать варианты». */
  aiEnabled: boolean
}

export function buildChecklist(input: ChecklistInput): TodoItem[] {
  const items: TodoItem[] = []
  const { skills, variants } = input

  if (input.title.trim() === '') {
    items.push({ key: 'title', text: 'Введите название работы', target: 'test-title' })
  }
  if (input.subject.trim() === '') {
    items.push({ key: 'subject', text: 'Укажите предмет', target: 'test-subject' })
  }
  if (input.classes.length === 0) {
    items.push({ key: 'classes', text: 'Укажите классы', target: 'test-classes' })
  } else if (input.longClasses.length > 0) {
    items.push({ key: 'classes', text: 'Сократите название класса', target: 'test-classes' })
  }

  if (skills.length === 0) {
    items.push({ key: 'skills', text: 'Добавьте хотя бы одно умение', target: 'skill-add' })
    return items
  }
  skills.forEach((skill, index) => {
    if (skill.title.trim() === '') {
      items.push({
        key: `skill-${index}`,
        text: `Назовите умение ${index + 1}`,
        target: `skill-title-${index}`,
      })
    }
  })

  // Генерация ещё идёт — задания появятся сами, перечислять пустые клетки рано.
  if (input.job !== null && input.job.status === 'running') {
    items.push({
      key: 'job',
      text: `Дождитесь конца генерации — готово ${input.job.done} из ${input.job.total}`,
      target: 'ai-progress',
    })
    return items
  }

  const hasContent = variants.some((variant) =>
    variant.tasks.some((task) => task.text.trim() !== ''),
  )
  if (variants.length === 0 || !hasContent) {
    items.push({
      key: 'tasks',
      text: input.aiEnabled
        ? 'Составьте задания: нажмите «Сгенерировать варианты» или заполните таблицу вручную'
        : 'Составьте задания: заполните таблицу вручную или вставьте ответ ИИ',
      target: input.aiEnabled ? 'ai-generate' : variants.length > 0 ? cellId(1, 1, 0) : 'tasks-section',
    })
    return items
  }

  if (variants.length !== input.variantsCount) {
    items.push({
      key: 'variants-count',
      text:
        `В таблице ${variants.length} ${plural(variants.length, 'вариант', 'варианта', 'вариантов')}, ` +
        `а указано ${input.variantsCount}: верните прежнее число или составьте задания заново`,
      target: 'variants-count',
    })
  }

  // Совпадает ли таблица с умениями: учитель мог поменять умения после генерации.
  const structureOk = variants.every((variant) =>
    variant.tasks.every((task) => task.skillIndex >= 1 && task.skillIndex <= skills.length) &&
    skills.every((skill, position) => {
      const own = variant.tasks.filter((task) => task.skillIndex === position + 1)
      return (
        own.length === skill.tasksPerVariant &&
        own.every((task) => task.answerFormat === skill.answerFormat)
      )
    }),
  )
  if (!structureOk) {
    items.push({
      key: 'structure',
      text:
        'Умения поменялись после того, как составили задания: ' +
        'сгенерируйте варианты заново или верните прежние умения',
      target: input.aiEnabled ? 'ai-generate' : 'tasks-section',
    })
    return items
  }

  const taskItems: TodoItem[] = []
  for (const variant of [...variants].sort((a, b) => a.variantNo - b.variantNo)) {
    if (variant.aiStatus === 'failed') {
      items.push({
        key: `failed-${variant.variantNo}`,
        text: `Вариант ${variant.variantNo} не составился — нажмите «повторить» в таблице`,
        target: retryId(variant.variantNo),
      })
      continue
    }

    skills.forEach((skill, position) => {
      const skillIndex = position + 1
      const own = variant.tasks.filter((task) => task.skillIndex === skillIndex)
      own.forEach((task, order) => {
        const problem = taskProblem(task)
        if (problem === null) {
          return
        }
        const where =
          `умение ${skillIndex}, вариант ${variant.variantNo}` +
          (skill.tasksPerVariant > 1 ? `, задание ${order + 1}` : '')

        let then = 'task-text'
        if (problem.field === 'answers') {
          then = 'task-answers'
        } else if (problem.field === 'correct') {
          then = 'task-correct-0'
        } else if (problem.field === 'options') {
          const empty = task.options.findIndex((option) => option.trim() === '')
          then = `task-option-${Math.max(0, empty)}`
        }

        taskItems.push({
          key: `task-${variant.variantNo}-${skillIndex}-${order}`,
          text: `Заполните задание: ${where} — ${problem.short}`,
          target: cellId(variant.variantNo, skillIndex, order),
          then,
        })
      })
    })
  }

  if (taskItems.length > MAX_TASK_ITEMS) {
    const rest = taskItems.length - MAX_TASK_ITEMS + 1
    items.push(...taskItems.slice(0, MAX_TASK_ITEMS - 1), {
      ...taskItems[MAX_TASK_ITEMS - 1],
      key: 'tasks-rest',
      text: `И ещё ${rest} ${plural(rest, 'задание', 'задания', 'заданий')} без текста или ответа`,
    })
  } else {
    items.push(...taskItems)
  }

  // Жёлтые — не препятствие, но учитель должен их увидеть.
  let reviewCount = 0
  let firstReview = ''
  for (const variant of variants) {
    skills.forEach((_, position) => {
      variant.tasks
        .filter((task) => task.skillIndex === position + 1)
        .forEach((task, order) => {
          if (needsReview(task)) {
            reviewCount += 1
            firstReview ||= cellId(variant.variantNo, position + 1, order)
          }
        })
    })
  }
  if (reviewCount > 0) {
    items.push({
      key: 'review',
      text:
        `Проверьте ${reviewCount} ${plural(reviewCount, 'задание', 'задания', 'заданий')}, ` +
        `${reviewCount === 1 ? 'отмеченное' : 'отмеченные'} жёлтым`,
      target: firstReview,
      then: 'task-answers-or-correct',
      soft: true,
    })
  }

  return items
}

/**
 * Ведёт к пункту списка: прокручивает, ставит фокус, а для задания —
 * открывает его в редакторе и ставит фокус в нужное поле.
 */
export function goToItem(item: Pick<TodoItem, 'target' | 'then'>): void {
  const element = document.getElementById(item.target)
  if (!element) {
    return
  }
  element.scrollIntoView({ behavior: 'smooth', block: 'center' })

  if (!item.then) {
    element.focus({ preventScroll: true })
    return
  }

  // Клетка таблицы: открываем задание, а фокус ставим в поле редактора,
  // когда он отрисуется.
  element.click()
  window.setTimeout(() => {
    const field =
      item.then === 'task-answers-or-correct'
        ? (document.getElementById('task-answers') ?? document.getElementById('task-correct-0'))
        : document.getElementById(item.then ?? '')
    const focusable = (field ?? element) as HTMLElement
    focusable.scrollIntoView({ behavior: 'smooth', block: 'center' })
    focusable.focus({ preventScroll: true })
  }, 60)
}
