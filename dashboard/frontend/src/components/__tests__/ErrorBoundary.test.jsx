import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ErrorBoundary from '../ErrorBoundary';
import { I18nProvider } from '../../i18n';

function Boom() {
  throw new Error('the page exploded');
}

const show = (children) => render(
  <MemoryRouter>
    <I18nProvider><ErrorBoundary>{children}</ErrorBoundary></I18nProvider>
  </MemoryRouter>,
);

describe('ErrorBoundary', () => {
  let errorSpy;
  // React logs the caught error itself; the boundary logs it again on purpose.
  // Silenced so a passing test does not print a stack trace.
  beforeEach(() => { errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {}); });
  afterEach(() => errorSpy.mockRestore());

  it('renders its children when nothing throws', () => {
    show(<p>all good</p>);
    expect(screen.getByText('all good')).toBeInTheDocument();
    expect(screen.queryByText('This page stopped working')).not.toBeInTheDocument();
  });

  it('shows the fallback, with the message, when a child throws', () => {
    show(<Boom />);
    expect(screen.getByText('This page stopped working')).toBeInTheDocument();
    expect(screen.getByText('the page exploded')).toBeInTheDocument();
  });

  it('offers a way out: reload, or leave for the dashboard', () => {
    show(<Boom />);
    expect(screen.getByRole('button', { name: /Reload page/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Back to dashboard/ })).toHaveAttribute('href', '/dashboard');
  });

  it('logs the failure so it is not swallowed', () => {
    show(<Boom />);
    expect(errorSpy).toHaveBeenCalledWith(
      'Route render failed:', expect.any(Error), expect.anything(),
    );
  });
});
