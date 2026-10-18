/**
 * The identity posture: one operator, always. There is no login, no accounts
 * and nothing identity-related on screen. The helpers keep the names the
 * pages already read; with one operator they say "single, no named users".
 */
import { createContext, useContext } from 'react';

export const SINGLE = 'single';

export const DEFAULT_AUTH = {
  mode: SINGLE,
  features: {},
  user: null,
  loading: false,
};

export const AuthContext = createContext(DEFAULT_AUTH);

export const useAuth = () => useContext(AuthContext) || DEFAULT_AUTH;

/** Whether named users exist: never. */
export const isMultiUser = () => false;

/** Whether this viewer manages accounts: there are none to manage. */
export const isAdmin = () => false;
