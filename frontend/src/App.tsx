/**
 * Каркас приложения: шапка, меню по роли, роутинг и защита страниц.
 *
 * Страницы делятся на три группы:
 *   * публичные — вход, регистрация и страница ученика /t/:code;
 *   * для вошедших — кабинет, создание проверочной работы, результаты;
 *   * для администратора — учителя, статистика, расход ИИ, настройки.
 */

import { Link, Navigate, Route, Routes, useLocation } from 'react-router'
import type { ReactNode } from 'react'
import { useAuth } from './lib/authContext'
import AdminAiPage from './pages/AdminAiPage'
import AdminSettingsPage from './pages/AdminSettingsPage'
import AdminStatsPage from './pages/AdminStatsPage'
import AdminTeachersPage from './pages/AdminTeachersPage'
import CreateTestPage from './pages/CreateTestPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import HomePage from './pages/HomePage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ResultsPage from './pages/ResultsPage'
import SetPasswordPage from './pages/SetPasswordPage'
import StudentTestPage from './pages/StudentTestPage'

/**
 * Пускает дальше только вошедших.
 *
 * Пока идёт первый запрос «кто я», ничего не решаем: иначе на долю секунды
 * мелькал бы экран входа у уже вошедшего человека.
 */
function Protected({ children, adminOnly = false }: { children: ReactNode; adminOnly?: boolean }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <main className="page">
        <p className="loading">Проверяем вход…</p>
      </main>
    )
  }

  if (user === null) {
    // Запоминаем, куда человек шёл, — после входа вернём его туда же.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  if (adminOnly && user.role !== 'admin') {
    return (
      <main className="page">
        <h1>Раздел только для администратора</h1>
        <p className="lead">
          Если вам нужен доступ, обратитесь к администратору школы.
        </p>
        <Link className="btn btn--primary" to="/">
          К моим работам
        </Link>
      </main>
    )
  }

  return <>{children}</>
}

/** Шапка: название школы, меню по роли, имя вошедшего и выход. */
function TopBar() {
  const { user, signOut } = useAuth()

  async function handleSignOut() {
    try {
      await signOut()
    } catch {
      // Даже если сервер не ответил, на клиенте мы уже «вышли».
    }
  }

  return (
    <header className="topbar">
      <div className="topbar__inner">
        <Link className="topbar__brand" to="/">
          <span className="topbar__school">Школа №2090</span>
          <span className="topbar__dot">·</span>
          <span className="topbar__name">Проверочные работы</span>
        </Link>

        {user !== null && (
          <nav className="topbar__nav">
            <Link className="topbar__link" to="/">
              {user.role === 'admin' ? 'Все работы' : 'Мои работы'}
            </Link>
            <Link className="topbar__link" to="/create">
              Создать
            </Link>
            {user.role === 'admin' && (
              <>
                <Link className="topbar__link" to="/admin/stats">
                  Статистика
                </Link>
                <Link className="topbar__link" to="/admin/teachers">
                  Учителя
                </Link>
                <Link className="topbar__link" to="/admin/ai">
                  ИИ
                </Link>
                <Link className="topbar__link" to="/admin/settings">
                  Настройки
                </Link>
              </>
            )}
          </nav>
        )}

        <div className="topbar__user">
          {user !== null ? (
            <>
              <span className="topbar__person" title={user.email}>
                {user.full_name}
                {user.role === 'admin' && <span className="tag">админ</span>}
              </span>
              <button type="button" className="btn btn--small btn--ghost" onClick={handleSignOut}>
                Выйти
              </button>
            </>
          ) : (
            <Link className="btn btn--small btn--ghost" to="/login">
              Войти
            </Link>
          )}
        </div>
      </div>
    </header>
  )
}

export default function App() {
  return (
    <div className="app">
      <TopBar />

      <Routes>
        {/* Публичное */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot" element={<ForgotPasswordPage />} />
        {/* Ссылки из писем: приглашение и сброс пароля. Вход не нужен. */}
        <Route path="/invite/:token" element={<SetPasswordPage />} />
        <Route path="/reset/:token" element={<SetPasswordPage />} />
        {/* Ссылка для учеников: вход не нужен */}
        <Route path="/t/:code" element={<StudentTestPage />} />

        {/* Для вошедших учителей */}
        <Route
          path="/"
          element={
            <Protected>
              <HomePage />
            </Protected>
          }
        />
        <Route
          path="/create"
          element={
            <Protected>
              <CreateTestPage />
            </Protected>
          }
        />
        <Route
          path="/tests/:testId/results"
          element={
            <Protected>
              <ResultsPage />
            </Protected>
          }
        />

        {/* Только администратор */}
        <Route
          path="/admin/teachers"
          element={
            <Protected adminOnly>
              <AdminTeachersPage />
            </Protected>
          }
        />
        <Route
          path="/admin/stats"
          element={
            <Protected adminOnly>
              <AdminStatsPage />
            </Protected>
          }
        />
        <Route
          path="/admin/ai"
          element={
            <Protected adminOnly>
              <AdminAiPage />
            </Protected>
          }
        />
        <Route
          path="/admin/settings"
          element={
            <Protected adminOnly>
              <AdminSettingsPage />
            </Protected>
          }
        />

        <Route
          path="*"
          element={
            <main className="page">
              <h1>Страница не найдена</h1>
              <p className="lead">Проверьте ссылку — возможно, она скопирована не целиком.</p>
              <Link className="btn btn--primary" to="/">
                На главную
              </Link>
            </main>
          }
        />
      </Routes>
    </div>
  )
}
