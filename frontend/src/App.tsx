/**
 * Роутинг приложения: какой адрес — какая страница.
 * Сам <BrowserRouter> подключён в main.tsx.
 */

import { Route, Routes } from 'react-router'
import CreateTestPage from './pages/CreateTestPage'
import HomePage from './pages/HomePage'
import ResultsPage from './pages/ResultsPage'
import StudentTestPage from './pages/StudentTestPage'

export default function App() {
  return (
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
            <p className="lead">
              <a href="/">Вернуться на главную</a>
            </p>
          </main>
        }
      />
    </Routes>
  )
}
