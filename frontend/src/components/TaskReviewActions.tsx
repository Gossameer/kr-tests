/**
 * Правки задания уже проведённой работы — для учителя (автора) и администратора.
 *
 * «Добавить верный ответ» (у ввода) или «засчитать этот вариант» (у выбора),
 * «Засчитать всем», «Исключить из оценки» — и отмена каждой. После любой правки
 * сервер перепроверяет все сданные работы: баллы, проценты, умения, статистика
 * и Excel пересчитываются. Ответы учеников не меняются.
 */

import { useState } from 'react'
import { reviewTask } from '../api'
import type { ReviewAction, ReviewOption } from '../types'
import MathText from './MathText'

export type ReviewableTask = {
  task_id: number
  answer_format: 'input' | 'choice'
  credit_all: boolean
  excluded: boolean
  extra_answers: string[]
  options: ReviewOption[]
  /** id вариантов, которые учитель засчитал дополнительно. */
  accepted_option_ids: number[]
}

type Props = {
  testId: number
  task: ReviewableTask
  /** Ответ этого ученика (в разборе работы) — подставляется в «Добавить верный ответ». */
  studentAnswer?: string | null
  /** Вариант, который выбрал ученик (в разборе работы). */
  chosenOptionId?: number | null
  isCorrect?: boolean
  /** Сервер пересчитал работы — странице пора обновить таблицы. */
  onChanged: () => void
}

export default function TaskReviewActions({
  testId,
  task,
  studentAnswer,
  chosenOptionId,
  isCorrect,
  onChanged,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function run(action: ReviewAction, extra: { answer?: string; option_id?: number } = {}) {
    setBusy(true)
    setError('')
    try {
      await reviewTask(testId, task.task_id, { action, ...extra })
      onChanged()
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось сохранить правку')
    } finally {
      setBusy(false)
    }
  }

  function handleAddAnswer() {
    const answer = window.prompt(
      'Какой ответ тоже засчитывать?\n\nОн добавится к правильным ответам этого задания, ' +
        'и все сданные работы перепроверятся. Отменить можно в любой момент.',
      studentAnswer ?? '',
    )
    if (answer !== null && answer.trim() !== '') {
      void run('add_answer', { answer: answer.trim() })
    }
  }

  function confirmThen(text: string, action: ReviewAction) {
    if (window.confirm(text)) {
      void run(action)
    }
  }

  const chosen = task.options.find((option) => option.id === chosenOptionId)
  const canAcceptChosen =
    task.answer_format === 'choice' &&
    chosen !== undefined &&
    !chosen.is_correct &&
    !task.accepted_option_ids.includes(chosen.id)

  return (
    <div className="reviewactions">
      {(task.excluded || task.credit_all) && (
        <p className="reviewactions__state">
          {task.excluded && <span className="badge badge--closed">не учитывается</span>}
          {task.credit_all && <span className="badge badge--open">засчитано всем</span>}
        </p>
      )}

      {task.extra_answers.length > 0 && (
        <p className="reviewactions__extra">
          Добавленные верные ответы:{' '}
          {task.extra_answers.map((answer) => (
            <span key={answer} className="chip chip--small">
              {answer}{' '}
              <button
                type="button"
                className="linkbtn"
                disabled={busy}
                onClick={() => void run('remove_answer', { answer })}
                title="Больше не засчитывать этот ответ"
              >
                убрать
              </button>
            </span>
          ))}
        </p>
      )}

      {task.accepted_option_ids.length > 0 && (
        <p className="reviewactions__extra">
          Засчитаны и варианты:{' '}
          {task.options
            .filter((option) => task.accepted_option_ids.includes(option.id))
            .map((option) => (
              <span key={option.id} className="chip chip--small">
                <MathText text={option.text} />{' '}
                <button
                  type="button"
                  className="linkbtn"
                  disabled={busy}
                  onClick={() => void run('unaccept_option', { option_id: option.id })}
                >
                  отменить
                </button>
              </span>
            ))}
        </p>
      )}

      <div className="row row--tight">
        {task.answer_format === 'input' && (
          <button type="button" className="btn btn--small btn--ghost" disabled={busy} onClick={handleAddAnswer}>
            {isCorrect === false && studentAnswer ? 'Засчитать этот ответ…' : 'Добавить верный ответ…'}
          </button>
        )}
        {canAcceptChosen && chosen && (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            disabled={busy}
            onClick={() => void run('accept_option', { option_id: chosen.id })}
          >
            Засчитать этот вариант
          </button>
        )}
        <button
          type="button"
          className="btn btn--small btn--ghost"
          disabled={busy}
          onClick={() =>
            task.credit_all
              ? void run('uncredit_all')
              : confirmThen(
                  'Засчитать это задание всем, кто его получил?\n\nВсе сданные работы перепроверятся. ' +
                    'Отменить можно в любой момент.',
                  'credit_all',
                )
          }
        >
          {task.credit_all ? 'Отменить «засчитать всем»' : 'Засчитать всем'}
        </button>
        <button
          type="button"
          className="btn btn--small btn--ghost"
          disabled={busy}
          onClick={() =>
            task.excluded
              ? void run('include')
              : confirmThen(
                  'Исключить задание из оценки?\n\nОно не войдёт ни в баллы, ни в максимум, ни в умения — ' +
                    'у всех, кто его получил. Отменить можно в любой момент.',
                  'exclude',
                )
          }
        >
          {task.excluded ? 'Вернуть в оценку' : 'Исключить из оценки'}
        </button>
      </div>
      {error !== '' && <p className="field-error">{error}</p>}
    </div>
  )
}
