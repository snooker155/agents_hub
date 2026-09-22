import React from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RefreshCw, LayoutDashboard } from 'lucide-react';
import { I18nContext } from '../i18n';

/**
 * Catches a render error in one route and shows it instead of losing the app.
 *
 * Without this, a single page that throws while rendering takes the whole tree
 * down to a blank screen, and the only route back is the browser's reload
 * button. The boundary keeps the shell, the sidebar and every other route
 * alive, names what went wrong, and offers the two things that actually help:
 * reload this page, or leave for one that works.
 *
 * A class component because that is still the only way to catch a render error
 * in React; `contextType` is how it reaches the translator, since hooks are not
 * available here.
 */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // The fallback shows the message; the stack belongs in the console, where
    // a developer is already looking and a support request can be pasted from.
    console.error('Route render failed:', error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const t = this.context?.t || ((key) => key);
    const message = error?.message || String(error);
    return (
      <div className="h-full w-full flex items-center justify-center p-8">
        <div className="max-w-lg w-full rounded-2xl border border-red-200 bg-white p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-gray-900">
                {t('errorBoundary.title')}
              </h2>
              <p className="mt-1 text-sm text-gray-500 leading-relaxed">
                {t('errorBoundary.description')}
              </p>
            </div>
          </div>
          <div className="mt-4 rounded-lg bg-gray-50 border border-gray-200 p-3">
            <div className="text-[10px] uppercase tracking-wider text-gray-400 font-semibold">
              {t('errorBoundary.detailsLabel')}
            </div>
            <pre className="mt-1 text-xs text-red-700 whitespace-pre-wrap break-words font-mono">
              {message}
            </pre>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              {t('errorBoundary.reload')}
            </button>
            <Link
              to="/dashboard"
              className="inline-flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 transition-colors"
            >
              <LayoutDashboard className="w-4 h-4" />
              {t('errorBoundary.backToDashboard')}
            </Link>
          </div>
        </div>
      </div>
    );
  }
}

ErrorBoundary.contextType = I18nContext;

export default ErrorBoundary;
