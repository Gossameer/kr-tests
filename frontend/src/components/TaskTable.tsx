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
 */

import { useState } from 'react'
import { buildReplacePrompt } from '../lib/aiPrompt'
import type { PromptFields } from '../lib/aiPrompt'
import { parseSingleTask } from '../lib/parseTestJson'
import type { SkillDraft, TaskDraft, VariantDraft } from '../types'

type Props = {
  skills: SkillDraft[]
  variants: VariantDraft[]
  promptFields: PromptFields
  onChange: (variants: VariantDraft[]) => void
}

/** Где именно лежит задание: вариант, умение и номер задания внутри умения. */
type Place = { variantNo: number; skillIndex: number; order: number }

/** Заполнено ли задание настолько, чтобы его можно было публиковать. */
function isReady(task: TaskDraft): boolean {
  if (task.text.trim() === '') {
    return false
  }
  if (task.answerFormat === 'choice') {
    const options = task.options.map((option) => option.trim()).filter(Boolean)
    return options.length >= 2 && task.correct !== null
  }
  return task.acceptedAnswers.some((answer) => answer.trim() !== '')
}

export default function TaskTable({ skills, variants, promptFields, onChange }: Props) {
  const [place, setPlace] = useState<Place | null>(null)
  const [replaceRaw, setReplaceRaw] = useState('')
  const [replaceErrors, setReplaceErrors] = useState<string[]>([])
  const [promptCopied, setPromptCopied] = useState(false)

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
    onChange(
      variants.map((variant) => {
        if (variant.variantNo !== target.variantNo) {
          return variant
        }

        // Считаем, какой это по счёту задание нужного умения.
        let seen = -1
        return {
          ...variant,
          tasks: variant.tasks.map((task) => {
            if (task.skillIndex !== target.skillIndex) {
              return task
            }
            seen += 1
            return seen === target.order ? { ...task, ...changes } : task
          }),
        }
      }),
    )
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
    })
    setReplaceRaw('')
    setReplaceErrors([])
  }

  return (
    <div className="tasktable">
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

                            return (
                              <button
                                key={order}
                                type="button"
                                className={
                                  'cell' +
                                  (ready ? ' cell--ready' : ' cell--empty') +
                                  (active ? ' cell--active' : '')
                                }
                                onClick={() => {
                                  setPlace({
                                    variantNo: variant.variantNo,
                                    skillIndex,
                                    order,
                                  })
                                  setReplaceRaw('')
                                  setReplaceErrors([])
                                }}
                                title={task?.text || 'Задание не заполнено'}
                              >
                                {ready ? '✓' : '—'}
                                <span className="cell__text">
                                  {task?.text.slice(0, 28) || 'пусто'}
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
        — — не хватает текста или ответа.
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

          <label className="label label--spaced" htmlFor="task-text">
            Текст задания
          </label>
          <textarea
            id="task-text"
            className="textarea textarea--question"
            value={selected.text}
            onChange={(event) => updateTask(place, { text: event.target.value })}
            rows={3}
            placeholder="Например: Найдите дискриминант уравнения x² − 4x + 3 = 0"
          />

          {selected.answerFormat === 'choice' ? (
            <>
              <p className="label label--spaced">Варианты ответа (отметьте верный)</p>
              <ul className="editor__options">
                {selected.options.map((option, optionIndex) => (
                  <li key={optionIndex} className="optrow">
                    <label className="optrow__radio" title="Это правильный вариант">
                      <input
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
                      className="input"
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
                className="input"
                value={selected.acceptedAnswers.join(' | ')}
                onChange={(event) =>
                  updateTask(place, {
                    // Несколько допустимых записей разделяются вертикальной чертой.
                    acceptedAnswers: event.target.value.split('|').map((item) => item.trim()),
                  })
                }
                placeholder="4"
              />
              <p className="hint">
                Если правильных записей несколько, перечислите их через «|»: например
                «0,5 | 1/2». Регистр, лишние пробелы, запятая вместо точки и «ё» вместо
                «е» учитываются автоматически.
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
            placeholder="D = 16 − 12 = 4"
          />

          {/* ------------------------- Замена через ИИ ------------------------- */}
          <div className="replace">
            <p className="label">Заменить это задание через ИИ</p>
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
