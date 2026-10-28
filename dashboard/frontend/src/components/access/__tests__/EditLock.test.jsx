import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { EditLock, AccessNotice } from '../EditLock';
import { canAdminister } from '../../auth';

describe('EditLock', () => {
  it('disables the controls inside when locked', () => {
    render(<EditLock locked><button type="button">Save</button></EditLock>);
    expect(screen.getByText('Save').closest('fieldset').disabled).toBe(true);
  });

  it('renders the children untouched when not locked', () => {
    render(<EditLock locked={false}><button type="button">Save</button></EditLock>);
    expect(screen.getByText('Save').closest('fieldset')).toBeNull();
  });

  it('shows the notice text', () => {
    render(<AccessNotice>Only an administrator</AccessNotice>);
    expect(screen.getByTestId('access-notice').textContent).toContain('Only an administrator');
  });
});

describe('canAdminister', () => {
  it('is true outside multi mode and for admins, false for members', () => {
    expect(canAdminister({ mode: 'single' })).toBe(true);
    expect(canAdminister({ mode: 'token' })).toBe(true);
    expect(canAdminister({ mode: 'multi', user: { role: 'admin' } })).toBe(true);
    expect(canAdminister({ mode: 'multi', user: { role: 'member' } })).toBe(false);
  });
});
