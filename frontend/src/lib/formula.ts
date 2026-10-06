/**
 * Формулы в текстах заданий: разметка $...$ и $$...$$ (LaTeX, рисует KaTeX).
 *
 * Здесь только работа со строками — сам KaTeX подгружается отдельным куском
 * (см. components/MathText.tsx), и страницам без формул он не нужен.
 * Те же правила разбора — на сервере, в app/formulas.py.
 */

export type FormulaPart =
  | { kind: 'text'; value: string }
  | { kind: 'inline' | 'display'; value: string; source: string }

/** Позиция закрывающего знака или -1. «\$» и прочие «\x» пропускаем. */
function findClosing(text: string, start: number, mark: string): number {
  let position = start
  while (position < text.length) {
    if (text[position] === '\\') {
      position += 2
      continue
    }
    if (text.startsWith(mark, position)) {
      return position
    }
    position += 1
  }
  return -1
}

/**
 * Делит текст на обычные куски и формулы.
 * Незакрытый знак $ формулой не считается: остаток показываем как есть.
 */
export function splitFormulas(text: string): FormulaPart[] {
  const parts: FormulaPart[] = []
  let plain = ''
  let position = 0

  const flush = () => {
    if (plain !== '') {
      parts.push({ kind: 'text', value: plain })
      plain = ''
    }
  }

  while (position < text.length) {
    const char = text[position]
    if (char === '\\' && text[position + 1] === '$') {
      plain += '$'
      position += 2
      continue
    }
    if (char !== '$') {
      plain += char
      position += 1
      continue
    }

    const mark = text.startsWith('$$', position) ? '$$' : '$'
    const end = findClosing(text, position + mark.length, mark)
    if (end === -1) {
      plain += text.slice(position)
      break
    }
    flush()
    parts.push({
      kind: mark === '$$' ? 'display' : 'inline',
      value: text.slice(position + mark.length, end),
      source: text.slice(position, end + mark.length),
    })
    position = end + mark.length
  }

  flush()
  return parts
}

/** Сколько в тексте знаков $, не считая «\$». */
function countDollars(text: string): number {
  return (text.replace(/\\[\s\S]/g, '').match(/\$/g) ?? []).length
}

/** Остался ли в тексте знак $ без пары. */
export function hasUnclosedDollar(text: string): boolean {
  return splitFormulas(text.replace(/\\\$/g, '')).some(
    (part) => part.kind === 'text' && part.value.includes('$'),
  )
}

/** Курсор стоит внутри формулы? */
export function insideFormula(text: string, position: number): boolean {
  return countDollars(text.slice(0, position)) % 2 === 1
}

/**
 * Обрезает текст для клетки таблицы, не разрывая формулу посередине:
 * половина формулы показалась бы сырой разметкой.
 */
export function shortenText(text: string, limit: number): string {
  if (text.length <= limit) {
    return text
  }
  let result = ''
  for (const part of splitFormulas(text)) {
    const piece = part.kind === 'text' ? part.value.replace(/\$/g, '\\$') : part.source
    if (result.length + piece.length <= limit) {
      result += piece
      continue
    }
    // Текст режем по границе, формулу, которая не влезла, берём целиком:
    // клетка сама обрежет лишнее многоточием, а половина формулы не рисуется.
    result += part.kind === 'text' ? piece.slice(0, limit - result.length) : piece
    break
  }
  return result
}

/* ===================== Вставка из панели кнопок ===================== */

export type SnippetKind =
  | 'frac'
  | 'power'
  | 'index'
  | 'sqrt'
  | 'cdot'
  | 'colon'
  | 'le'
  | 'ge'
  | 'ne'
  | 'pi'
  | 'degree'

export const SNIPPET_BUTTONS: { kind: SnippetKind; label: string; title: string }[] = [
  { kind: 'frac', label: 'a/b', title: 'Дробь' },
  { kind: 'power', label: 'xⁿ', title: 'Степень' },
  { kind: 'index', label: 'xₙ', title: 'Нижний индекс' },
  { kind: 'sqrt', label: '√', title: 'Корень' },
  { kind: 'cdot', label: '·', title: 'Умножение' },
  { kind: 'colon', label: ':', title: 'Деление' },
  { kind: 'le', label: '≤', title: 'Меньше или равно' },
  { kind: 'ge', label: '≥', title: 'Больше или равно' },
  { kind: 'ne', label: '≠', title: 'Не равно' },
  { kind: 'pi', label: 'π', title: 'Число пи' },
  { kind: 'degree', label: '°', title: 'Градус' },
]

/** Знаки: что вставить в обычный текст и что — внутри формулы. */
const SIGNS: Partial<Record<SnippetKind, [plain: string, latex: string]>> = {
  cdot: [' · ', ' \\cdot '],
  colon: [' : ', ' : '],
  le: [' ≤ ', ' \\le '],
  ge: [' ≥ ', ' \\ge '],
  ne: [' ≠ ', ' \\ne '],
  pi: ['π', '\\pi '],
  degree: ['°', '^\\circ'],
}

export type Insertion = { value: string; cursor: number }

/**
 * Вставляет заготовку в позицию курсора (или вокруг выделенного).
 *
 * Знаки (·, ≤, π) в обычном тексте вставляются как есть, внутри формулы —
 * командой LaTeX. Дробь, степень, индекс и корень вне формулы сами
 * оборачиваются в $...$. Курсор встаёт туда, где нужно печатать дальше.
 */
