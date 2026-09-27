/**
 * Память браузера о работе ученика.
 *
 * Храним две вещи:
 *   * начатую работу (progress) — чтобы после перезагрузки страницы ученик
 *     вернулся в свой вариант, а не начинал заново;
 *   * сданную работу (attempt) — чтобы при повторном открытии ссылки сразу
 *     показать результат.
 *
 * Это только удобство. Настоящая защита — на сервере: он не даст ни начать
 * вторую попытку, ни сдать работу дважды.
 *
 * Все обращения обёрнуты в try/catch: в режиме инкогнито или при запрете
 * данных сайта localStorage может бросить исключение.
 */

import type { StoredAttempt, StoredProgress } from '../types'

function attemptKey(code: string): string {
  return `kr-tests:attempt:${code}`
}

function progressKey(code: string): string {
  return `kr-tests:progress:${code}`
}

/* ===================== Сданная работа ===================== */

export function loadAttempt(code: string): StoredAttempt | null {
  try {
    const raw = window.localStorage.getItem(attemptKey(code))
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
    window.localStorage.setItem(attemptKey(code), JSON.stringify(attempt))
  } catch {
    // Не удалось сохранить — результат уже показан на экране.
  }
}

/* ===================== Начатая работа ===================== */

export function loadProgress(code: string): StoredProgress | null {
  try {
    const raw = window.localStorage.getItem(progressKey(code))
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as StoredProgress
    if (typeof parsed?.attemptToken === 'string' && typeof parsed?.attemptId === 'number') {
      return parsed
    }
    return null
  } catch {
    return null
  }
}

export function saveProgress(code: string, progress: StoredProgress): void {
  try {
    window.localStorage.setItem(progressKey(code), JSON.stringify(progress))
  } catch {
    // игнорируем
  }
}

export function clearProgress(code: string): void {
  try {
    window.localStorage.removeItem(progressKey(code))
  } catch {
    // игнорируем
  }
}
