/**
 * Роутинг приложения: какой адрес — какая страница.
 * Сам <BrowserRouter> подключён в main.tsx.
 */

import { Route, Routes, useParams } from 'react-router'
import CreateTestPage from './pages/CreateTestPage'
import HomePage from './pages/HomePage'

/**
 * Заглушка страницы ученика. Нужна только чтобы ссылка из «Опубликовать»
 * не открывала пустоту. Настоящая страница прохождения теста — следующий шаг.
 */
function StudentStub() {
  const { code } = useParams()

  return (
    <main className="page">
      <h1>Контрольная {code}</h1>
      <p className="lead">Страница прохождения теста появится на следующем шаге.</p>
    </main>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/create" element={<CreateTestPage />} />
      <Route path="/t/:code" element={<StudentStub />} />
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
