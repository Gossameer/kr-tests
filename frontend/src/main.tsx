/** Точка входа фронтенда: монтируем React-приложение в <div id="root"> из index.html. */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App'
import { AuthProvider } from './lib/auth'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* BrowserRouter даёт «обычные» адреса вида /create без решётки,
        AuthProvider один раз выясняет, кто вошёл, и раздаёт это страницам. */}
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
