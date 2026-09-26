/**
 * Редактор вопросов: то, что учитель правит руками.
 *
 * Один и тот же редактор используется и при наборе с нуля, и после загрузки
 * ответа ИИ — чтобы вопросы от ИИ можно было спокойно поправить.
 *
 * Компонент ничего не хранит сам: список вопросов лежит в состоянии страницы,
 * а сюда приходит через props вместе с функцией onChange. Это обычный для React
 * приём: «владелец данных один, меняет их тоже он».
 */

import type { DraftQuestion } from '../types'

type Props = {
  questions: DraftQuestion[]
  onChange: (questions: DraftQuestion[]) => void
}

/** Пустой вопрос с двумя пустыми вариантами — минимальная заготовка. */
function emptyQuestion(): DraftQuestion {
  return { text: '', options: ['', ''], correct: 0 }
}

/** Буква варианта: 0 → А, 1 → Б и так далее. */
function optionLetter(index: number): string {
  return 'АБВГДЕЖЗИК'[index] ?? String(index + 1)
}

export default function QuestionEditor({ questions, onChange }: Props) {
  /** Заменяет один вопрос новым значением, остальные оставляет как были. */
  function updateQuestion(index: number, changes: Partial<DraftQuestion>) {
    onChange(
      questions.map((question, position) =>
        position === index ? { ...question, ...changes } : question,
      ),
    )
  }

  function addQuestion() {
    onChange([...questions, emptyQuestion()])
  }

  function removeQuestion(index: number) {
    onChange(questions.filter((_, position) => position !== index))
  }

  /** Переставляет вопрос на одну позицию вверх или вниз. */
  function moveQuestion(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= questions.length) {
      return
    }

    const reordered = [...questions]
    // Обмен двух элементов местами.
    ;[reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    onChange(reordered)
  }

  function updateOption(questionIndex: number, optionIndex: number, value: string) {
    const question = questions[questionIndex]
    const options = question.options.map((option, position) =>
      position === optionIndex ? value : option,
    )
    updateQuestion(questionIndex, { options })
  }

  function addOption(questionIndex: number) {
    const question = questions[questionIndex]
    updateQuestion(questionIndex, { options: [...question.options, ''] })
  }

  function removeOption(questionIndex: number, optionIndex: number) {
    const question = questions[questionIndex]
    if (question.options.length <= 2) {
      return // меньше двух вариантов быть не может
    }

    const options = question.options.filter((_, position) => position !== optionIndex)

    // Правильный вариант мог сдвинуться или быть удалён — пересчитываем его номер.
    let correct = question.correct
    if (optionIndex === question.correct) {
      correct = 0 // удалили правильный — отмечаем первый, учитель поправит
    } else if (optionIndex < question.correct) {
      correct = question.correct - 1
    }

    updateQuestion(questionIndex, { options, correct })
  }

  return (
    <div className="editor">
      {questions.length === 0 && (
        <p className="empty">
          Вопросов пока нет. Нажмите «Добавить вопрос» или загрузите их на вкладке «Из ИИ».
        </p>
      )}

      <ol className="editor__list">
        {questions.map((question, questionIndex) => (
          <li key={questionIndex} className="qcard">
            <div className="qcard__head">
              <span className="qcard__number">Вопрос {questionIndex + 1}</span>

              <div className="qcard__tools">
                <button
                  type="button"
                  className="iconbtn"
                  onClick={() => moveQuestion(questionIndex, -1)}
                  disabled={questionIndex === 0}
                  title="Переместить выше"
                  aria-label={`Переместить вопрос ${questionIndex + 1} выше`}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="iconbtn"
                  onClick={() => moveQuestion(questionIndex, 1)}
                  disabled={questionIndex === questions.length - 1}
                  title="Переместить ниже"
                  aria-label={`Переместить вопрос ${questionIndex + 1} ниже`}
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="iconbtn iconbtn--danger"
                  onClick={() => removeQuestion(questionIndex)}
                  title="Удалить вопрос"
                  aria-label={`Удалить вопрос ${questionIndex + 1}`}
                >
                  ✕
                </button>
              </div>
            </div>

            <textarea
              className="textarea textarea--question"
              value={question.text}
              onChange={(event) => updateQuestion(questionIndex, { text: event.target.value })}
              placeholder="Текст вопроса"
              rows={2}
            />

            <p className="hint">Отметьте точкой правильный вариант:</p>

            <ul className="editor__options">
              {question.options.map((option, optionIndex) => (
                <li key={optionIndex} className="optrow">
                  {/* Радиокнопка внутри label — нажимать можно и по букве варианта. */}
                  <label className="optrow__radio" title="Это правильный вариант">
                    <input
                      type="radio"
                      name={`correct-${questionIndex}`}
                      checked={question.correct === optionIndex}
                      onChange={() => updateQuestion(questionIndex, { correct: optionIndex })}
                    />
                    <span className="optrow__letter">{optionLetter(optionIndex)}</span>
                  </label>

                  <input
                    className="input"
                    value={option}
                    onChange={(event) =>
                      updateOption(questionIndex, optionIndex, event.target.value)
                    }
                    placeholder={`Вариант ${optionLetter(optionIndex)}`}
                  />

                  <button
                    type="button"
                    className="iconbtn iconbtn--danger"
                    onClick={() => removeOption(questionIndex, optionIndex)}
                    disabled={question.options.length <= 2}
                    title={
                      question.options.length <= 2
                        ? 'Нужно хотя бы два варианта'
                        : 'Удалить вариант'
                    }
                    aria-label={`Удалить вариант ${optionLetter(optionIndex)}`}
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>

            <button
              type="button"
              className="btn btn--small btn--ghost"
              onClick={() => addOption(questionIndex)}
              disabled={question.options.length >= 10}
            >
              + вариант
            </button>
          </li>
        ))}
      </ol>

      <button type="button" className="btn btn--ghost" onClick={addQuestion}>
        + Добавить вопрос
      </button>
    </div>
  )
}
