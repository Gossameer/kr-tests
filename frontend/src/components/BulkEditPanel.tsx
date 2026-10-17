/**
 * «Изменить во всех вариантах»: ИИ поправляет или заменяет задания одного
 * умения (или одной позиции умения) сразу во всех вариантах.
 *
 * Учитель пишет своими словами, что не так, и выбирает режим:
 *   «Поправить» — те же задания с исправлением;
 *   «Заменить»  — новые задания на это умение с учётом пожелания.
 * Пожелание сохраняется в умении как «Уточнение для ИИ» и учитывается дальше.
 *
 * Результат — «было → стало» по каждому варианту, с самопроверкой и
 * замечаниями; учитель принимает всё разом или по одному. Остальные задания
 * таблицы (и ручные правки в них) не трогаются.
 */

import { useState } from 'react'
import { fetchAiJob, startBulkJob } from '../api'
import type { AiContext } from '../api'
import { reviewLines, sleep, taskFromAi } from '../lib/aiJob'
import type { BulkItem, SkillDraft, TaskDraft, VariantDraft } from '../types'
import MathText from './MathText'

type Props = {
  context: AiContext
  variantsCount: number
  skillIndex: number
  /** Номер задания внутри умения (с нуля) или null — все задания умения. */
  order: number | null
  variants: VariantDraft[]
  /** Сохранить пожелание в умении как «Уточнение для ИИ». */
  onHint: (skillIndex: number, hint: string) => void
  /**
   * Принять новые задания на места старых — пачкой: «Принять всё» должно
   * применить все правки к одной и той же таблице, а не каждую к старой копии.
   */
  onAccept: (accepted: { variantNo: number; order: number; task: TaskDraft }[]) => void
  onClose: () => void
}

/** Уточнение умения: новое пожелание дописывается к прежним, без повторов. */
function mergeHint(previous: string, wish: string): string {
  const lines = previous.split('\n').map((line) => line.trim()).filter((line) => line !== '')
  return lines.includes(wish) ? lines.join('\n') : [...lines, wish].join('\n')
}

function formatAnswer(task: { answer_format: string; options: string[]; correct: number | null; accepted_answers: string[] }): string {
  return task.answer_format === 'choice'
    ? `${(task.correct ?? 0) + 1}) ${task.options[task.correct ?? 0] ?? ''}`
    : task.accepted_answers.join(' | ')
}

