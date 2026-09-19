import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './theme.css'
import App from './App.jsx'
import { WorkspaceProvider } from './components/WorkspaceContext';
import { ThemeProvider } from './components/ThemeContext';
import { I18nProvider } from './i18n'
import { StreamProvider } from './components/StreamContext';
import ToastProvider from './components/ToastProvider'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <I18nProvider>
      <ThemeProvider>
        <ToastProvider>
          <WorkspaceProvider>
            <StreamProvider>
              <App />
            </StreamProvider>
          </WorkspaceProvider>
        </ToastProvider>
      </ThemeProvider>
    </I18nProvider>
  </StrictMode>,
)
