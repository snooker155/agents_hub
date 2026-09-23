import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Audit page is read-only over whatever GET /api/audit hands back: who
// may read which rows is a backend decision (routes/audit.py). What matters
// here is that the page renders the rows and their fields, offers the
// filters the backend accepts, and points the export buttons at
// auditExportUrl with the current filters.

const ok = (data) => Promise.resolve({ data });

const getAuditLog = vi.fn();
const getAuditActions = vi.fn(() => ok(['auth.login', 'run.launch']));
const getWorkspaces = vi.fn(() => ok([{ name: 'default' }, { name: 'acme' }]));
const auditExportUrl = vi.fn((params, format) => `/api/audit/export?format=${format}&stub=1`);

vi.mock('../../api', () => ({
  getAuditLog: (...a) => getAuditLog(...a),
  getAuditActions: (...a) => getAuditActions(...a),
  getWorkspaces: (...a) => getWorkspaces(...a),
  auditExportUrl: (...a) => auditExportUrl(...a),
}));

import Audit from '../Audit';

const ROWS = [
  {
    id: 5, at: '2026-09-23T10:00:00+00:00', actor_id: 'u1', actor_kind: 'user',
    actor_name: 'alice', action: 'auth.login', object_type: 'session', object_id: 's1',
    workspace: 'acme', ip: '10.0.0.1', method: null, path: null, result: 'ok',
    details: { kind: 'password' },
  },
  {
    id: 4, at: '2026-09-23T09:00:00+00:00', actor_id: 'svc', actor_kind: 'service',
    actor_name: null, action: 'run.launch', object_type: 'run', object_id: 'r1',
    workspace: 'default', ip: null, method: null, path: null, result: 'ok',
    details: { agent_id: 'swe_agent' },
  },
];

const show = () => render(<I18nProvider><Audit /></I18nProvider>);

beforeEach(() => {
  localStorage.clear();
  getAuditLog.mockReset();
  getAuditActions.mockClear();
  getWorkspaces.mockClear();
  auditExportUrl.mockClear();
  getAuditLog.mockImplementation(() => ok({ items: ROWS, total: 2, limit: 50, offset: 0 }));
});

describe('Audit', () => {
  it('lists rows with their actor, action, workspace and result', async () => {
    show();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    expect(screen.getByText('run.launch', { selector: 'td' })).toBeInTheDocument();
    expect(screen.getAllByText('acme').length).toBeGreaterThan(0);
  });

  it('loads the action list and workspace list for the filter dropdowns', async () => {
    show();
    await waitFor(() => expect(getAuditActions).toHaveBeenCalled());
    expect(getWorkspaces).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText('auth.login', { selector: 'option' })).toBeInTheDocument());
  });

  it('shows an empty state when there are no rows', async () => {
    getAuditLog.mockImplementation(() => ok({ items: [], total: 0, limit: 50, offset: 0 }));
    show();
    await waitFor(() => expect(screen.getByText(/no audit rows match/i)).toBeInTheDocument());
  });

  it('toggles a row open to show its details JSON', async () => {
    show();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    const toggles = screen.getAllByText(/details/i).filter((el) => el.closest('button'));
    fireEvent.click(toggles[0]);
    await waitFor(() => expect(screen.getByText(/"kind": "password"/)).toBeInTheDocument());
  });

  it('points the export buttons at auditExportUrl with csv and jsonl', async () => {
    show();
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    const csvLink = screen.getByText(/export csv/i).closest('a');
    const jsonlLink = screen.getByText(/export jsonl/i).closest('a');
    expect(csvLink).toHaveAttribute('href', expect.stringContaining('format=csv'));
    expect(jsonlLink).toHaveAttribute('href', expect.stringContaining('format=jsonl'));
  });

  it('re-queries from the first page when a filter changes', async () => {
    show();
    await waitFor(() => expect(getAuditLog).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText(/user id or name/i), { target: { value: 'alice' } });
    await waitFor(() => expect(getAuditLog).toHaveBeenCalledTimes(2));
    const lastCall = getAuditLog.mock.calls.at(-1)[0];
    expect(lastCall.actor).toBe('alice');
    expect(lastCall.offset).toBe(0);
  });
});
