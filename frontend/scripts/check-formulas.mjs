/**
 * Проверка формул без браузера:  npm test   (нужен Node 22.18+ — он сам читает .ts)
 *
 *   1. Каждая команда LaTeX, которую сервер считает допустимой
 *      (backend/app/formulas.py), действительно рисуется KaTeX. Иначе сервер
 *      пропустил бы формулу, которую ученик увидит сырым текстом.
 *   2. Разбор $...$, вставка из панели кнопок, предпросмотр ответа ученика,
 *      починка «\frac» с одной чертой в JSON.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import katex from 'katex'
import {
  answerPreviewLatex,
  fixLatexEscapes,
  hasUnclosedDollar,
  insertSnippet,
  plainAnswer,
  shortenText,
  splitFormulas,
} from '../src/lib/formula.ts'

const options = { throwOnError: true, strict: 'ignore', trust: false }
const renders = (latex) => {
  try {
    katex.renderToString(latex, options)
    return true
  } catch {
    return false
  }
}

/* ---------- 1. Команды сервера понимает KaTeX ---------- */

const python = readFileSync(new URL('../../backend/app/formulas.py', import.meta.url), 'utf8')
const block = (name) => {
  const match = new RegExp(`^${name} = [({]([\\s\\S]*?)^[)}]`, 'm').exec(python)
  assert.ok(match, `в formulas.py нет ${name}`)
  return match[1]
}
const names = (text, pattern) => [...text.matchAll(pattern)].map((match) => match[1])

const symbols = names(block('SYMBOLS'), /"([A-Za-z]+)":/g)
const functions = names(block('FUNCTIONS'), /"([a-z]+)"/g)
const fractions = names(/^FRACTIONS = \((.*)\)$/m.exec(python)[1], /"([a-z]+)"/g)
const upright = names(/^UPRIGHT = \((.*)\)$/m.exec(python)[1], /"([a-z]+)"/g)
const accents = names(/^ACCENTS = \{(.*)\}$/m.exec(python)[1], /"([a-z]+)":/g)
const escaped = names(block('ESCAPED'), /"(.)":/g)
assert.ok(symbols.length > 60 && functions.length > 10 && fractions.length === 3)

const failed = [
  ...symbols.map((name) => `\\${name}`),
  ...functions.map((name) => `\\${name} x`),
  ...fractions.map((name) => `\\${name}{1}{2}`),
  ...upright.map((name) => `\\${name}{abc}`),
  ...accents.map((name) => `\\${name}{AB}`),
  ...escaped.map((char) => `a\\${char}b`),
].filter((latex) => !renders(latex))
assert.deepEqual(failed, [], 'сервер допускает команды, которых не знает KaTeX')

for (const latex of [
  '2\\frac{1}{3} + 1{,}5',
  '3 \\cdot 4 : 2',
  '\\sqrt[3]{x+1}',
  '90^\\circ',
  '\\left(\\frac{a+b}{2}\\right)^2',
  '\\begin{cases} x+y=5 \\\\ x-y=1 \\end{cases}',
  '5 \\text{ см}^2',
  'S = 5 см · 4 см',
  "f'(x)",
]) {
  assert.ok(renders(latex), `KaTeX не рисует ${latex}`)
}
for (const latex of ['\\frac{1}{', '\\foo', 'x^', '\\left( x']) {
  assert.ok(!renders(latex), `KaTeX неожиданно нарисовал ${latex}`)
}

/* ---------- 2. Разбор текста ---------- */

assert.deepEqual(splitFormulas('Найдите $\\frac{1}{2}$ от 10. $$x^2$$ Цена 5\\$.'), [
  { kind: 'text', value: 'Найдите ' },
  { kind: 'inline', value: '\\frac{1}{2}', source: '$\\frac{1}{2}$' },
  { kind: 'text', value: ' от 10. ' },
  { kind: 'display', value: 'x^2', source: '$$x^2$$' },
  { kind: 'text', value: ' Цена 5$.' },
])
assert.deepEqual(splitFormulas('a $b'), [{ kind: 'text', value: 'a $b' }])
assert.equal(hasUnclosedDollar('a $b$ c $d'), true)
assert.equal(hasUnclosedDollar('a $b$ c 5\\$'), false)
assert.equal(
  shortenText('Вычислите значение $\\frac{1}{2}$ и запишите ответ', 22),
  'Вычислите значение $\\frac{1}{2}$',
)
assert.equal(shortenText('короткий $x$', 28), 'короткий $x$')

