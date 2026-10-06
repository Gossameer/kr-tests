/**
 * Встроенная генерация через ИИ: перенос результата в таблицу и черновик.
 *
 * Генерация идёт на сервере в фоне. Страница хранит номер генерации и сам
 * черновик в localStorage — после перезагрузки или возврата на страницу
 * учитель видит свою работу и продолжение прогресса.
 */

import { emptyTask } from './parseTestJson'
import type {
  AiCellState,
  AiJob,
  AiTask,
  SkillDraft,
  TaskDraft,
  VariantDraft,
} from '../types'

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
    // Задания без вопросов учителю неинтересны — храним только то, что стоит посмотреть:
    // расхождение, невыполненную самопроверку или замечание к самому заданию.
    review: task.needs_review
      ? {
          status: task.review.status,
          generated: task.review.generated,
          checked: task.review.checked,
          warning: task.review.warning ?? '',
        }
      : null,
  }
}

/** Требует ли задание взгляда учителя. */
export function needsReview(task: TaskDraft): boolean {
  return Boolean(task.review)
}

/** Что именно не так с заданием — строками, для карточки и подсказки. */
export function reviewLines(task: TaskDraft): string[] {
  const review = task.review
  if (!review) {
    return []
  }
  const lines: string[] = []
  if (review.warning) {
    // «ответ виден в условии; ввод не различает регистр…» → с заглавной буквы.
    lines.push(review.warning.charAt(0).toUpperCase() + review.warning.slice(1) + '.')
  }
  if (review.status === 'mismatch') {
    lines.push(`При генерации: ${review.generated} · при проверке: ${review.checked}`)
  } else if (review.status === 'unchecked') {
    lines.push(
      `Самопроверка не выполнилась: ${review.checked || 'причина неизвестна'}. Сверьте ответ сами.`,
    )
  }
  return lines
}

/** Подсказка к клетке: «при генерации: 2,5 · при проверке: 25» или причина. */
export function reviewHint(task: TaskDraft): string {
  return reviewLines(task)
    .map((line) => line.charAt(0).toLowerCase() + line.slice(1))
    .join('\n')
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
 * Переносит свежий статус генерации в таблицу — по клеткам «умение × вариант».
 *
 * Готовая клетка переносится ОДИН раз (по номеру версии): как только она
 * составлена и проверена, она появляется в таблице, а если учитель уже правит
 * её задания, следующий опрос сервера его правки не затрёт. Остальные клетки
 * остаются пустыми, но получают статус — видно, что ещё составляется,
 * что проверяется, а что не удалось.
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
  const cellAt = new Map(job.cells.map((cell) => [`${cell.variant_no}:${cell.skill_index}`, cell]))

  return Array.from({ length: job.variants_count }, (_, index) => {
    const variantNo = index + 1
    const old = byNumber.get(variantNo)
    const aiCells: Record<number, AiCellState> = {}

    const tasks = skills.flatMap((skill, position) => {
      const skillIndex = position + 1
      const cell = cellAt.get(`${variantNo}:${skillIndex}`)
      const applied = old?.aiCells?.[skillIndex]?.version ?? 0
      const kept = old?.tasks.filter((task) => task.skillIndex === skillIndex) ?? []
      const current =
        kept.length === skill.tasksPerVariant
          ? kept
          : Array.from({ length: skill.tasksPerVariant }, () =>
              emptyTask(skillIndex, skill.answerFormat),
            )

      if (!cell) {
        return current
      }
      if (cell.status === 'ok' && cell.version > applied) {
        aiCells[skillIndex] = { status: 'ok', version: cell.version, error: '' }
        return cell.tasks.map(taskFromAi)
      }
      aiCells[skillIndex] = { status: cell.status, version: applied, error: cell.error }
      return current
    })

    return { variantNo, tasks, aiCells }
  })
}

/** Клетка ещё в работе на сервере: в очереди, составляется или проверяется. */
export function cellBusy(state: AiCellState | undefined): boolean {
  return (
    state !== undefined &&
    (state.status === 'pending' || state.status === 'running' || state.status === 'checking')
  )
}

/** Клетку не удалось составить (ИИ не справился или сервер перезапустили). */
export function cellMissing(state: AiCellState | undefined): boolean {
  return state !== undefined && (state.status === 'failed' || state.status === 'interrupted')
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
