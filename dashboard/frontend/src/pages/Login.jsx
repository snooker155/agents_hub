import React, { useState } from 'react';
import {
  Building2, KeyRound, Loader, LogIn, ShieldPlus,
} from 'lucide-react';
import { oidcStartUrl } from '../api';
import { useAuth } from '../components/auth';
import { parseFragment, ssoErrorMessage } from '../components/sessionMeta';
import { useI18n } from '../i18n';
import OidcCallback from './OidcCallback';

const inputCls = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm '
  + 'focus:ring-2 focus:ring-indigo-500 focus:outline-none';

/**
 * The login screen, shown only in `multi` mode and only while nobody is
 * logged in. App.jsx renders it instead of the application, so there is no
 * route to bookmark past it and no half-rendered page behind it.
 *
 * On a first run it is a bootstrap form instead: with no accounts there is no
 * administrator to create the first one, so the backend keeps
 * `POST /api/auth/bootstrap` open until exactly one exists.
 *
 * With single sign-on configured (`auth.oidc`) it offers the provider's
 * button, and the password form only while `features.local_passwords` is on.
 * With passwords off the form still exists behind a small link: that is the
 * administrators' way in when the provider is down (the backend refuses a
 * password to anybody else).
 *
 * Because it replaces every route while nobody is signed in, it is also what
 * renders at `/login/oidc`, where the provider sends the browser back: that
 * path is handed to the callback page.
 */
export default function Login() {
  if (typeof window !== 'undefined' && window.location.pathname.startsWith('/login/oidc')) {
    return <OidcCallback />;
  }
  return <LoginForm />;
}

/** Where to go after signing in: here, unless "here" is a login path. */
const currentPath = () => {
  const { pathname, search } = window.location;
  if (!pathname || pathname === '/' || pathname.startsWith('/login')) return '';
  return `${pathname}${search || ''}`;
};

function LoginForm() {
  const { t } = useI18n();
  const {
    bootstrapRequired, login, bootstrap, oidc, features,
  } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() => {
    const code = parseFragment(window.location.hash).error;
    return code ? ssoErrorMessage(t, code) : '';
  });
  const [showPasswordForm, setShowPasswordForm] = useState(false);

  const firstRun = bootstrapRequired;
  // Default on: a backend that predates the feature flag has only passwords.
  const passwordsOn = features?.local_passwords !== false;
  const passwordForm = firstRun || passwordsOn || showPasswordForm;

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    if (firstRun && password !== confirm) {
      setError(t('auth.login.passwordsDiffer'));
      return;
    }
    setBusy(true);
    try {
      if (firstRun) await bootstrap(username, password);
      else await login(username, password);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(detail || t('auth.login.failed'));
    } finally {
      setBusy(false);
    }
  };

  const Icon = firstRun ? ShieldPlus : KeyRound;

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-gray-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex items-center gap-2">
          <span className="h-9 w-9 rounded-lg bg-indigo-50 flex items-center justify-center text-indigo-600">
            <Icon className="w-5 h-5" />
          </span>
          <div>
            <h1 className="text-base font-semibold text-gray-900">
              {firstRun ? t('auth.login.bootstrapTitle') : t('auth.login.title')}
            </h1>
            <p className="text-xs text-gray-500">
              {firstRun ? t('auth.login.bootstrapHint') : t('auth.login.hint')}
            </p>
          </div>
        </div>

        {oidc && !firstRun && (
          <a href={oidcStartUrl(currentPath())}
            className="w-full flex items-center justify-center gap-2 border border-gray-200 hover:bg-gray-50 text-gray-800 px-3 py-2 rounded-lg text-sm font-medium">
            <Building2 className="w-4 h-4" />
            {t('auth.sso.signInWith', { provider: oidc.provider_name })}
          </a>
        )}

        {oidc && !firstRun && passwordForm && (
          <p className="text-center text-xs text-gray-400">{t('auth.sso.or')}</p>
        )}

        {!passwordForm && (
          <>
            {error && <p className="text-xs text-red-600">{error}</p>}
            <button type="button" onClick={() => setShowPasswordForm(true)}
              className="w-full text-center text-xs text-gray-500 hover:text-gray-700">
              {t('auth.sso.passwordsOff')}
            </button>
          </>
        )}

        {passwordForm && (
          <>
            <div>
              <label htmlFor="auth-username" className="text-sm font-medium text-gray-700">
                {t('auth.fields.username')}
              </label>
              <input id="auth-username" value={username} autoComplete="username" autoFocus
                onChange={(e) => setUsername(e.target.value)} className={inputCls} />
            </div>

            <div>
              <label htmlFor="auth-password" className="text-sm font-medium text-gray-700">
                {t('auth.fields.password')}
              </label>
              <input id="auth-password" type="password" value={password}
                autoComplete={firstRun ? 'new-password' : 'current-password'}
                onChange={(e) => setPassword(e.target.value)} className={inputCls} />
            </div>

            {firstRun && (
              <div>
                <label htmlFor="auth-confirm" className="text-sm font-medium text-gray-700">
                  {t('auth.fields.confirmPassword')}
                </label>
                <input id="auth-confirm" type="password" value={confirm} autoComplete="new-password"
                  onChange={(e) => setConfirm(e.target.value)} className={inputCls} />
              </div>
            )}

            {error && <p className="text-xs text-red-600">{error}</p>}

            <button type="submit" disabled={busy || !username || !password}
              className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
              {busy ? <Loader className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
              {firstRun ? t('auth.login.createAdmin') : t('auth.login.submit')}
            </button>
          </>
        )}
      </form>
    </div>
  );
}
