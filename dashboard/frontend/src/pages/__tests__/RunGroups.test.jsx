import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The run groups page is one view over four different stores (flow, loop,
// team and task-container runs). What matters here is that the list renders
// what the backend actually returns (kind, status, cost, children), that a
// group only offers Stop while it is active, and that a group's children link
// to the existing run detail page rather than to nothing.

const ok = (data) => Promise.resolve({ data });

const listRunGroups = vi.fn();
const stopRunGroup = vi.fn(() => ok({ kind: 'flow', id: 'f1', stopped: true }));

vi.mock('../../api', () => ({
  listRunGroups: (...a) => listRunGroups(...a),
  stopRunGroup: (...a) => stopRunGroup(...a),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default', liveUpdates: false }),
}));

vi.mock('../../components/stream', () => ({
  useLiveRefetch: () => {},
}));

import RunGroups from '../RunGroups';

const GROUPS = [
  {
    kind: 'flow', id: 'f1', status: 'running', started_at: '2026-01-05T10:00:00Z',
    finished_at: null, total_cost: 0.1234, error: null, children: ['run-1', 'run-2'],
    workspace: 'acme', title: 'Nightly build', parent_id: 'flow-def', active: true,
  },
  {
    kind: 'team', id: 't1', status: 'done', started_at: '2026-01-04T09:00:00Z',
    finished_at: '2026-01-04T09:30:00Z', total_cost: 0.5, error: null, children: ['run-3'],
    workspace: 'acme', title: 'Roster review', parent_id: 'team-def', active: false,
  },
];

const show = () => render(
  <I18nProvider><MemoryRouter><RunGroups /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  localStorage.clear();
  listRunGroups.mockClear();
  stopRunGroup.mockClear();
  listRunGroups.mockImplementation(() => ok({ groups: GROUPS, kinds: ['flow', 'loop', 'team', 'container'] }));
});

describe('RunGroups', () => {
  it('lists groups with their kind, status and cost', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Nightly build')).toBeInTheDocument());
    expect(screen.getByText('Roster review')).toBeInTheDocument();
    expect(screen.getByText('$0.1234')).toBeInTheDocument();
    expect(screen.getByText('$0.5000')).toBeInTheDocument();
  });

  it('offers Stop only for an active group', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Nightly build')).toBeInTheDocument());
    const stopButtons = screen.getAllByText(/stop/i).filter((el) => el.closest('button'));
    expect(stopButtons.length).toBe(1);
  });

  it('confirms before stopping and calls the stop endpoint', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await waitFor(() => expect(screen.getByText('Nightly build')).toBeInTheDocument());
    fireEvent.click(screen.getByText(/^stop$/i));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(stopRunGroup).toHaveBeenCalledWith('flow', 'f1'));
    confirmSpy.mockRestore();
  });

  it('expands a row to show its child runs, linked to the run detail page', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Nightly build')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Nightly build'));
    await waitFor(() => expect(screen.getByText('run-1')).toBeInTheDocument());
    expect(screen.getByText('run-1').closest('a')).toHaveAttribute('href', '/messages/run-1');
  });

  it('shows the empty state when nothing is running', async () => {
    listRunGroups.mockImplementation(() => ok({ groups: [], kinds: ['flow', 'loop', 'team', 'container'] }));
    show();
    await waitFor(() => expect(screen.getByText(/no run groups found/i)).toBeInTheDocument());
  });
});
