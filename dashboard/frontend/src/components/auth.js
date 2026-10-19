/**
 * The identity posture: one operator, always. There is no login, no accounts
 * and nothing identity-related on screen.
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
