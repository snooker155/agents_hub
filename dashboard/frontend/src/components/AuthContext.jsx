import React from 'react';
import { AuthContext, DEFAULT_AUTH } from './auth';

/**
 * Provides the one-operator identity to the app. Nothing is fetched: there is
 * no login and no user to resolve.
 */
export function AuthProvider({ children }) {
  return <AuthContext.Provider value={DEFAULT_AUTH}>{children}</AuthContext.Provider>;
}
