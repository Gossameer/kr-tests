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

/** Школьная запись знаков — то же требование, что и во встроенной генерации на сервере. */
const SCHOOL_NOTATION =
  'записывай математику по-школьному: умножение — знак «·» (например, 3 · 4), ' +
  'деление — двоеточие «:» (например, 12 : 3), дроби — через косую черту (3/5); ' +
  'не используй «*» и не пиши «/» для деления чисел'
const PLACEHOLDER_GRADE = '[КЛАСС]'

/** Понятное ИИ описание формата ответа. */
function formatRule(format: AnswerFormat): string {
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
        `${skill.tasksPerVariant}, формат: ${formatRule(skill.answerFormat)}`,
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
Формат ответа: ${formatRule(skill.answerFormat)}
Это задание для варианта ${variantNo}.

Задание, которое нужно заменить (новое должно проверять то же умение,
быть той же сложности, но с другими числами и данными):
${currentText.trim() || '(пока пустое)'}

Запись знаков: ${SCHOOL_NOTATION}.

Верни ТОЛЬКО JSON одного задания, без пояснений и без markdown:

${shape}`
}
