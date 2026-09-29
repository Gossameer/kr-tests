/**
 * Заголовок вкладки браузера: «Создать проверочную работу — Проверочные работы».
 *
 * Учитель держит открытыми несколько вкладок сервиса — по заголовку видно,
 * где какая.
 */

import { useEffect } from 'react'

const SERVICE_NAME = 'Проверочные работы'

export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} — ${SERVICE_NAME}` : SERVICE_NAME
  }, [title])
}
