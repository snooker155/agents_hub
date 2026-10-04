import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider, translate } from '../../../i18n';

// "Isolated" next to the workspace name (common/isolation.py): shown only
// once the GET resolves and says the workspace is isolated. Each case uses
// its own workspace name so the shared hook cache never leaks between tests.

const ok = (data) => Promise.resolve({ data });
const getWorkspaceIsolation = vi.fn();

vi.mock('../../../api/isolation', () => ({
  getWorkspaceIsolation: (...a) => getWorkspaceIsolation(...a),
  updateWorkspaceIsolation: vi.fn(),
}));

import IsolationBadge from '../IsolationBadge';

const t = (key) => translate('en', key);
const wrap = (node) => render(<I18nProvider>{node}</I18nProvider>);

// vite.config.js sets `test.restoreMocks: true`, so every mock is restored
// to a clean slate before each test here; no local beforeEach needed (and,
// oddly, pairing one with `mockRejectedValue` below made the rejection
// surface as unhandled instead of being caught where it is awaited).
describe('IsolationBadge', () => {
  it('shows the badge once the workspace is confirmed isolated', async () => {
    getWorkspaceIsolation.mockReturnValue(ok({ workspace: 'ws-badge-on', isolated: true }));
    wrap(<IsolationBadge workspace="ws-badge-on" />);
    expect(await screen.findByTestId('isolation-badge')).toHaveTextContent(t('isolation.badge'));
  });

  it('renders nothing for a workspace that is not isolated', async () => {
    getWorkspaceIsolation.mockReturnValue(ok({ workspace: 'ws-badge-off', isolated: false }));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-off" />);
    await waitFor(() => expect(getWorkspaceIsolation).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing until the request resolves, then shows the badge', async () => {
    let resolveGet;
    getWorkspaceIsolation.mockReturnValue(new Promise((resolve) => { resolveGet = resolve; }));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-pending" />);
    expect(container).toBeEmptyDOMElement();
    resolveGet({ data: { workspace: 'ws-badge-pending', isolated: true } });
    expect(await screen.findByTestId('isolation-badge')).toBeInTheDocument();
  });

  it('renders nothing when the request fails', async () => {
    getWorkspaceIsolation.mockRejectedValue(new Error('network down'));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-fail" />);
    await waitFor(() => expect(getWorkspaceIsolation).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing without a workspace', () => {
    const { container } = wrap(<IsolationBadge workspace="" />);
    expect(getWorkspaceIsolation).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
  });
});
