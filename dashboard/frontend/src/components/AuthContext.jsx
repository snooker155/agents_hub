import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  authBootstrap, authLogin, authLogout, getAuthMode, getMe, setSessionToken, getSessionToken,
  oidcStartUrl,
} from '../api';
import { AuthContext, DEFAULT_AUTH, MULTI, SINGLE } from './auth';
import {
  RENEW_CHECK_MS, oidcRenewalDue, readSessionMeta, writeSessionMeta,
} from './sessionMeta';

/**
 * Asks the backend what identity posture it is in, once, at app start.
 *
 * In `single` and `token` mode this is a no-op provider: there is one
 * operator, so there is no user to fetch, no login to offer and nothing on
 * screen to hide behind it. Only `multi` goes on to resolve who the viewer is.
 *
 * The mode is read once and never refetched. Changing it is an edit to `.env`
 * and a restart of the backend, so a tab open across that restart is already
 * stale in other ways, and re-asking on every navigation would buy nothing.
 *
 * While the answer is on the wire the app renders as `single`, which is why
 * `loading` is exposed separately: a login form must not flash in front of
 * somebody who is not going to need one.
 */
export function AuthProvider({ children }) {
  const [state, setState] = useState({
    mode: SINGLE, bootstrapRequired: false, features: {}, oidc: null, user: null,
    loading: true,
  });

  const loadUser = useCallback(async () => {
    if (!getSessionToken()) return null;
    try {
      const { data } = await getMe();
      return data;
    } catch {
      // An unusable session is the same as none: the login form is next.
      setSessionToken('');
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let mode = SINGLE;
      let bootstrapRequired = false;
      let features = {};
      let oidc = null;
      try {
        const { data } = await getAuthMode();
        mode = data?.mode || SINGLE;
        bootstrapRequired = Boolean(data?.bootstrap_required);
        features = data?.features || {};
        oidc = data?.oidc || null;
      } catch {
        // An unreachable or older backend has no identity layer to render.
      }
      const user = mode === MULTI ? await loadUser() : null;
      if (!cancelled) {
        setState({ mode, bootstrapRequired, features, oidc, user, loading: false });
      }
    })();
    return () => { cancelled = true; };
  }, [loadUser]);

  const login = useCallback(async (username, password) => {
    const { data } = await authLogin({ username, password });
    setSessionToken(data.token);
    writeSessionMeta({ kind: data.kind || 'password', expires_at: data.expires_at || '' });
    setState((prev) => ({ ...prev, user: data.user, bootstrapRequired: false }));
    return data.user;
  }, []);

  const bootstrap = useCallback(async (username, password, displayName = '') => {
    const { data } = await authBootstrap({
      username, password, display_name: displayName,
    });
    setSessionToken(data.token);
    setState((prev) => ({ ...prev, user: data.user, bootstrapRequired: false }));
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authLogout();
    } catch {
      // The session is being dropped either way: a failed logout call means
      // the server already forgot it, or cannot be reached to be told.
    }
    setSessionToken('');
    writeSessionMeta(null);
    setState((prev) => ({ ...prev, user: null }));
  }, []);

  // A session opened elsewhere (the OIDC callback page stores the token it
  // was handed, then calls this) becomes the viewer without a reload.
  const adoptSession = useCallback(async (token) => {
    setSessionToken(token);
    const user = await loadUser();
    setState((prev) => ({ ...prev, user, bootstrapRequired: false }));
    return user;
  }, [loadUser]);

  // Silent renewal of a single sign-on session: a little before it ends, send
  // the browser through the provider again. The provider's own session makes
  // that a pair of redirects with no form, and the callback page brings the
  // browser back to where it was. Checked once when a user is known and then
  // every few minutes; password sessions are long and never renewed here.
  const oidcOn = Boolean(state.oidc);
  const signedIn = Boolean(state.user);
  useEffect(() => {
    if (state.mode !== MULTI || !oidcOn || !signedIn) return undefined;
    const check = () => {
      if (!oidcRenewalDue(readSessionMeta())) return;
      const { pathname, search } = window.location;
      if (pathname.startsWith('/login')) return;
      window.location.assign(oidcStartUrl(`${pathname}${search || ''}`));
    };
    check();
    const timer = window.setInterval(check, RENEW_CHECK_MS);
    return () => window.clearInterval(timer);
  }, [state.mode, oidcOn, signedIn]);

  const value = useMemo(() => ({
    ...DEFAULT_AUTH, ...state, login, bootstrap, logout, adoptSession,
  }), [state, login, bootstrap, logout, adoptSession]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export default AuthProvider;
