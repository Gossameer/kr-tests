/**
 * «Где ошиблись»: сводка по заданиям всех вариантов, сгруппированная по умениям.
 *
 * При 30 вариантах открывать каждую работу долго. Здесь у каждого задания видно,
 * сколько учеников ответили неверно и какие неверные ответы встречались чаще,
 * а рядом — «засчитать этот ответ». Там же «Засчитать всем» и «Исключить из оценки».
 * Внизу — журнал правок: кто, что и когда.
 */

import { useCallback, useEffect, useState } from 'react'
import { fetchReviewLog, fetchTasksReview, reviewTask } from '../api'
import type { ReviewLogEntry, TaskReviewRow } from '../types'
import MathText from './MathText'
import TaskReviewActions from './TaskReviewActions'

type Props = {
  testId: number
  /** Меняется после каждой правки где угодно на странице — сводку надо перечитать. */
  revision: number
  onChanged: () => void
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function ReviewSummary({ testId, revision, onChanged }: Props) {
  const [tasks, setTasks] = useState<TaskReviewRow[] | null>(null)
  const [log, setLog] = useState<ReviewLogEntry[]>([])
  const [error, setError] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [busyAnswer, setBusyAnswer] = useState('')

  const load = useCallback(() => {
    fetchTasksReview(testId)
      .then((data) => {
        setTasks(data.tasks)
        setError('')
      })
      .catch((problem: unknown) =>
        setError(problem instanceof Error ? problem.message : 'Не удалось загрузить сводку'),
      )
    fetchReviewLog(testId)
      .then(setLog)
      .catch(() => setLog([]))
  }, [testId])

  useEffect(load, [load, revision])

  async function acceptWrong(task: TaskReviewRow, answer: string, optionId: number | null) {
    const key = `${task.task_id}:${answer}`
    setBusyAnswer(key)
    setError('')
    try {
      await reviewTask(
        testId,
        task.task_id,
        task.answer_format === 'choice' && optionId !== null
          ? { action: 'accept_option', option_id: optionId }
          : { action: 'add_answer', answer },
      )
      onChanged()
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось засчитать ответ')
    } finally {
      setBusyAnswer('')
    }
  }

  if (tasks === null) {
    return error !== '' ? <p className="field-error">{error}</p> : <p className="loading">Считаем…</p>
  }

  const answeredAny = tasks.some((task) => task.answered > 0)
  const shown = tasks.filter(
    (task) => showAll || task.wrong > 0 || task.excluded || task.credit_all || task.extra_answers.length > 0,
  )
  const skills = [...new Map(shown.map((task) => [task.skill_id, task])).values()]

  return (
    <div className="reviewsummary">
      <p className="muted">
        Задание составлено неудачно? Засчитайте верный по сути ответ, засчитайте задание всем
        или исключите его из оценки — все сданные работы пересчитаются сами: баллы, проценты,
        умения, статистика и Excel. Ответы учеников не меняются, любую правку можно отменить.
      </p>
      <label className="check check--inline">
        <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />
        <span>показать все задания, а не только те, где ошибались</span>
      </label>
      {error !== '' && <p className="field-error">{error}</p>}

      {!answeredAny ? (
        <p className="empty">Пока никто не сдал — ошибок нет.</p>
      ) : shown.length === 0 ? (
        <p className="empty">Все ответили на все задания верно.</p>
      ) : (
        skills.map((skill) => (
          <section key={skill.skill_id} className="reviewskill">
            <h3>
              {skill.skill_position}. {skill.skill_title}
            </h3>
            <ol className="reviewtasks">
              {shown
                .filter((task) => task.skill_id === skill.skill_id)
                .sort((a, b) => b.wrong - a.wrong || a.variant_no - b.variant_no)
                .map((task) => (
                  <li key={task.task_id} className="reviewtask" id={`review-task-${task.task_id}`}>
                    <p className="reviewtask__head">
                      <span className="reviewtask__place">
                        Вариант {task.variant_no}, № {task.position}
                      </span>
                      <span className={task.wrong > 0 ? 'reviewtask__wrong' : 'hint'}>
                        неверно {task.wrong} из {task.answered}
                      </span>
                    </p>
                    <p className="reviewtask__text">
                      <MathText text={task.text} />
                    </p>
                    <p className="hint">
                      Правильно:{' '}
                      {task.answer_format === 'choice' ? (
                        <MathText text={task.options.find((option) => option.is_correct)?.text ?? '—'} />
                      ) : (
                        task.accepted_answers.join(' | ')
                      )}
                    </p>

                    {task.top_wrong.length > 0 && (
                      <ul className="wronglist">
                        {task.top_wrong.map((item) => (
                          <li key={`${item.answer}-${item.option_id ?? ''}`}>
                            <span className="wronglist__answer">
                              {item.answer === '' ? (
                                <i>нет ответа</i>
                              ) : (
                                <MathText text={item.answer} />
                              )}
                            </span>
                            <span className="hint"> — {item.count} уч.</span>
                            {item.answer !== '' && !task.credit_all && !task.excluded && (
                              <button
                                type="button"
                                className="btn btn--small btn--ghost"
                                disabled={busyAnswer !== ''}
                                onClick={() => void acceptWrong(task, item.answer, item.option_id)}
                              >
                                {busyAnswer === `${task.task_id}:${item.answer}`
                                  ? 'Пересчитываем…'
                                  : 'засчитать этот ответ'}
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}

                    <TaskReviewActions
                      testId={testId}
                      task={{
                        ...task,
                        accepted_option_ids: task.options
                          .filter((option) => option.accepted)
                          .map((option) => option.id),
                      }}
                      onChanged={onChanged}
                    />
                  </li>
                ))}
            </ol>
          </section>
        ))
      )}

      {log.length > 0 && (
        <details className="details">
          <summary>Журнал правок ({log.length})</summary>
          <ul className="reviewlog">
            {log.map((entry) => (
              <li key={entry.id}>
                <span className="hint">{formatDateTime(entry.created_at)}</span> {entry.user_name}{' '}
                {entry.action_name} — {entry.detail}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
