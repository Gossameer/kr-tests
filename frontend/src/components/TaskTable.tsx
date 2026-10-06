/**
 * Экран проверки: таблица «варианты × умения» и правка одного задания.
 *
 * Как устроено:
 *   - в таблице строки — умения, столбцы — варианты; в ячейке столько клеточек,
 *     сколько заданий на это умение в варианте;
 *   - клеточка показывает, заполнено ли задание, и открывает его на правку;
 *   - под таблицей — редактор выбранного задания: текст, ответ, решение;
 *   - там же кнопка «Скопировать промт для замены» и поле, куда учитель
 *     вставляет ответ ИИ, чтобы заменить ЭТО задание.
 *
 * Без ИИ таблицу можно заполнить руками: пустые задания создаются заранее.
 *
 * После генерации через ИИ здесь же видно:
 *   - задания, где ответ при генерации не совпал с самопроверкой, — жёлтой
 *     рамкой, в подсказке оба ответа; сверху счётчик и фильтр «только они»;
 *   - варианты, которые не удались, — с кнопкой «повторить».
 */

import { useState } from 'react'
import {
  cellBusy,
  cellMissing,
  countNeedsReview,
  needsReview,
  replaceTaskAt,
  reviewHint,
  reviewLines,
} from '../lib/aiJob'
import type { TaskPlace } from '../lib/aiJob'
import { buildReplacePrompt } from '../lib/aiPrompt'
import type { PromptFields } from '../lib/aiPrompt'
import { cellId } from '../lib/checklist'
import { parseSingleTask, taskProblem } from '../lib/parseTestJson'
import type { AiCellStatus, SkillDraft, TaskDraft, VariantDraft } from '../types'

type Props = {
  skills: SkillDraft[]
  variants: VariantDraft[]
  promptFields: PromptFields
  onChange: (variants: VariantDraft[]) => void
  /** Настроена ли генерация на сервере: без неё кнопку «Перегенерировать» не показываем. */
  aiEnabled?: boolean
  /** Перегенерировать одно задание на сервере. */
  onRegenerate?: (place: TaskPlace) => Promise<void>
  /** Учитель уже нажимал «Опубликовать» — показываем все ошибки, даже в нетронутых полях. */
  showErrors?: boolean
}

/** Где именно лежит задание: вариант, умение и номер задания внутри умения. */
type Place = TaskPlace

/** Что написать в клетке, пока в ней нет задания от ИИ. */
const CELL_STATE: Record<Exclude<AiCellStatus, 'ok'>, string> = {
  pending: 'в очереди…',
  running: 'составляется…',
  checking: 'проверяется…',
  failed: 'не удалось',
  interrupted: 'прервано',
}

/** Заполнено ли задание настолько, чтобы его можно было публиковать. */
function isReady(task: TaskDraft): boolean {
  // Те же правила, что в списке «Чтобы опубликовать, осталось»: ✓ в клетке
  // и пункт списка не могут расходиться.
  return taskProblem(task) === null
}

