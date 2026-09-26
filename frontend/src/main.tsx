/** Точка входа фронтенда: монтируем React-приложение в <div id="root"> из index.html. */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* BrowserRouter даёт приложению «обычные» адреса вида /create без решётки. */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
