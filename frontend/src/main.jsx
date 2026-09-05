import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import AiBuyer from './AiBuyer.jsx'

const path = window.location.pathname.replace(/\/+$/, '') || '/'
const rootElement = path === '/ai-buyer' ? <AiBuyer /> : <App />

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {rootElement}
  </StrictMode>,
)