export function insertSnippet(
  text: string,
  start: number,
  end: number,
  kind: SnippetKind,
): Insertion {
  const inside = insideFormula(text, start)
  const selected = text.slice(start, end)
  let before = text.slice(0, start)
  const after = text.slice(end)

  const sign = SIGNS[kind]
  if (sign !== undefined) {
    const piece = sign[inside ? 1 : 0]
    return { value: before + selected + piece + after, cursor: (before + selected + piece).length }
  }

  let body = ''
  // Сколько знаков от начала вставки до места, где печатать дальше.
  let offset = 0
  if (kind === 'frac') {
    body = `\\frac{${selected}}{}`
    offset = selected === '' ? '\\frac{'.length : body.length - 1
  } else if (kind === 'sqrt') {
    body = `\\sqrt{${selected}}`
    offset = selected === '' ? '\\sqrt{'.length : body.length
  } else {
    // Степень и индекс: основание — выделенное или слово перед курсором.
    let base = selected
    if (base === '' && !inside) {
      base = /[\p{L}\p{N}]+$/u.exec(before)?.[0] ?? ''
      before = before.slice(0, before.length - base.length)
    }
    body = `${base}${kind === 'power' ? '^' : '_'}{}`
    offset = body.length - 1
  }

  const open = inside ? '' : '$'
  const close = inside ? '' : '$'
  return {
    value: before + open + body + close + after,
    cursor: before.length + open.length + offset,
  }
}

/* ===================== Предпросмотр ответа ученика ===================== */

const CYRILLIC_WORDS = /\s*[А-Яа-яЁё]+(?:\s+[А-Яа-яЁё]+)*\s*/g
const OPERAND = String.raw`(?:\([^()]*\)|[\p{L}\p{N}.,]+)`

function unwrap(operand: string): string {
  return operand.startsWith('(') && operand.endsWith(')') ? operand.slice(1, -1) : operand
}

/**
 * Ответ, набранный с клавиатуры, → LaTeX для предпросмотра:
 * «3/5» → дробь, «2 1/3» → смешанное число, «2^3» → степень, «sqrt(2)» → корень.
 *
 * null — показывать нечего: в ответе нет ничего, что выглядело бы иначе.
 */
export function answerPreviewLatex(raw: string): string | null {
  const text = raw.trim()
  if (text === '' || text.length > 80) {
    return null
  }
  // Ученик сам набрал разметку — показываем её как есть.
  if (text.includes('\\') || text.includes('$')) {
    const latex = text.replace(/\$/g, '').trim()
    return latex === '' ? null : latex
  }
  if (!/[/^]|sqrt|√|<=|>=|!=|\*/i.test(text) || /[{}#&~]/.test(text)) {
    return null
  }

  const latex = text
    .replace(/%/g, '\\%')
    .replace(/(?:sqrt|√)\s*\(([^()]*)\)/gi, '\\sqrt{$1}')
    .replace(/√\s*([\p{L}\p{N}.,{}]+)/gu, '\\sqrt{$1}')
    .replace(/\^\s*\(([^()]*)\)/g, '^{$1}')
    .replace(/\^\s*(-?[\p{L}\p{N}.,{}]+)/gu, '^{$1}')
    // Смешанное число раньше обычной дроби: «2 1/3».
    .replace(/(\d)\s+(\d+)\s*\/\s*(\d+)/g, '$1\\frac{$2}{$3}')
    .replace(
      new RegExp(`(${OPERAND}(?:\\^\\{[^{}]*\\})?)\\s*/\\s*(${OPERAND}(?:\\^\\{[^{}]*\\})?)`, 'gu'),
      (_, top: string, bottom: string) => `\\frac{${unwrap(top)}}{${unwrap(bottom)}}`,
    )
    .replace(/<=/g, '\\le ')
    .replace(/>=/g, '\\ge ')
    .replace(/!=|<>/g, '\\ne ')
    .replace(/\*/g, '\\cdot ')
    // Русские слова («см», «кг») — прямым шрифтом и с пробелами.
    .replace(CYRILLIC_WORDS, (words) => `\\text{${words}}`)
    // Десятичная запятая — без пробела после неё.
    .replace(/(\d),(\d)/g, '$1{,}$2')

  return latex
}

/* ===================== Ответ ИИ ===================== */

/**
 * Удваивает обратную черту перед командами LaTeX в тексте JSON.
 *
 * ИИ часто пишет в JSON «\frac» с одной чертой. Для JSON «\f» — перевод
 * страницы, «\t» — табуляция, а «\c» — просто ошибка: формула молча портится
 * или ответ не разбирается совсем. То же делает сервер (fix_latex_escapes).
 */
export function fixLatexEscapes(raw: string): string {
  return raw.replace(
    /(?<!\\)((?:\\\\)*)\\(?=[^"\\/bfnrtu]|(?:frac|beta|bar|begin|bullet|backslash|big|forall|neq?|nu|notin|nabla|rho|right|rightarrow|rbrace|rangle|times|tau|theta|text|textrm|textbf|textit|tfrac|to|tg|tan|th|triangle|textstyle)(?![a-zA-Z]))/g,
    '$1\\\\',
  )
}

/**
 * Ответ для «ввода» — без разметки: «$\frac{3}{5}$» → «3/5», «2{,}5» → «2,5».
 * Сервер при проверке снимает разметку и сам; здесь — чтобы учитель видел
 * ответ таким, каким его наберёт ученик.
 */
export function plainAnswer(answer: string): string {
  if (!answer.includes('$') && !answer.includes('\\') && !answer.includes('{')) {
    return answer
  }
  return answer
    .replace(/\$/g, '')
    .replace(/(\d)\s*\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '$1 $2/$3')
    .replace(/\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '$1/$2')
    .replace(/\{,\}/g, ',')
    .replace(/\\cdot\s*/g, '·')
    .replace(/\^\{([^{}]*)\}/g, '^$1')
    .trim()
}
