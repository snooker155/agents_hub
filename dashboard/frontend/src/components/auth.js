/**
 * The identity posture this hub runs in, read once at app start from
 * `GET /api/auth/mode`.
 *
 * Kept apart from `AuthContext.jsx` so that file exports nothing but the
 * provider component and Fast Refresh can hot-swap it (the same split as
 * `features.js` / `FeaturesContext.jsx`).
 *
 * The default is `single`: one operator, no login, nothing identity-related on
 * screen. That is also what a backend too old to answer this route means, and
 * what an unreachable backend means, so a slow answer never flashes a login
 * form at somebody who does not need one.
 */
import { createContext, useContext } from 'react';

export const SINGLE = 'single';
export const TOKEN = 'token';
export const MULTI = 'multi';

export const DEFAULT_AUTH = {
  mode: SINGLE,
  bootstrapRequired: false,
  // `features` and `oidc` come straight from GET /api/auth/mode: what to
  // render (login form, accounts, audit, api keys) and the sign-in button.
  features: {},
  oidc: null,
  user: null,
  loading: true,
  login: async () => {},
  bootstrap: async () => {},
  logout: async () => {},
};

export const AuthContext = createContext(DEFAULT_AUTH);

export const useAuth = () => useContext(AuthContext) || DEFAULT_AUTH;

/** True when named users exist at all. Everything identity-related hangs off it. */
export const isMultiUser = (auth) => auth?.mode === MULTI;

/** True when the app should show a login form instead of the application. */
export const needsLogin = (auth) => (
  auth?.mode === MULTI && !auth?.loading && !auth?.user
);

/** True when this viewer may manage accounts. */
export const isAdmin = (auth) => (
  auth?.mode === MULTI && auth?.user?.role === 'admin'
);
