/**
 * Исправление оценки по одному заданию уже проведённой работы — для учителя
 * (автора) и администратора. У каждой кнопки — подсказка, когда её нажимать.
 *
 *   «Исправить правильный ответ» / «Отметить другой вариант как верный» —
 *     ИИ ошибся в ответе: ответ ЗАМЕНЯЕТСЯ (не добавляется второй);
 *   «Считать этот ответ верным» / «Засчитать этот вариант» — ученик ответил
 *     верно, но иначе записал (или выбрал вариант, который тоже верен);
 *   «Засчитать всем» и «Исключить из оценки» — задание некорректное.
 *
 * Каждую правку можно отменить. После любой из них сервер перепроверяет все
 * сданные работы: баллы, проценты, умения, статистика, Excel и ключ ответов.
 */

import { useState } from 'react'
import { reviewTask } from '../api'
import { ACTION_HINTS } from '../lib/reviewHints'
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
  /** Правильный ответ исправляли — каким он был (иначе null). */
  original_answer?: string | null
}

type Props = {
  testId: number
  task: ReviewableTask
  /** Ответ этого ученика (в разборе работы). */
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
  // Выбор другого верного варианта у задания с выбором.
  const [pickingOption, setPickingOption] = useState(false)

  async function run(action: ReviewAction, extra: { answer?: string; option_id?: number } = {}) {
    setBusy(true)
    setError('')
    try {
      await reviewTask(testId, task.task_id, { action, ...extra })
      setPickingOption(false)
      onChanged()
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось сохранить правку')
    } finally {
      setBusy(false)
    }
  }

  function ask(text: string, initial: string, action: ReviewAction) {
    const answer = window.prompt(text, initial)
    if (answer !== null && answer.trim() !== '') {
      void run(action, { answer: answer.trim() })
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
  const answeredWrong = isCorrect === false && Boolean(studentAnswer)

  return (
    <div className="reviewactions">
      {(task.excluded || task.credit_all || task.original_answer) && (
        <p className="reviewactions__state">
          {task.excluded && <span className="badge badge--closed">не учитывается в оценке</span>}
          {task.credit_all && <span className="badge badge--open">засчитано всем</span>}
          {task.original_answer && (
            <span className="badge badge--open">
              правильный ответ исправлен, был: <MathText text={task.original_answer} />
            </span>
          )}
        </p>
      )}

      {task.extra_answers.length > 0 && (
        <p className="reviewactions__extra">
          Тоже считаются верными:{' '}
          {task.extra_answers.map((answer) => (
            <span key={answer} className="chip chip--small">
              {answer}{' '}
              <button
                type="button"
                className="linkbtn"
                disabled={busy}
                onClick={() => void run('remove_answer', { answer })}
                title="Больше не считать этот ответ верным"
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

      <div className="row row--tight reviewactions__buttons">
        {/* ИИ ошибся в правильном ответе */}
        {task.answer_format === 'input' ? (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            disabled={busy}
            title={ACTION_HINTS.setAnswer}
            onClick={() =>
              ask(
                'Какой ответ правильный?\n\nОн ЗАМЕНИТ прежний правильный ответ (несколько записей — ' +
                  'через «|»). Все сданные работы перепроверятся; отменить можно в любой момент.',
                answeredWrong ? (studentAnswer ?? '') : '',
                'set_answer',
              )
            }
          >
            Исправить правильный ответ…
          </button>
        ) : (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            disabled={busy}
            title={ACTION_HINTS.setOption}
            onClick={() => setPickingOption((value) => !value)}
          >
            Отметить другой вариант как верный…
          </button>
        )}
        {task.original_answer && (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            disabled={busy}
            title="Вернуть правильный ответ, который был до исправления"
            onClick={() => void run('restore_answer')}
          >
            Вернуть прежний ответ
          </button>
        )}

        {/* Ученик ответил верно, но иначе */}
        {task.answer_format === 'input' && (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            disabled={busy}
            title={answeredWrong ? ACTION_HINTS.acceptAnswer : ACTION_HINTS.addAnswer}
            onClick={() =>
              answeredWrong
                ? void run('add_answer', { answer: studentAnswer ?? '' })
                : ask('Какую ещё запись ответа считать верной?', '', 'add_answer')
            }
          >
            {answeredWrong ? 'Считать этот ответ верным' : 'Добавить верную запись ответа…'}
          </button>
        )}
        {canAcceptChosen && chosen && (
          <button
            type="button"
            className="btn btn--small btn--ghost"
            disabled={busy}
            title={ACTION_HINTS.acceptOption}
            onClick={() => void run('accept_option', { option_id: chosen.id })}
          >
            Засчитать этот вариант
          </button>
        )}

        {/* Задание некорректное */}
        <button
          type="button"
          className="btn btn--small btn--ghost"
          disabled={busy}
          title={task.credit_all ? 'Снова проверять ответы на это задание как обычно' : ACTION_HINTS.creditAll}
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
          title={task.excluded ? 'Снова учитывать задание в баллах и умениях' : ACTION_HINTS.exclude}
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

      {pickingOption && (
        <div className="optionpicker">
          <p className="hint">Какой вариант верный? Прежний перестанет засчитываться.</p>
          {task.options.map((option) => (
            <button
              key={option.id}
              type="button"
              className={'btn btn--small ' + (option.is_correct ? 'btn--primary' : 'btn--ghost')}
              disabled={busy || option.is_correct}
              onClick={() => void run('set_option', { option_id: option.id })}
            >
              <MathText text={option.text} />
              {option.is_correct && ' — сейчас верный'}
            </button>
          ))}
        </div>
      )}
      {error !== '' && <p className="field-error">{error}</p>}
    </div>
  )
}
