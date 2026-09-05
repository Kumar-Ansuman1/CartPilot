import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import AiBuyer from './AiBuyer.jsx'
import ModeSwitcher from './ModeSwitcher.jsx'

const path = window.location.pathname.replace(/\/+$/, '') || '/'
const isAiBuyer = path === '/ai-buyer'
const rootElement = isAiBuyer ? <AiBuyer /> : <App />

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ModeSwitcher mode={isAiBuyer ? 'ai' : 'normal'} />
    {rootElement}
  </StrictMode>,
)
