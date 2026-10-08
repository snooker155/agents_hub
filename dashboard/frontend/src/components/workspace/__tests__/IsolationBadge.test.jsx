
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider, translate } from '../../../i18n';

// "Isolated" next to the workspace name: shown only once the workspace
// summary (api/workspaceSummary.js) resolves and says the workspace is
// isolated. Each case uses its own workspace name so the shared summary cache
// never leaks between tests.

const ok = (data) => Promise.resolve({ data });
const apiGet = vi.fn();

vi.mock('../../../api', () => ({
  default: { get: (...a) => apiGet(...a) },
}));

import IsolationBadge from '../IsolationBadge';
import { patchWorkspaceSummary } from '../../../api/workspaceSummary';

const t = (key) => translate('en', key);
const wrap = (node) => render(<I18nProvider>{node}</I18nProvider>);

// vite.config.js sets `test.restoreMocks: true`, so every mock is restored
// to a clean slate before each test here; no local beforeEach needed.
describe('IsolationBadge', () => {
  it('shows the badge once the workspace is confirmed isolated', async () => {
    apiGet.mockReturnValue(ok({ name: 'ws-badge-on', isolated: true }));
    wrap(<IsolationBadge workspace="ws-badge-on" />);
    expect(await screen.findByTestId('isolation-badge')).toHaveTextContent(t('isolation.badge'));
    expect(apiGet).toHaveBeenCalledWith('/workspaces/ws-badge-on/summary');
  });

  it('renders nothing for a workspace that is not isolated', async () => {
    apiGet.mockReturnValue(ok({ name: 'ws-badge-off', isolated: false }));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-off" />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing until the request resolves, then shows the badge', async () => {
    let resolveGet;
    apiGet.mockReturnValue(new Promise((resolve) => { resolveGet = resolve; }));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-pending" />);
    expect(container).toBeEmptyDOMElement();
    resolveGet({ data: { name: 'ws-badge-pending', isolated: true } });
    expect(await screen.findByTestId('isolation-badge')).toBeInTheDocument();
  });

  it('renders nothing when the request fails', async () => {
    apiGet.mockRejectedValue(new Error('network down'));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-fail" />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing without a workspace', () => {
    const { container } = wrap(<IsolationBadge workspace="" />);
    expect(apiGet).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
  });

  it('two badges mounted together share one request', async () => {
    apiGet.mockReturnValue(ok({ name: 'ws-badge-shared', isolated: true }));
    wrap(<><IsolationBadge workspace="ws-badge-shared" /><IsolationBadge workspace="ws-badge-shared" /></>);
    expect(await screen.findAllByTestId('isolation-badge')).toHaveLength(2);
    expect(apiGet).toHaveBeenCalledTimes(1);
  });

  it('follows a patch after the switch is saved, without asking again', async () => {
    apiGet.mockReturnValue(ok({ name: 'ws-badge-patch', isolated: false }));
    const { container } = wrap(<IsolationBadge workspace="ws-badge-patch" />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    patchWorkspaceSummary('ws-badge-patch', { isolated: true });
    expect(await screen.findByTestId('isolation-badge')).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledTimes(1);
  });
});
