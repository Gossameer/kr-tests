/**
 * Кто вошёл — одно место на всё приложение.
 *
 * Провайдер один раз спрашивает сервер «кто я», а страницы берут готовый ответ
 * через useAuth() (см. authContext.ts). Без этого каждая страница дёргала бы
 * /api/auth/me сама.
 *
 * Пароль и токен сессии тут не хранятся: токен лежит в httpOnly-cookie,
 * до которой JavaScript не дотягивается, — так безопаснее.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { fetchMe, login as apiLogin, logout as apiLogout, register as apiRegister } from '../api'
import { AuthContext } from './authContext'
import type { AuthValue } from './authContext'
import type { User } from '../types'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  /** Спрашивает сервер, кто вошёл. Ошибку наружу не пускает. */
  const refresh = useCallback(async () => {
    try {
      setUser(await fetchMe())
    } catch {
      // Сервер недоступен — считаем, что не вошли. Настоящую ошибку человек
      // увидит при попытке войти.
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  // Первый вопрос «кто я» при загрузке страницы.
  useEffect(() => {
    // cancelled защищает от записи состояния после ухода со страницы.
    let cancelled = false

    fetchMe()
      .then((value) => {
        if (!cancelled) setUser(value)
      })
      .catch(() => {
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const value = useMemo<AuthValue>(
    () => ({
      user,
      loading,
      async signIn(email, password) {
        setUser(await apiLogin(email, password))
      },
      async signUp(payload) {
        setUser(await apiRegister(payload))
      },
      async signOut() {
        await apiLogout()
        setUser(null)
      },
      refresh,
    }),
    [user, loading, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
