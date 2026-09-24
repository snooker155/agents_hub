import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Browser page: a sentence and a docs link when the hub has no browser
// service, otherwise the workspace's sessions (an agent's and a person's,
// told apart by a badge) and the open one with its toolbar.

const ok = (data) => Promise.resolve({ data });

const getBrowserStatus = vi.fn();
const listBrowserSessions = vi.fn();
const createBrowserSession = vi.fn();
const getBrowserFrame = vi.fn(() => ok({
  url: 'https://example.com/', title: 'Example', width: 1280, height: 800, image: 'data:image/jpeg;base64,AAAA',
}));

vi.mock('../../api/browser', () => ({
  getBrowserStatus: (...a) => getBrowserStatus(...a),
  listBrowserSessions: (...a) => listBrowserSessions(...a),
  createBrowserSession: (...a) => createBrowserSession(...a),
  closeBrowserSession: () => ok({ ok: true }),
  handoffBrowserSession: () => ok({ task_id: 't1', run_id: 'r1' }),
  getBrowserFrame: (...a) => getBrowserFrame(...a),
  sendBrowserInput: () => ok({}),
}));

vi.mock('../../api', () => ({
  getWorkspaces: () => ok([{ name: 'default' }, { name: 'shop' }]),
  getAgents: () => ok([{ id: 'shopper', name: 'Shopper' }]),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'shop' }),
}));

import Browser from '../Browser';

const SESSIONS = [
  { session_id: 'A1aaaaaaaa', run_id: 'run-12345678', workspace: 'shop', owner: 'agent', label: 'shopper',
    url: 'https://example.com/cart', title: 'Cart' },
  { session_id: 'U1uuuuuuuu', run_id: '', workspace: 'shop', owner: 'user', label: 'ann',
    url: 'https://example.com/', title: 'Example' },
];

const show = () => render(
  <MemoryRouter>
    <I18nProvider>
      <Browser />
    </I18nProvider>
  </MemoryRouter>,
);

describe('Browser page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listBrowserSessions.mockImplementation(() => ok({ sessions: SESSIONS }));
  });

  it('explains when the browser service is not configured', async () => {
    getBrowserStatus.mockImplementation(() => ok({ configured: false, url: null }));
    show();
    expect(await screen.findByText(/The browser service is not configured/)).toBeTruthy();
    expect(screen.getByText('How to set it up').closest('a').getAttribute('href')).toBe('/docs/browser');
    expect(listBrowserSessions).not.toHaveBeenCalled();
  });

  it('lists the workspace sessions with owner badges and opens one', async () => {
    getBrowserStatus.mockImplementation(() => ok({ configured: true, url: 'http://browser:3000' }));
    show();
    expect(await screen.findByText('Cart')).toBeTruthy();
    expect(listBrowserSessions).toHaveBeenCalledWith('shop');
    expect(screen.getByText('agent')).toBeTruthy();
    expect(screen.getByText('person')).toBeTruthy();

    fireEvent.click(screen.getByText('Cart'));
    await waitFor(() => expect(getBrowserFrame).toHaveBeenCalledWith('A1aaaaaaaa'));
    // An agent's session is watched until the person takes control.
    expect(screen.getByText('Take control')).toBeTruthy();
    expect(screen.getByText('Hand to agent')).toBeTruthy();
  });

  it('creates a session from the address bar', async () => {
    getBrowserStatus.mockImplementation(() => ok({ configured: true }));
    listBrowserSessions.mockImplementation(() => ok({ sessions: [] }));
    createBrowserSession.mockImplementation(() => ok({ session_id: 'N1', url: 'https://example.org/', title: '' }));
    show();
    const address = await screen.findByLabelText('Address');
    fireEvent.change(address, { target: { value: 'example.org' } });
    fireEvent.submit(address.closest('form'));
    await waitFor(() => expect(createBrowserSession).toHaveBeenCalledWith('shop', 'https://example.org'));
  });
});
