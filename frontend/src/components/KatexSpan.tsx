/**
 * Одна формула, нарисованная KaTeX.
 *
 * Файл подгружается отдельным куском вместе с самим KaTeX и его шрифтами —
 * только когда на странице встретилась первая формула (см. MathText.tsx).
 *
 * Рисуем через katex.render — он собирает узлы DOM, а не строку HTML. Это важно
 * для сервера: строгая политика CSP запрещает атрибут style в разметке, но
 * стили, выставленные из скрипта, разрешает. Со строкой (renderToString)
 * формулы на сервере потеряли бы вёрстку.
 */

import katex from 'katex'
import 'katex/dist/katex.min.css'
import { useLayoutEffect, useRef } from 'react'

type Props = {
  latex: string
  display?: boolean
  /** Что показать, если формулу не разобрать: исходный текст вместе с $. */
  source: string
}

export default function KatexSpan({ latex, display = false, source }: Props) {
  const holder = useRef<HTMLSpanElement>(null)

  // Содержимым этого <span> управляет KaTeX, а не React: детей у него в JSX нет.
  useLayoutEffect(() => {
    const element = holder.current
    if (element === null) {
      return
    }
    try {
      katex.render(latex, element, {
        displayMode: display,
        throwOnError: true,
        // Русские буквы и «·» внутри формулы — не ошибка.
        strict: 'ignore',
        trust: false,
      })
      element.classList.remove('formula--broken')
      element.removeAttribute('title')
    } catch {
      // Битая формула не должна ломать страницу: показываем то, что написано.
      element.textContent = source
      element.classList.add('formula--broken')
      element.title = 'Ошибка в формуле — показан исходный текст'
    }
  }, [latex, display, source])

  return <span ref={holder} className={display ? 'formula formula--display' : 'formula'} />
}
