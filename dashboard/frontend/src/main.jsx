import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './theme.css'
import App from './App.jsx'
import { WorkspaceProvider } from './components/WorkspaceContext';
import { ThemeProvider } from './components/ThemeContext';
import { I18nProvider } from './i18n'
import { StreamProvider } from './components/StreamContext';
import ToastProvider from './components/ToastProvider'

// The demo build (VITE_DEMO=1, `npm run build:demo`) answers every /api call
// from recorded fixtures through a mock service worker. The condition is a
// build-time constant, so a normal build drops the branch and never ships MSW.
const IS_DEMO = import.meta.env.VITE_DEMO === '1';
const DemoBadge = IS_DEMO ? lazy(() => import('./demo/DemoBadge.jsx')) : null;

async function bootstrap() {
  if (IS_DEMO) {
    const { startDemo } = await import('./demo/start.js');
    await startDemo();
  }

  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <I18nProvider>
        <ThemeProvider>
          <ToastProvider>
            <WorkspaceProvider>
              <StreamProvider>
                <App />
                {DemoBadge && (
                  <Suspense fallback={null}>
                    <DemoBadge />
                  </Suspense>
                )}
              </StreamProvider>
            </WorkspaceProvider>
          </ToastProvider>
        </ThemeProvider>
      </I18nProvider>
    </StrictMode>,
  )
}

bootstrap()