export default function TaskTable({
  skills,
  variants,
  promptFields,
  onChange,
  aiEnabled = false,
  onRegenerate,
  showErrors = false,
}: Props) {
  const [place, setPlace] = useState<Place | null>(null)
  const [replaceRaw, setReplaceRaw] = useState('')
  const [replaceErrors, setReplaceErrors] = useState<string[]>([])
  const [promptCopied, setPromptCopied] = useState(false)
  const [onlyReview, setOnlyReview] = useState(false)
  // Какое задание сейчас перегенерируется (ключ места).
  const [regenerating, setRegenerating] = useState<string | null>(null)
  const [aiProblem, setAiProblem] = useState('')

  const reviewCount = countNeedsReview(variants)
  // Фильтр имеет смысл, только пока есть что показывать.
  const filtering = onlyReview && reviewCount > 0

  /** Задания одного варианта на одно умение, по порядку. */
  function tasksAt(variantNo: number, skillIndex: number): TaskDraft[] {
    const variant = variants.find((item) => item.variantNo === variantNo)
    if (!variant) {
      return []
    }
    return variant.tasks.filter((task) => task.skillIndex === skillIndex)
  }

  function taskAt(target: Place): TaskDraft | null {
    return tasksAt(target.variantNo, target.skillIndex)[target.order] ?? null
  }

  /** Заменяет одно задание новым значением, остальные оставляет как есть. */
  function updateTask(target: Place, changes: Partial<TaskDraft>) {
    onChange(replaceTaskAt(variants, target, changes))
  }

  function placeKey(target: Place): string {
    return `${target.variantNo}:${target.skillIndex}:${target.order}`
  }

  async function handleRegenerate() {
    if (!place || !onRegenerate) {
      return
    }
    const target = place
    setRegenerating(placeKey(target))
    setAiProblem('')
    try {
      await onRegenerate(target)
    } catch (error: unknown) {
      setAiProblem(error instanceof Error ? error.message : 'Не удалось сгенерировать задание.')
    } finally {
      setRegenerating(null)
    }
  }

  const selected = place ? taskAt(place) : null
  const selectedSkill = place ? skills[place.skillIndex - 1] : null

  async function handleCopyPrompt() {
    if (!place || !selected || !selectedSkill) {
      return
    }
    const prompt = buildReplacePrompt(
      promptFields,
      selectedSkill,
      place.variantNo,
      selected.text,
    )
    try {
      await navigator.clipboard.writeText(prompt)
      setPromptCopied(true)
      window.setTimeout(() => setPromptCopied(false), 2000)
    } catch {
      setReplaceErrors(['Браузер не дал скопировать — скопируйте промт вручную.'])
    }
  }

  function handleApplyReplacement() {
    if (!place) {
      return
    }
    const result = parseSingleTask(replaceRaw, place.skillIndex)
    if (!result.ok) {
      setReplaceErrors(result.errors)
      return
    }

    updateTask(place, {
      text: result.task.text,
      options: result.task.options,
      correct: result.task.correct,
      acceptedAnswers: result.task.acceptedAnswers,
      solution: result.task.solution,
      // Задание новое — прежняя отметка о расхождении к нему не относится.
      review: null,
    })
    setReplaceRaw('')
    setReplaceErrors([])
  }

  const selectedBusy = place !== null && regenerating === placeKey(place)

  // Что не так с открытым заданием. Про пустой текст говорим только после
  // «Опубликовать» (иначе новое пустое задание сразу краснеет), а про ответ —
  // как только учитель начал заполнять задание.
  const problem = selected ? taskProblem(selected) : null
  const shownProblem =
    problem !== null && (showErrors || (problem.field !== 'text' && selected?.text.trim() !== ''))
      ? problem
      : null

  return (
    <div className="tasktable">
      {/* ------------------------- Требуют проверки ------------------------- */}
      {reviewCount > 0 && (
        <div className="reviewbar">
          <span className="reviewbar__count">Требуют проверки: {reviewCount}</span>
          <label className="check check--inline">
            <input
              type="checkbox"
              checked={onlyReview}
              onChange={(event) => setOnlyReview(event.target.checked)}
            />
            <span>показать только их</span>
          </label>
          <span className="hint">
            У этих заданий самопроверка ИИ не сошлась, не выполнилась или есть замечание
            к условию. Нажмите на жёлтую клетку — там написано, что именно.
          </span>
        </div>
      )}

      {aiProblem !== '' && <p className="field-error">{aiProblem}</p>}

      {/* ------------------------- Таблица ------------------------- */}
      <div className="table-scroll">
        <table className="table matrix">
          <thead>
            <tr>
              <th>Умение</th>
              {variants.map((variant) => (
                <th key={variant.variantNo}>Вариант {variant.variantNo}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {skills.map((skill, skillPosition) => {
              const skillIndex = skillPosition + 1
              const hasReview = variants.some((variant) =>
                variant.tasks.some((task) => task.skillIndex === skillIndex && needsReview(task)),
              )
              if (filtering && !hasReview) {
                return null
              }
              return (
                <tr key={skillIndex}>
                  <td className="matrix__skill">
                    <span className="matrix__title">
                      {skillIndex}. {skill.title || <i>без названия</i>}
                    </span>
                    <span className="hint">
                      {skill.answerFormat === 'choice' ? 'выбор' : 'ввод'} ·{' '}
                      {skill.tasksPerVariant} шт.
                    </span>
                  </td>

                  {variants.map((variant) => {
                    const own = tasksAt(variant.variantNo, skillIndex)
                    return (
                      <td key={variant.variantNo}>
                        <div className="cells">
                          {Array.from({ length: skill.tasksPerVariant }).map((_, order) => {
                            const task = own[order]
                            const active =
                              place?.variantNo === variant.variantNo &&
                              place?.skillIndex === skillIndex &&
                              place?.order === order
                            const ready = task ? isReady(task) : false
                            const review = task ? needsReview(task) : false
                            // Состояние клетки на сервере: пока задания нет,
                            // показываем, что с ней происходит.
                            const state = variant.aiCells?.[skillIndex]
                            const busy = !ready && cellBusy(state)
                            const missing = !ready && cellMissing(state)

                            if (filtering && !review) {
                              // Пустое место вместо клетки: строки не прыгают по высоте.
                              return <span key={order} className="cell cell--hidden" />
                            }

                            return (
                              <button
                                key={order}
                                id={cellId(variant.variantNo, skillIndex, order)}
                                type="button"
                                className={
                                  'cell' +
                                  (ready ? ' cell--ready' : ' cell--empty') +
                                  (review ? ' cell--review' : '') +
                                  (busy ? ' cell--busy' : '') +
                                  (missing ? ' cell--failed' : '') +
                                  (active ? ' cell--active' : '')
                                }
                                // Пока ИИ работает над клеткой, править её рано:
                                // готовое задание заменит то, что успели ввести.
                                disabled={busy}
                                onClick={() => {
                                  setPlace({
                                    variantNo: variant.variantNo,
                                    skillIndex,
                                    order,
                                  })
                                  setReplaceRaw('')
                                  setReplaceErrors([])
                                }}
                                title={
                                  task && review
                                    ? `${task.text}\n${reviewHint(task)}`
                                    : missing && state
                                      ? `${state.error || 'Задание не составлено'}\nНажмите, чтобы заполнить вручную.`
                                      : task?.text || 'Задание не заполнено'
                                }
                              >
                                {review ? '!' : ready ? '✓' : missing ? '✕' : '—'}
                                <span className="cell__text">
                                  {(busy || missing) && state && state.status !== 'ok'
                                    ? CELL_STATE[state.status]
                                    : task?.text.slice(0, 28) || 'пусто'}
                                </span>
                              </button>
                            )
                          })}
                        </div>
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="hint">
        Нажмите на клетку, чтобы посмотреть и поправить задание. ✓ — задание готово,
        — — не хватает текста или ответа
        {reviewCount > 0 && <>, ! — ответ не подтвердился при самопроверке ИИ</>}.
      </p>

      {/* ------------------------- Правка задания ------------------------- */}
      {place && selected && selectedSkill && (
        <section className="card card--task">
          <div className="card__head">
            <h3>
              Вариант {place.variantNo} · умение {place.skillIndex} «{selectedSkill.title}»
              {selectedSkill.tasksPerVariant > 1 && <> · задание {place.order + 1}</>}
            </h3>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => setPlace(null)}>
              Закрыть
            </button>
          </div>

          {selected.review && (
            <div className="reviewnote">
              {selected.review.status === 'mismatch' && (
                <p>ИИ решил задание заново и получил другой ответ.</p>
              )}
              {reviewLines(selected).map((line) => (
                <p key={line}>{line}</p>
              ))}
              <button
                type="button"
                className="btn btn--small btn--ghost"
                onClick={() => updateTask(place, { review: null })}
              >
                {selected.review.status === 'mismatch'
                  ? 'Ответ верный — снять отметку'
                  : 'Проверил(а) — снять отметку'}
              </button>
            </div>
          )}

          <label className="label label--spaced" htmlFor="task-text">
            Текст задания
          </label>
          <textarea
            id="task-text"
            className={
              'textarea textarea--question' +
              (shownProblem?.field === 'text' ? ' input--invalid' : '')
            }
            value={selected.text}
            onChange={(event) => updateTask(place, { text: event.target.value })}
            rows={3}
            placeholder="Например: Найдите 3/5 от 20"
            aria-invalid={shownProblem?.field === 'text'}
            aria-describedby="task-text-hint"
          />
          {shownProblem?.field === 'text' ? (
            <p className="field-error" id="task-text-hint">
              {shownProblem.message}
            </p>
          ) : (
            <p className="hint" id="task-text-hint">
              Так задание увидит ученик. Знаки — по-школьному: 3 · 4, 12 : 3, дробь 3/5.
            </p>
          )}

          {selected.answerFormat === 'choice' ? (
            <>
              <p className="label label--spaced">Варианты ответа</p>
              <p className="hint">
                Впишите варианты и отметьте кружком правильный.
              </p>
              <ul className="editor__options">
                {selected.options.map((option, optionIndex) => (
                  <li key={optionIndex} className="optrow">
                    <label className="optrow__radio" title="Это правильный вариант">
                      <input
                        id={`task-correct-${optionIndex}`}
                        type="radio"
                        name="task-correct"
                        checked={selected.correct === optionIndex}
                        onChange={() => updateTask(place, { correct: optionIndex })}
                      />
                      <span className="optrow__letter">
                        {'АБВГДЕЖЗИК'[optionIndex] ?? optionIndex + 1}
                      </span>
                    </label>
                    <input
                      id={`task-option-${optionIndex}`}
                      className={
                        'input' +
                        (shownProblem?.field === 'options' && option.trim() === ''
                          ? ' input--invalid'
                          : '')
                      }
                      aria-label={`Вариант ответа ${'АБВГДЕЖЗИК'[optionIndex] ?? optionIndex + 1}`}
                      value={option}
                      onChange={(event) => {
                        const options = selected.options.map((item, index) =>
                          index === optionIndex ? event.target.value : item,
                        )
                        updateTask(place, { options })
                      }}
                      placeholder={`Вариант ${optionIndex + 1}`}
                    />
                    <button
                      type="button"
                      className="iconbtn iconbtn--danger"
                      disabled={selected.options.length <= 2}
                      title={
                        selected.options.length <= 2
                          ? 'Нужно хотя бы два варианта'
                          : 'Удалить вариант'
                      }
                      aria-label="Удалить вариант"
                      onClick={() => {
                        const options = selected.options.filter(
                          (_, index) => index !== optionIndex,
                        )
                        let correct = selected.correct
                        if (correct === optionIndex) {
                          correct = 0
                        } else if (correct !== null && optionIndex < correct) {
                          correct = correct - 1
                        }
                        updateTask(place, { options, correct })
                      }}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
              {(shownProblem?.field === 'options' || shownProblem?.field === 'correct') && (
                <p className="field-error">{shownProblem.message}</p>
              )}
              <button
                type="button"
                className="btn btn--small btn--ghost"
                onClick={() => updateTask(place, { options: [...selected.options, ''] })}
                disabled={selected.options.length >= 10}
              >
                + вариант
              </button>
            </>
          ) : (
            <>
              <label className="label label--spaced" htmlFor="task-answers">
                Правильные ответы
              </label>
              <input
                id="task-answers"
                className={'input' + (shownProblem?.field === 'answers' ? ' input--invalid' : '')}
                aria-invalid={shownProblem?.field === 'answers'}
                value={selected.acceptedAnswers.join(' | ')}
                onChange={(event) =>
                  updateTask(place, {
                    // Несколько допустимых записей разделяются вертикальной чертой.
                    acceptedAnswers: event.target.value.split('|').map((item) => item.trim()),
                  })
                }
                placeholder="12"
              />
              {shownProblem?.field === 'answers' && (
                <p className="field-error">{shownProblem.message}</p>
              )}
              <p className="hint">
                Что должен ввести ученик. Если правильных записей несколько, перечислите их
                через «|»: например «0,5 | 1/2». Регистр, лишние пробелы, запятую вместо точки
                и «ё» вместо «е» мы учтём сами.
              </p>
            </>
          )}

          <label className="label label--spaced" htmlFor="task-solution">
            Краткое решение (видите только вы)
          </label>
          <textarea
            id="task-solution"
            className="textarea textarea--question"
            value={selected.solution}
            onChange={(event) => updateTask(place, { solution: event.target.value })}
            rows={2}
            placeholder="20 : 5 · 3 = 12"
          />
          <p className="hint">Необязательно. 1–2 строки, чтобы быстро сверить ответ.</p>

          {/* ------------------------- Замена через ИИ ------------------------- */}
          <div className="replace">
            <p className="label">Заменить это задание через ИИ</p>
            {aiEnabled && onRegenerate && (
              <div className="row row--tight">
                <button
                  type="button"
                  className="btn btn--primary btn--small"
                  onClick={() => void handleRegenerate()}
                  disabled={regenerating !== null}
                >
                  {selectedBusy ? 'Генерируем…' : 'Перегенерировать'}
                </button>
                <span className="hint">то же умение и формат ответа, другие числа</span>
              </div>
            )}
            <div className="row row--tight">
              <button type="button" className="btn btn--ghost" onClick={handleCopyPrompt}>
                Скопировать промт для замены
              </button>
              {promptCopied && <span className="copied">Скопировано</span>}
            </div>

            <textarea
              className="textarea"
              value={replaceRaw}
              onChange={(event) => {
                setReplaceRaw(event.target.value)
                setReplaceErrors([])
              }}
              rows={4}
              placeholder="Вставьте сюда ответ ИИ с одним заданием"
              spellCheck={false}
            />
            <div className="row row--tight">
              <button
                type="button"
                className="btn btn--primary btn--small"
                onClick={handleApplyReplacement}
                disabled={replaceRaw.trim() === ''}
              >
                Заменить задание
              </button>
            </div>

            {replaceErrors.length > 0 && (
              <div className="alert alert--error">
                <ul>
                  {replaceErrors.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
