import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The web requests page loads the log in batches (GET /api/web-logs with a
// growing limit from the top, routes/weblogs.py) like the other long lists:
// the first batch, then "load more" asks for the next one, and a sort change
// goes to the server rather than reordering the rows on screen.

const ok = (data) => Promise.resolve({ data });
const row = (i) => ({ id: `w${i}`, kind: 'fetch', url: `https://ok.test/${i}`, status: 'ok', ts: '2026-10-11T10:00:00Z' });

const getWebLogs = vi.fn();
vi.mock('../../api', () => ({
  getWebLogs: (...a) => getWebLogs(...a),
  getWebLogStats: () => ok({ enabled: true, total: 45, by_kind: {}, by_status: {} }),
  getWebLogEntry: vi.fn(),
  clearWebLogs: vi.fn(),
}));

import WebLogs from '../WebLogs';

const show = () => render(<I18nProvider><WebLogs /></I18nProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  getWebLogs.mockImplementation(({ limit }) => ok({
    items: Array.from({ length: Math.min(limit, 45) }, (_, i) => row(i)),
    total: 45,
  }));
});

describe('WebLogs paging', () => {
  it('loads the first batch and then the next one', async () => {
    show();
    await waitFor(() => expect(screen.getByText('https://ok.test/19')).toBeInTheDocument());
    expect(getWebLogs.mock.calls[0][0]).toMatchObject({ limit: 20, offset: 0, sort: 'created', order: 'desc' });
    expect(screen.queryByText('https://ok.test/20')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText(/load 20 more/i));
    await waitFor(() => expect(screen.getByText('https://ok.test/39')).toBeInTheDocument());
    expect(getWebLogs).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 40, offset: 0 }));
    expect(screen.getByText(/load 5 more/i)).toBeInTheDocument();
  });

  it('asks the server for the chosen sort', async () => {
    show();
    await waitFor(() => expect(getWebLogs).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'severity' } });
    await waitFor(() => expect(getWebLogs).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: 'severity', limit: 20 })));
  });
});
