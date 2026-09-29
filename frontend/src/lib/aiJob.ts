/**
 * Встроенная генерация через ИИ: перенос результата в таблицу и черновик.
 *
 * Генерация идёт на сервере в фоне. Страница хранит номер генерации и сам
 * черновик в localStorage — после перезагрузки или возврата на страницу
 * учитель видит свою работу и продолжение прогресса.
 */

import { emptyTask } from './parseTestJson'
import type { AiJob, AiTask, SkillDraft, TaskDraft, VariantDraft } from '../types'

/** Задание из ответа сервера → задание таблицы проверки. */
export function taskFromAi(task: AiTask): TaskDraft {
  return {
    skillIndex: task.skill_index,
    text: task.text,
    answerFormat: task.answer_format,
    options: task.answer_format === 'choice' ? task.options : [],
    correct: task.answer_format === 'choice' ? task.correct : null,
    acceptedAnswers: task.answer_format === 'input' ? task.accepted_answers : [],
    solution: task.solution,
    // Совпавшие ответы учителю неинтересны — храним только расхождения.
    review:
      task.review.status === 'ok'
        ? null
        : {
            status: task.review.status,
            generated: task.review.generated,
            checked: task.review.checked,
          },
  }
}

/** Требует ли задание взгляда учителя. */
export function needsReview(task: TaskDraft): boolean {
  return Boolean(task.review)
}

/** Подсказка к заданию с расхождением: «при генерации: 2,5 · при проверке: 25». */
export function reviewHint(task: TaskDraft): string {
  if (!task.review) {
    return ''
  }
  return `при генерации: ${task.review.generated} · при проверке: ${task.review.checked}`
}

export function countNeedsReview(variants: VariantDraft[]): number {
  return variants.reduce(
    (sum, variant) => sum + variant.tasks.filter((task) => needsReview(task)).length,
    0,
  )
}

/** Пустые задания варианта под текущие умения. */
export function emptyTasks(skills: SkillDraft[]): TaskDraft[] {
  return skills.flatMap((skill, position) =>
    Array.from({ length: skill.tasksPerVariant }, () =>
      emptyTask(position + 1, skill.answerFormat),
    ),
  )
}

/**
 * Переносит свежий статус генерации в таблицу.
 *
 * Готовый вариант переносится ОДИН раз (по номеру версии): если учитель уже
 * правит задания, очередной опрос сервера не затрёт его правки. Не готовые
 * варианты остаются пустыми, но получают статус — в таблице видно,
 * какой ещё идёт, а какой не удался.
 */
export function mergeJob(previous: VariantDraft[], job: AiJob): VariantDraft[] {
  // Пустые задания строим по умениям, с которыми запускали генерацию,
  // а не по текущей форме: учитель мог её уже поправить.
  const skills: SkillDraft[] = job.request.skills.map((skill) => ({
    title: skill.title,
    tasksPerVariant: skill.tasks_per_variant,
    answerFormat: skill.answer_format,
  }))
  const byNumber = new Map(previous.map((variant) => [variant.variantNo, variant]))

  return job.variants.map((source) => {
    const old = byNumber.get(source.variant_no)

    if (source.status === 'ok' && (old?.aiVersion ?? 0) < source.version) {
      return {
        variantNo: source.variant_no,
        tasks: source.tasks.map(taskFromAi),
        aiStatus: 'ok',
        aiError: '',
        aiVersion: source.version,
      }
    }

    return {
      variantNo: source.variant_no,
      tasks: old && old.tasks.length > 0 ? old.tasks : emptyTasks(skills),
      aiStatus: source.status,
      aiError: source.error,
      aiVersion: old?.aiVersion ?? 0,
    }
  })
}

/** Где лежит задание: вариант, умение и номер задания внутри умения. */
export type TaskPlace = { variantNo: number; skillIndex: number; order: number }

/** Меняет одно задание, остальные оставляет как есть. */
export function replaceTaskAt(
  variants: VariantDraft[],
  place: TaskPlace,
  changes: Partial<TaskDraft>,
): VariantDraft[] {
  return variants.map((variant) => {
    if (variant.variantNo !== place.variantNo) {
      return variant
    }

    // Считаем, какое это по счёту задание нужного умения.
    let seen = -1
    return {
      ...variant,
      tasks: variant.tasks.map((task) => {
        if (task.skillIndex !== place.skillIndex) {
          return task
        }
        seen += 1
        return seen === place.order ? { ...task, ...changes } : task
      }),
    }
  })
}

/* ===================== Черновик в браузере ===================== */

const DRAFT_KEY = 'kr-tests:create-draft'

/** Всё, что нужно, чтобы вернуть экран создания после перезагрузки. */
export type CreateDraft = {
  jobId: number
  title: string
  subject: string
  classesRaw: string
  variantsCount: number
  shuffle: boolean
  skills: SkillDraft[]
  topic: string
  grade: string
  variants: VariantDraft[]
}

export function loadDraft(): CreateDraft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY)
    if (!raw) {
      return null
    }
    const parsed = JSON.parse(raw) as CreateDraft
    // Мусор от старой версии или ручной правки — просто не восстанавливаем.
    if (typeof parsed?.jobId !== 'number' || !Array.isArray(parsed.skills)) {
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export function saveDraft(draft: CreateDraft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  } catch {
    // Хранилище недоступно или переполнено — генерация на сервере не пострадает.
  }
}

export function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY)
  } catch {
    // см. saveDraft
  }
}

/** Пауза для опроса сервера. */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}
