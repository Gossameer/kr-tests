/**
 * Редактор списка умений.
 *
 * Умение — это то, что проверяет контрольная («Находить дискриминант»).
 * У каждого умения свои число заданий в варианте и формат ответа.
 *
 * Компонент ничего не хранит сам: список лежит в состоянии страницы и приходит
 * сюда через props вместе с функцией onChange.
 */

import type { AnswerFormat, SkillDraft } from '../types'

type Props = {
  skills: SkillDraft[]
  onChange: (skills: SkillDraft[]) => void
}

/** Заготовка нового умения: одно задание, ввод ответа. */
function emptySkill(): SkillDraft {
  return { title: '', tasksPerVariant: 1, answerFormat: 'input' }
}

export default function SkillEditor({ skills, onChange }: Props) {
  function update(index: number, changes: Partial<SkillDraft>) {
    onChange(skills.map((skill, position) =>
      position === index ? { ...skill, ...changes } : skill,
    ))
  }

  function add() {
    onChange([...skills, emptySkill()])
  }

  function remove(index: number) {
    onChange(skills.filter((_, position) => position !== index))
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= skills.length) {
      return
    }
    const reordered = [...skills]
    ;[reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    onChange(reordered)
  }

  return (
    <div className="editor">
      {skills.length === 0 && (
        <p className="empty">
          Умений пока нет. Добавьте первое — например «Решать квадратные уравнения».
        </p>
      )}

      <ol className="editor__list">
        {skills.map((skill, index) => (
          <li key={index} className="qcard">
            <div className="qcard__head">
              <span className="qcard__number">Умение {index + 1}</span>
              <div className="qcard__tools">
                <button
                  type="button"
                  className="iconbtn"
                  onClick={() => move(index, -1)}
                  disabled={index === 0}
                  title="Переместить выше"
                  aria-label={`Переместить умение ${index + 1} выше`}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="iconbtn"
                  onClick={() => move(index, 1)}
                  disabled={index === skills.length - 1}
                  title="Переместить ниже"
                  aria-label={`Переместить умение ${index + 1} ниже`}
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="iconbtn iconbtn--danger"
                  onClick={() => remove(index)}
                  title="Удалить умение"
                  aria-label={`Удалить умение ${index + 1}`}
                >
                  ✕
                </button>
              </div>
            </div>

            <input
              className="input"
              value={skill.title}
              onChange={(event) => update(index, { title: event.target.value })}
              placeholder="Что проверяем: например «Находить дискриминант»"
            />

            <div className="fields fields--inline">
              <div className="field field--narrow">
                <label className="label" htmlFor={`skill-count-${index}`}>
                  Заданий
                </label>
                <input
                  id={`skill-count-${index}`}
                  className="input"
                  type="number"
                  min={1}
                  max={10}
                  value={skill.tasksPerVariant}
                  onChange={(event) =>
                    update(index, {
                      // Пустое поле не должно ломать число: подставляем 1.
                      tasksPerVariant: Math.max(1, Number(event.target.value) || 1),
                    })
                  }
                />
                <p className="hint">в каждом варианте</p>
              </div>

              <div className="field">
                <label className="label" htmlFor={`skill-format-${index}`}>
                  Формат ответа
                </label>
                <select
                  id={`skill-format-${index}`}
                  className="input select"
                  value={skill.answerFormat}
                  onChange={(event) =>
                    update(index, { answerFormat: event.target.value as AnswerFormat })
                  }
                >
                  <option value="input">Ввод ответа</option>
                  <option value="choice">Выбор из вариантов</option>
                </select>
                <p className="hint">
                  При вводе ответа ученик не угадывает из четырёх — проверка честнее.
                </p>
              </div>
            </div>
          </li>
        ))}
      </ol>

      <button type="button" className="btn btn--ghost" onClick={add}>
        + Добавить умение
      </button>
    </div>
  )
}
