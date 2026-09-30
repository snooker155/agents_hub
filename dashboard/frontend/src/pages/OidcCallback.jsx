import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../components/auth';
import {
  parseFragment, safeNextPath, ssoErrorMessage, writeSessionMeta,
} from '../components/sessionMeta';
import { useI18n } from '../i18n';

// Where the destination waits while the app swaps the login screen for the
// application: the instance of this page that adopted the session may be
// unmounted by that swap, and the one the router mounts next reads it here.
const PENDING_NEXT_KEY = 'agents_hub_oidc_next';

const takePendingNext = () => {
  try {
    const next = window.sessionStorage.getItem(PENDING_NEXT_KEY);
    window.sessionStorage.removeItem(PENDING_NEXT_KEY);
    return next;
  } catch {
    return null;
  }
};

const putPendingNext = (next) => {
  try {
    window.sessionStorage.setItem(PENDING_NEXT_KEY, next);
  } catch {
    // Without sessionStorage the first instance's own navigate still runs.
  }
};

const clearHash = () => {
  try {
    const { pathname, search } = window.location;
    window.history.replaceState(window.history.state, '', `${pathname}${search}`);
  } catch {
    // Nothing to clear in an environment without history.
  }
};

/**
 * Where single sign-on lands the browser: `/login/oidc#token=…&expires_at=…&next=…`
 * or `/login/oidc#error=…`.
 *
 * The backend puts the session in the fragment, never the query, so it is
 * never sent to a server or written to an access log. This page takes it,
 * remembers when it ends (for the silent renewal in AuthContext.jsx), wipes
 * the fragment from the address bar and history, and moves on to where the
 * person was going.
 *
 * Rendered from two places: by the login screen while nobody is signed in
 * (App.jsx shows `Login` in place of every route then, and `Login` hands this
 * path to this page), and by the router once somebody is.
 */
export default function OidcCallback() {
  const { t } = useI18n();
  const { adoptSession } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState(() => parseFragment(window.location.hash).error || '');
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = parseFragment(window.location.hash);
    if (params.error) {
      clearHash();
      return;
    }
    if (!params.token) {
      // A reload after success, the router's second mount, or a stray visit.
      navigate(safeNextPath(takePendingNext() || '/'), { replace: true });
      return;
    }
    const next = safeNextPath(params.next);
    writeSessionMeta({ kind: 'oidc', expires_at: params.expires_at || '' });
    putPendingNext(next);
    clearHash();
    (async () => {
      try {
        const user = await adoptSession(params.token);
        if (!user) {
          writeSessionMeta(null);
          setError('unknown');
          return;
        }
        // The pending destination stays for the router's own mount of this
        // page, which may run before or after this line.
        navigate(next, { replace: true });
      } catch {
        writeSessionMeta(null);
        setError('unknown');
      }
    })();
  }, [adoptSession, navigate]);

  if (error) {
    return (
      <div className="min-h-screen w-full flex items-center justify-center bg-gray-50 px-4">
        <div role="alert" className="w-full max-w-sm bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-3">
          <div className="flex items-center gap-2">
            <span className="h-9 w-9 rounded-lg bg-red-50 flex items-center justify-center text-red-600">
              <AlertTriangle className="w-5 h-5" />
            </span>
            <h1 className="text-base font-semibold text-gray-900">{t('auth.sso.failedTitle')}</h1>
          </div>
          <p className="text-sm text-gray-600">{ssoErrorMessage(t, error)}</p>
          <a href="/login" className="inline-block text-sm font-medium text-indigo-600 hover:text-indigo-700">
            {t('auth.sso.backToLogin')}
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-gray-50 px-4">
      <p className="text-sm text-gray-500 flex items-center gap-2">
        <Loader className="w-4 h-4 animate-spin" /> {t('auth.sso.completing')}
      </p>
    </div>
  );
}