/* ---------- 3. Вставка из панели ---------- */

assert.deepEqual(insertSnippet('Найдите ', 8, 8, 'frac'), { value: 'Найдите $\\frac{}{}$', cursor: 15 })
assert.deepEqual(insertSnippet('$1 + $', 5, 5, 'frac'), { value: '$1 + \\frac{}{}$', cursor: 11 })
assert.deepEqual(insertSnippet('Площадь 5 см', 12, 12, 'power'), { value: 'Площадь 5 $см^{}$', cursor: 15 })
assert.deepEqual(insertSnippet('$x$', 2, 2, 'power'), { value: '$x^{}$', cursor: 4 })
assert.deepEqual(insertSnippet('abc', 0, 3, 'sqrt'), { value: '$\\sqrt{abc}$', cursor: 11 })
assert.deepEqual(insertSnippet('3 4', 1, 1, 'cdot'), { value: '3 ·  4', cursor: 4 })
assert.deepEqual(insertSnippet('$3 4$', 2, 2, 'cdot'), { value: '$3 \\cdot  4$', cursor: 9 })
assert.deepEqual(insertSnippet('Угол 90', 7, 7, 'degree'), { value: 'Угол 90°', cursor: 8 })
assert.deepEqual(insertSnippet('$90$', 3, 3, 'degree'), { value: '$90^\\circ$', cursor: 9 })

/* ---------- 4. Предпросмотр ответа ученика ---------- */

assert.equal(answerPreviewLatex('3/5'), '\\frac{3}{5}')
assert.equal(answerPreviewLatex('2 1/3'), '2\\frac{1}{3}')
assert.equal(answerPreviewLatex('2^3'), '2^{3}')
assert.equal(answerPreviewLatex('(a+b)/2'), '\\frac{a+b}{2}')
assert.equal(answerPreviewLatex('x^2/4'), '\\frac{x^{2}}{4}')
assert.equal(answerPreviewLatex('sqrt(2)'), '\\sqrt{2}')
assert.equal(answerPreviewLatex('2,5/3'), '\\frac{2{,}5}{3}')
assert.equal(answerPreviewLatex('5 см^2'), '5\\text{ см}^{2}')
assert.equal(answerPreviewLatex('$\\frac{1}{2}$'), '\\frac{1}{2}')
for (const plain of ['25', 'Париж', '-4', '2,5', '', 'x = 5']) {
  assert.equal(answerPreviewLatex(plain), null, `лишний предпросмотр для «${plain}»`)
}
for (const typed of ['3/5', '2 1/3', '2^3', '(a+b)/2', 'x^2/4', 'sqrt(2)', '5 см^2', '1/2 + 3/4', '2*3', 'x<=5', '50%/2']) {
  assert.ok(renders(answerPreviewLatex(typed)), `предпросмотр «${typed}» не рисуется`)
}

/* ---------- 5. Ответ ИИ ---------- */

const raw = '{"text": "Вычислите $\\frac{1}{2} \\cdot 4 \\times 2$,\\nответ — число", "n": "a\\nb"}'
assert.deepEqual(JSON.parse(fixLatexEscapes(raw)), {
  text: 'Вычислите $\\frac{1}{2} \\cdot 4 \\times 2$,\nответ — число',
  n: 'a\nb',
})
const doubled = JSON.stringify({ text: '$\\frac{1}{2} \\ne \\tau$' })
assert.equal(JSON.parse(fixLatexEscapes(doubled)).text, '$\\frac{1}{2} \\ne \\tau$')
assert.equal(plainAnswer('$\\frac{3}{5}$'), '3/5')
assert.equal(plainAnswer('$2\\frac{1}{3}$'), '2 1/3')
assert.equal(plainAnswer('$2{,}5$'), '2,5')
assert.equal(plainAnswer('Париж'), 'Париж')

console.log(`Формулы: всё в порядке (команд сервера сверено с KaTeX: ${symbols.length + functions.length + fractions.length + upright.length + accents.length + escaped.length})`)
