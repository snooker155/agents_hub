import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AuthProvider } from '../AuthContext';
import { isAdmin, isMultiUser, useAuth } from '../auth';

// The identity is fixed: one operator, nobody signed in, no accounts to manage.
function Probe() {
  const auth = useAuth();
  return (
    <>
      <span data-testid="mode">{auth.mode}</span>
      <span data-testid="admin">{String(isAdmin(auth))}</span>
      <span data-testid="multi">{String(isMultiUser(auth))}</span>
      <span data-testid="user">{auth.user?.username || 'none'}</span>
    </>
  );
}

describe('AuthProvider', () => {
  it('is always the single operator', () => {
    render(<AuthProvider><Probe /></AuthProvider>);
    expect(screen.getByTestId('mode').textContent).toBe('single');
    expect(screen.getByTestId('admin').textContent).toBe('false');
    expect(screen.getByTestId('multi').textContent).toBe('false');
    expect(screen.getByTestId('user').textContent).toBe('none');
  });
});
