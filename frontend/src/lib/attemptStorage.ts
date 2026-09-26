/**
 * Память браузера о сданной работе.
 *
 * Зачем: после сдачи ученик не должен снова попасть на тест — при повторном
 * открытии ссылки он сразу видит свой результат.
 *
 * Важно понимать границы такой защиты: localStorage живёт в одном браузере
 * на одном устройстве. Другой браузер, режим инкогнито или очистка данных —
 * и ученик пройдёт тест снова. Настоящая защита возможна только на сервере
 * (например, ограничение по имени и классу), это отдельная задача.
 *
 * Все обращения обёрнуты в try/catch: в режиме инкогнито или при запрете
 * cookies обращение к localStorage может бросить исключение.
 */

import type { StoredAttempt } from '../types'

/** Ключ свой для каждой контрольной, чтобы разные тесты не мешали друг другу. */
function storageKey(code: string): string {
  return `kr-tests:attempt:${code}`
}

export function loadAttempt(code: string): StoredAttempt | null {
  try {
    const raw = window.localStorage.getItem(storageKey(code))
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as StoredAttempt
    // Минимальная проверка: вдруг в хранилище лежит мусор от старой версии.
    if (parsed?.result && typeof parsed.result.score === 'number') {
      return parsed
    }
    return null
  } catch {
    return null
  }
}

export function saveAttempt(code: string, attempt: StoredAttempt): void {
  try {
    window.localStorage.setItem(storageKey(code), JSON.stringify(attempt))
  } catch {
    // Не смогли сохранить — не беда: результат уже показан на экране,
    // просто при повторном открытии ссылки тест откроется заново.
  }
}

export function clearAttempt(code: string): void {
  try {
    window.localStorage.removeItem(storageKey(code))
  } catch {
    // игнорируем
  }
}
