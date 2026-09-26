/**
 * Каркас приложения: шапка, общая обёртка и роутинг.
 * Сам <BrowserRouter> подключён в main.tsx.
 */

import { Link, Route, Routes } from 'react-router'
import CreateTestPage from './pages/CreateTestPage'
import HomePage from './pages/HomePage'
import ResultsPage from './pages/ResultsPage'
import StudentTestPage from './pages/StudentTestPage'

/** Шапка одна на все страницы: по ней видно, что это сервис школы, а не случайный сайт. */
function TopBar() {
  return (
    <header className="topbar">
      <div className="topbar__inner">
        <Link className="topbar__brand" to="/">
          <span className="topbar__school">Школа №2090</span>
          <span className="topbar__dot">·</span>
          <span className="topbar__name">Контрольные работы</span>
        </Link>
      </div>
    </header>
  )
}

export default function App() {
  return (
    <div className="app">
      <TopBar />

      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/create" element={<CreateTestPage />} />
        {/* Ссылка для учеников: /t/<код контрольной> */}
        <Route path="/t/:code" element={<StudentTestPage />} />
        {/* Секретная ссылка учителя на результаты: /r/<длинный токен> */}
        <Route path="/r/:token" element={<ResultsPage />} />
        {/* Любой другой адрес — короткое понятное сообщение вместо пустой страницы. */}
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
