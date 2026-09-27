/**
 * Контекст входа: описание значения и хук доступа.
 *
 * Вынесено из auth.tsx, потому что в одном файле с компонентом не должно быть
 * других экспортов — иначе ломается горячая перезагрузка Vite при правках.
 */

import { createContext, useContext } from 'react'
import type { User } from '../types'

export type AuthValue = {
  /** null — никто не вошёл. */
  user: User | null
  /** true, пока не пришёл первый ответ от сервера: страницы ждут его. */
  loading: boolean
  signIn: (email: string, password: string) => Promise<void>
  signUp: (payload: {
    full_name: string
    email: string
    password: string
    school_code: string
  }) => Promise<void>
  signOut: () => Promise<void>
  /** Перечитать пользователя с сервера. */
  refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthValue | null>(null)

/** Доступ к данным о входе из любой страницы. */
export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (value === null) {
    // Такое бывает только при ошибке в коде: страница вне AuthProvider.
    throw new Error('useAuth вызван вне AuthProvider')
  }
  return value
}
