/**
 * Список «Мои контрольные» — память браузера учителя.
 *
 * Авторизации в сервисе пока нет, поэтому ссылку на результаты негде посмотреть
 * второй раз. Чтобы она не терялась, после публикации мы запоминаем контрольную
 * здесь, в localStorage. Это ОСОЗНАННОЕ ограничение: список живёт только в этом
 * браузере на этом устройстве — в другом браузере его не будет.
 *
 * Все обращения обёрнуты в try/catch: в режиме инкогнито или при запрете
 * данных сайта localStorage может бросить исключение.
 */

export type MyTest = {
  code: string
  resultsToken: string
  title: string
  questionsCount: number
  /** Дата публикации в формате ISO. */
  createdAt: string
}

const STORAGE_KEY = 'kr-tests:my-tests'
/** Больше хранить незачем: список нужен «на последние недели», а не как архив. */
const MAX_ITEMS = 50

export function loadMyTests(): MyTest[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return []
    }

    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }

    // Отбрасываем мусор от прежних версий: нужны хотя бы код и токен.
    return parsed.filter(
      (item): item is MyTest =>
        item !== null &&
        typeof item === 'object' &&
        typeof (item as MyTest).code === 'string' &&
        typeof (item as MyTest).resultsToken === 'string',
    )
  } catch {
    return []
  }
}

/** Добавляет контрольную в начало списка. Повторная публикация того же кода не дублируется. */
export function rememberTest(test: MyTest): void {
  try {
    const existing = loadMyTests().filter((item) => item.code !== test.code)
    const updated = [test, ...existing].slice(0, MAX_ITEMS)
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
  } catch {
    // Не удалось сохранить — ссылки всё равно показаны на экране публикации.
  }
}

/** Убирает контрольную из списка (например, после её удаления на сервере). */
export function forgetTest(code: string): void {
  try {
    const updated = loadMyTests().filter((item) => item.code !== code)
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
  } catch {
    // игнорируем
  }
}
