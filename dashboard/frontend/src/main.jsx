import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { WorkspaceProvider } from './components/WorkspaceContext'
import { ThemeProvider } from './components/ThemeContext'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider>
      <WorkspaceProvider>
        <App />
      </WorkspaceProvider>
    </ThemeProvider>
  </StrictMode>,
)
