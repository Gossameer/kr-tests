/**
 * Промты для внешнего ИИ.
 *
 * Два разных: один просит составить ВСЮ проверочную работу по списку умений,
 * второй — заменить ОДНО задание, если учителю не понравилось конкретное.
 *
 * Оба просят вернуть строго наш JSON. Если ИИ всё же добавит текст вокруг
 * или обернёт ответ в ```json — мы это вырежем сами (см. parseTestJson.ts).
 */

import type { AnswerFormat, SkillDraft } from '../types'

/** Что учитель указывает перед копированием промта. */
export type PromptFields = {
  subject: string
  grade: string
}

const PLACEHOLDER_SUBJECT = '[ПРЕДМЕТ И ТЕМА]'

/** Ответ в условии — задание ничего не проверяет. То же требование — на сервере. */
const NO_ANSWER_IN_TEXT =
  'НИКОГДА не включай ответ в текст задания: в условии не должно быть ни правильного ответа, ' +
  'ни подсказки, которая его выдаёт (например, слова с уже выделенной ударной буквой)'

/** Требование к заданиям на ударение — как во встроенной генерации. */
function stressRule(skill: SkillDraft): string {
  if (!/ударен|орфоэп/i.test(skill.title)) {
    return ''
  }
  return skill.answerFormat === 'choice'
    ? ' Это задание на ударение: в тексте слово строчными буквами, а варианты ответа — ' +
        'одно и то же слово с ударением на разных слогах, ударная гласная ЗАГЛАВНОЙ ' +
        '(например "звОнит", "звонИт"), от 2 до 4 вариантов.'
    : ' Это задание на ударение: в тексте слово строчными буквами, в "answers" — слово ' +
        'с ударной гласной ЗАГЛАВНОЙ буквой (например "звонИт").'
}

/** Школьная запись знаков — то же требование, что и во встроенной генерации на сервере. */
const SCHOOL_NOTATION =
  'математические выражения в тексте задания, вариантах ответа и решении пиши формулами ' +
  'LaTeX строго между знаками доллара: $...$ (другие обозначения формул не используй, ' +
  'каждый открытый $ закрывай); запись школьная: умножение — \\cdot ($3 \\cdot 4$), ' +
  'деление — двоеточие ($12 : 3$), дроби — \\frac ($\\frac{3}{5}$), смешанные числа — ' +
  '$2\\frac{1}{3}$, десятичная запятая — {,} ($2{,}5$), степень — $x^{2}$, корень — ' +
  '$\\sqrt{x}$; не используй «*» и «/». В JSON обратную черту удваивай: ' +
  '"$\\\\frac{3}{5}$". В "answers" разметки НЕТ: ответ записан так, как его наберёт ' +
  'ученик с клавиатуры — 3/5, 2 1/3, 2,5, -4, без $ и без команд LaTeX'
const PLACEHOLDER_GRADE = '[КЛАСС]'

/** Понятное ИИ описание формата ответа. */
function formatRule(format: AnswerFormat, stress = false): string {
  if (format === 'choice' && stress) {
    // У слова из двух слогов четырёх разных ударений не бывает.
    return '"format": "choice", от 2 до 4 вариантов ответа в "options" и номер верного в "correct" (с нуля)'
  }
  return format === 'choice'
    ? '"format": "choice", 4 варианта ответа в "options" и номер верного в "correct" (с нуля)'
    : '"format": "input", список допустимых ответов в "answers"'
}

/** Список умений в виде, пригодном для промта. */
function skillsBlock(skills: SkillDraft[]): string {
  return skills
    .map(
      (skill, index) =>
        `${index + 1}. ${skill.title} — заданий в каждом варианте: ` +
        `${skill.tasksPerVariant}, формат: ${formatRule(skill.answerFormat, /ударен|орфоэп/i.test(skill.title))}.` +
        stressRule(skill),
    )
    .join('\n')
}

/** Промт на всю проверочную работу. */
export function buildTestPrompt(
  fields: PromptFields,
  skills: SkillDraft[],
  variantsCount: number,
): string {
  const subject = fields.subject.trim() || PLACEHOLDER_SUBJECT
  const grade = fields.grade.trim() || PLACEHOLDER_GRADE
  const tasksPerVariant = skills.reduce((sum, skill) => sum + skill.tasksPerVariant, 0)

  return `Составь проверочную работу по предмету и теме: ${subject}
Класс: ${grade}
Вариантов: ${variantsCount}

Проверяемые умения (номер умения указывай в поле "skill"):
${skillsBlock(skills)}

Требования:
- в КАЖДОМ варианте должны быть задания на ВСЕ умения, ровно в указанном количестве;
- всего заданий в каждом варианте: ${tasksPerVariant};
- варианты равной сложности: одинаковые типы заданий, разные числа и данные;
- задания не должны повторяться между вариантами;
- у заданий с вводом ответа перечисли в "answers" все правильные формы записи
  (например "0,5" и "0.5"), ответ должен быть коротким — число или несколько слов;
- к каждому заданию добавь краткое решение в "solution" (1–2 строки, для учителя);
- ${NO_ANSWER_IN_TEXT};
- ${SCHOOL_NOTATION}.

Верни ТОЛЬКО JSON, без пояснений и без markdown, строго такой структуры:

{
  "variants": [
    {
      "variant": 1,
      "tasks": [
        {
          "skill": 1,
          "text": "Текст задания",
          "format": "input",
          "answers": ["12", "12.0"],
          "solution": "Краткое решение"
        },
        {
          "skill": 2,
          "text": "Текст задания",
          "format": "choice",
          "options": ["Вариант А", "Вариант Б", "Вариант В", "Вариант Г"],
          "correct": 0,
          "solution": "Краткое решение"
        }
      ]
    }
  ]
}

Поле "skill" — номер умения из списка выше. Поле "correct" — номер правильного
варианта в массиве "options", нумерация с нуля.`
}

/** Промт на замену одного задания. */
export function buildReplacePrompt(
  fields: PromptFields,
  skill: SkillDraft,
  variantNo: number,
  currentText: string,
): string {
  const subject = fields.subject.trim() || PLACEHOLDER_SUBJECT
  const grade = fields.grade.trim() || PLACEHOLDER_GRADE

  const shape =
    skill.answerFormat === 'choice'
      ? `{
  "text": "Текст задания",
  "format": "choice",
  "options": ["Вариант А", "Вариант Б", "Вариант В", "Вариант Г"],
  "correct": 0,
  "solution": "Краткое решение"
}`
      : `{
  "text": "Текст задания",
  "format": "input",
  "answers": ["12", "12.0"],
  "solution": "Краткое решение"
}`

  return `Придумай ОДНО новое задание для проверочной работы.

Предмет и тема: ${subject}
Класс: ${grade}
Проверяемое умение: ${skill.title}
Формат ответа: ${formatRule(skill.answerFormat, /ударен|орфоэп/i.test(skill.title))}
Это задание для варианта ${variantNo}.

Задание, которое нужно заменить (новое должно проверять то же умение,
быть той же сложности, но с другими числами и данными):
${currentText.trim() || '(пока пустое)'}

${NO_ANSWER_IN_TEXT}.${stressRule(skill)}
Запись знаков: ${SCHOOL_NOTATION}.

Верни ТОЛЬКО JSON одного задания, без пояснений и без markdown:

${shape}`
}
