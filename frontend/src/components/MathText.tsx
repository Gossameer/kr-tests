/**
 * Текст с формулами: куски между $...$ и $$...$$ рисует KaTeX, остальное — как есть.
 *
 * Текст без знака $ возвращается без изменений — как было до формул.
 * KaTeX подгружается только при первой формуле; пока он грузится (и если
 * формулу не разобрать), на месте формулы виден её исходный текст.
 */

import { Fragment, Suspense, lazy } from 'react'
import { splitFormulas } from '../lib/formula'

const KatexSpan = lazy(() => import('./KatexSpan'))

type Props = { text: string }

export default function MathText({ text }: Props) {
  if (!text.includes('$')) {
    return <>{text}</>
  }

  return (
    <>
      {splitFormulas(text).map((part, index) =>
        part.kind === 'text' ? (
          <Fragment key={index}>{part.value}</Fragment>
        ) : (
          <Suspense key={index} fallback={<span className="formula">{part.source}</span>}>
            <KatexSpan latex={part.value} display={part.kind === 'display'} source={part.source} />
          </Suspense>
        ),
      )}
    </>
  )
}

/** Формула без $ — для предпросмотра ответа ученика. */
export function MathFormula({ latex, source }: { latex: string; source: string }) {
  return (
    <Suspense fallback={<span className="formula">{source}</span>}>
      <KatexSpan latex={latex} source={source} />
    </Suspense>
  )
}