export default function BulkEditPanel({
  context,
  variantsCount,
  skillIndex,
  order,
  variants,
  onHint,
  onAccept,
  onClose,
}: Props) {
  const skill: SkillDraft = context.skills[skillIndex - 1]
  const [wish, setWish] = useState('')
  const [mode, setMode] = useState<'fix' | 'replace'>('fix')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState<BulkItem[] | null>(null)
  const [decided, setDecided] = useState<Record<string, 'accepted' | 'rejected'>>({})

  const key = (item: BulkItem) => `${item.variant_no}:${item.order}`

  async function handleStart() {
    const text = wish.trim()
    if (text.length < 3) {
      setError('Напишите, что не так, хотя бы парой слов.')
      return
    }
    setError('')
    setRunning(true)
    setItems(null)
    setDecided({})

    // Пожелание становится «Уточнением для ИИ» умения — и уходит в этот же запрос.
    const hint = mergeHint(skill.aiHint ?? '', text)
    onHint(skillIndex, hint)
    const skills = context.skills.map((item, index) => (index === skillIndex - 1 ? { ...item, aiHint: hint } : item))

    const current = variants
      .map((variant) => ({
        variant_no: variant.variantNo,
        tasks: variant.tasks
          .filter((task) => task.skillIndex === skillIndex)
          .filter((_, position) => order === null || position === order)
          .map((task) => ({
            text: task.text,
            answer_format: task.answerFormat,
            options: task.options,
            correct: task.correct,
            accepted_answers: task.acceptedAnswers.filter((answer) => answer.trim() !== ''),
            solution: task.solution,
          })),
      }))
      .filter((variant) => variant.tasks.some((task) => task.text.trim() !== ''))

    try {
      const { job_id: jobId } = await startBulkJob({ ...context, skills }, {
        variantsCount,
        skillIndex,
        order,
        mode,
        wish: text,
        current,
      })
      for (;;) {
        await sleep(1500)
        const job = await fetchAiJob(jobId)
        setItems(job.items)
        if (job.status !== 'running') {
          if (job.status === 'failed') {
            setError(job.error || 'ИИ не справился. Попробуйте ещё раз.')
          }
          break
        }
      }
    } catch (problem: unknown) {
      setError(problem instanceof Error ? problem.message : 'Не удалось запустить правку')
    } finally {
      setRunning(false)
    }
  }

  function accept(list: BulkItem[]) {
    const ready = list.filter((item) => item.after !== null)
    onAccept(
      ready.map((item) => ({ variantNo: item.variant_no, order: item.order, task: taskFromAi(item.after!) })),
    )
    setDecided((previous) => ({
      ...previous,
      ...Object.fromEntries(ready.map((item) => [key(item), 'accepted' as const])),
    }))
  }

  const ready = (items ?? []).filter((item) => item.status === 'ok' && item.after !== null)
  const pendingReady = ready.filter((item) => decided[key(item)] === undefined)
  const busyCount = (items ?? []).filter((item) => !['ok', 'failed'].includes(item.status)).length

  return (
    <section className="card bulkpanel" id="bulk-panel">
      <div className="card__head">
        <h3>
          Изменить во всех вариантах: умение {skillIndex} «{skill.title}»
          {order !== null && <>, задание {order + 1}</>}
        </h3>
        <button type="button" className="btn btn--small btn--ghost" onClick={onClose} disabled={running}>
          Закрыть
        </button>
      </div>

      <label className="label" htmlFor="bulk-wish">
        Что не так — своими словами
      </label>
      <textarea
        id="bulk-wish"
        className="textarea"
        rows={3}
        value={wish}
        onChange={(event) => setWish(event.target.value)}
        placeholder="Например: в части вариантов ответ получается бесконечным — нужен конечный ответ числом"
        disabled={running}
      />
      <p className="hint">
        Пожелание сохранится в умении как «Уточнение для ИИ» и будет учитываться при следующих
        генерациях и правках.
      </p>

      <div className="row row--tight" role="radiogroup" aria-label="Режим">
        <label className="check check--inline">
          <input type="radio" name="bulk-mode" checked={mode === 'fix'} onChange={() => setMode('fix')} disabled={running} />
          <span>
            <b>Поправить</b> — те же задания с исправлением
          </span>
        </label>
        <label className="check check--inline">
          <input type="radio" name="bulk-mode" checked={mode === 'replace'} onChange={() => setMode('replace')} disabled={running} />
          <span>
            <b>Заменить</b> — новые задания на это умение
          </span>
        </label>
      </div>

      <div className="row row--tight">
        <button type="button" className="btn btn--primary" onClick={() => void handleStart()} disabled={running}>
          {running ? `ИИ работает… осталось ${busyCount || '…'}` : 'Запустить'}
        </button>
        {pendingReady.length > 0 && !running && (
          <button type="button" className="btn btn--ghost" onClick={() => accept(pendingReady)}>
            Принять всё ({pendingReady.length})
          </button>
        )}
      </div>
      {error !== '' && <p className="field-error">{error}</p>}

      {items !== null && (
        <ol className="bulkitems">
          {items.map((item) => {
            const state = decided[key(item)]
            const after = item.after !== null ? taskFromAi(item.after) : null
            return (
              <li key={key(item)} className={'bulkitem' + (state ? ` bulkitem--${state}` : '')}>
                <p className="bulkitem__place">
                  Вариант {item.variant_no}
                  {(skill.tasksPerVariant > 1 || order !== null) && <>, задание {item.order + 1}</>}
                  {state === 'accepted' && <span className="badge badge--open">принято</span>}
                  {state === 'rejected' && <span className="badge badge--closed">отклонено</span>}
                </p>
                <div className="bulkitem__cols">
                  <div>
                    <p className="hint">Было</p>
                    <p>
                      <MathText text={item.before.text} />
                    </p>
                    <p className="hint">Ответ: {formatAnswer(item.before)}</p>
                  </div>
                  <div>
                    <p className="hint">Стало</p>
                    {item.status === 'failed' ? (
                      <p className="field-error">Не получилось: {item.error}</p>
                    ) : after === null ? (
                      <p className="loading">{item.status === 'checking' ? 'проверяется…' : 'составляется…'}</p>
                    ) : (
                      <>
                        <p>
                          <MathText text={after.text} />
                        </p>
                        {after.answerFormat === 'choice' ? (
                          <ol className="bulkitem__options">
                            {after.options.map((option, index) => (
                              <li key={index} className={index === after.correct ? 'taskpreview__correct' : undefined}>
                                <MathText text={option} />
                                {index === after.correct && ' ✓'}
                              </li>
                            ))}
                          </ol>
                        ) : (
                          <p className="hint">Ответ: {after.acceptedAnswers.join(' | ')}</p>
                        )}
                        {after.solution !== '' && (
                          <p className="hint">
                            Решение: <MathText text={after.solution} />
                          </p>
                        )}
                        {item.status === 'checking' && <p className="loading">проверяется…</p>}
                        {after.review && (
                          <div className="reviewnote">
                            {reviewLines(after).map((line) => (
                              <p key={line}>{line}</p>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
                {item.status === 'ok' && after !== null && state === undefined && (
                  <div className="row row--tight">
                    <button type="button" className="btn btn--small btn--primary" onClick={() => accept([item])}>
                      Принять
                    </button>
                    <button
                      type="button"
                      className="btn btn--small btn--ghost"
                      onClick={() => setDecided((previous) => ({ ...previous, [key(item)]: 'rejected' }))}
                    >
                      Отклонить
                    </button>
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
