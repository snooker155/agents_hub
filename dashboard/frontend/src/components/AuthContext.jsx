import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  authBootstrap, authLogin, authLogout, getAuthMode, getMe, setSessionToken, getSessionToken,
} from '../api';
import { AuthContext, DEFAULT_AUTH, MULTI, SINGLE } from './auth';

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
    mode: SINGLE, bootstrapRequired: false, user: null, loading: true,
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
      try {
        const { data } = await getAuthMode();
        mode = data?.mode || SINGLE;
        bootstrapRequired = Boolean(data?.bootstrap_required);
      } catch {
        // An unreachable or older backend has no identity layer to render.
      }
      const user = mode === MULTI ? await loadUser() : null;
      if (!cancelled) setState({ mode, bootstrapRequired, user, loading: false });
    })();
    return () => { cancelled = true; };
  }, [loadUser]);

  const login = useCallback(async (username, password) => {
    const { data } = await authLogin({ username, password });
    setSessionToken(data.token);
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
    setState((prev) => ({ ...prev, user: null }));
  }, []);

  const value = useMemo(() => ({
    ...DEFAULT_AUTH, ...state, login, bootstrap, logout,
  }), [state, login, bootstrap, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export default AuthProvider;
