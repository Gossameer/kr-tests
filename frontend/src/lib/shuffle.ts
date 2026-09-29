/**
 * Перемешивание вопросов и вариантов для конкретного ученика.
 *
 * Требование: порядок у каждого ученика свой, но НЕ меняется при перезагрузке
 * страницы — иначе ученик, обновивший страницу, увидит другую нумерацию и
 * запутается.
 *
 * Как добились: один раз генерируем случайное число (seed) и храним его в
 * localStorage рядом с кодом проверочной работы. Перемешивание считается из seed
 * математически, поэтому при том же seed порядок всегда получается тот же.
 *
 * Правильность ответов от порядка не зависит: на сервер уходят id вопроса
 * и id варианта, а не их номера на экране.
 */

/**
 * Простой генератор псевдослучайных чисел (mulberry32).
 *
 * Обычный Math.random() не подходит: он не принимает seed, поэтому при
 * перезагрузке дал бы другой порядок.
 */
function makeRandom(seed: number): () => number {
  let state = seed >>> 0 // приводим к 32-битному целому без знака

  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Перемешивает копию массива (алгоритм Фишера — Йетса).
 * Исходный массив не меняется: в React данные из состояния править нельзя.
 */
export function shuffleWithSeed<T>(items: T[], seed: number): T[] {
  const random = makeRandom(seed)
  const result = [...items]

  for (let i = result.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1))
    ;[result[i], result[j]] = [result[j], result[i]]
  }

  return result
}

/**
 * Возвращает seed для этой проверочной работы на этом устройстве.
 * При первом открытии создаёт его и запоминает.
 */
export function getOrCreateSeed(code: string): number {
  const key = `kr-tests:seed:${code}`

  try {
    const saved = window.localStorage.getItem(key)
    if (saved !== null) {
      const parsed = Number(saved)
      if (Number.isFinite(parsed)) {
        return parsed
      }
    }

    const seed = Math.floor(Math.random() * 2 ** 31)
    window.localStorage.setItem(key, String(seed))
    return seed
  } catch {
    // localStorage недоступен (инкогнито, запрет данных сайта).
    // Возвращаем устойчивое значение из самого кода: порядок будет одинаковым
    // у всех таких учеников, но хотя бы не поменяется при перезагрузке.
    let fallback = 7
    for (const char of code) {
      fallback = (fallback * 31 + char.charCodeAt(0)) % 2 ** 31
    }
    return fallback
  }
}
