import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// A run that used the browser tools gets a Browser panel under its tools, but
// only once the hub says the run has a session; a run without one shows
// nothing extra, and a run that never touched the browser is not asked about.

const ok = (data) => Promise.resolve({ data });
const notFound = () => Promise.reject({ response: { status: 404, data: { detail: 'none' } } });

const getRunBrowserSession = vi.fn();
const getBrowserFrame = vi.fn(() => ok({
  url: 'https://example.com/cart', title: 'Cart', width: 1280, height: 800, image: 'data:image/jpeg;base64,AAAA',
}));
const sendBrowserInput = vi.fn(() => ok({}));

vi.mock('../../api/browser', () => ({
  getRunBrowserSession: (...a) => getRunBrowserSession(...a),
  getBrowserFrame: (...a) => getBrowserFrame(...a),
  sendBrowserInput: (...a) => sendBrowserInput(...a),
}));

// The SSE channel needs the stream provider; the seed is enough here.
vi.mock('../stream', () => ({ useChannel: () => {} }));

import LiveRunStream from '../LiveRunStream';

const seed = (tools, status = 'running') => ({
  run_id: 'run-1', agent_id: 'shopper', text: '', thinking: [], status,
  tools: tools.map((tool, i) => ({ step: i, tool, input: '', output: 'ok' })),
});

const show = (s) => render(
  <I18nProvider>
    <LiveRunStream sessionId="sess-1" runId="run-1" seed={s} />
  </I18nProvider>,
);

describe('LiveRunStream browser panel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom has no layout; the panel follows the tail of a running run.
    Element.prototype.scrollIntoView = () => {};
  });

  it('shows the Browser panel when the run has a session', async () => {
    getRunBrowserSession.mockImplementation(() => ok({
      session_id: 'B1', run_id: 'run-1', owner: 'agent', url: 'https://example.com/cart', title: 'Cart',
    }));
    show(seed(['browser_open', 'browser_read']));
    expect(await screen.findByTestId('run-browser-panel')).toBeTruthy();
    expect(getRunBrowserSession).toHaveBeenCalledWith('run-1');
    await waitFor(() => expect(getBrowserFrame).toHaveBeenCalledWith('B1'));
    expect(await screen.findByAltText('Cart')).toBeTruthy();
    expect(screen.getByText('Take control')).toBeTruthy();
  });

  it('shows nothing extra when the lookup 404s', async () => {
    getRunBrowserSession.mockImplementation(notFound);
    show(seed(['browser_open']));
    await waitFor(() => expect(getRunBrowserSession).toHaveBeenCalled());
    expect(screen.queryByTestId('run-browser-panel')).toBeNull();
  });

  it('does not ask for a run that used no browser tool', async () => {
    show(seed(['fetch_url']));
    expect(await screen.findByText('fetch_url')).toBeTruthy();
    expect(getRunBrowserSession).not.toHaveBeenCalled();
  });
});
