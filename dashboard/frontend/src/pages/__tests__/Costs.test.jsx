import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { I18nProvider } from '../../i18n';

// The costs page draws its breakdowns from GET /api/costs and the run list
// from GET /api/costs/runs: the scope picker, the tabs and the filters.

const ok = (data) => Promise.resolve({ data });

const getCosts = vi.fn(() => ok({
  totals: { runs: 0, total_tokens: 0, cached_tokens: 0, cost: 0 },
  by_model: [], by_agent: [], by_workspace: [], by_project: [],
}));
const getCostRuns = vi.fn();
const getAgents = vi.fn(() => ok([{ id: 'a1', name: 'Writer' }, { id: 'a2', name: 'Local' }]));

vi.mock('../../api', () => ({
  getCosts: (...a) => getCosts(...a),
  getCostRuns: (...a) => getCostRuns(...a),
  getAgents: (...a) => getAgents(...a),
}));

let selectedWorkspace = 'default';
vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace }),
}));

import Costs from '../Costs';

const show = (path = '/costs') => render(
  <MemoryRouter initialEntries={[path]}><I18nProvider><Costs /></I18nProvider></MemoryRouter>,
);

const RUNS = {
  total: 3,
  items: [
    { run_id: 'r2', started_at: '2026-10-10T10:00:00Z', agent_id: 'a1', agent_name: 'Writer',
      provider: 'openai', model: 'gpt-x', workspace: 'default', inbound_tokens: 900,
      outbound_tokens: 100, cached_tokens: 0, total_tokens: 1000, cost: 0.004064, priced: true },
    { run_id: 'r1', started_at: '2026-10-09T10:00:00Z', agent_id: 'a2', agent_name: 'Local',
      provider: 'acme', model: 'mystery', workspace: 'default', inbound_tokens: 10,
      outbound_tokens: 10, cached_tokens: 0, total_tokens: 20, cost: 0, priced: false },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  selectedWorkspace = 'default';
  getCosts.mockImplementation(() => ok({
    totals: { runs: 0, total_tokens: 0, cached_tokens: 0, cost: 0 },
    by_model: [], by_agent: [], by_workspace: [], by_project: [],
  }));
  getCostRuns.mockImplementation(() => ok(RUNS));
  getAgents.mockImplementation(() => ok([{ id: 'a1', name: 'Writer' }, { id: 'a2', name: 'Local' }]));
});

// Only the default workspace may look across workspaces; elsewhere the scope
// picker is hidden and costs are always fetched for the current workspace.
describe('Costs scope picker', () => {
  const scopeSelect = () => screen.queryAllByRole('combobox')
    .find((s) => s.innerHTML.includes('value="all"'));

  it('offers all workspaces in the default workspace', async () => {
    show();
    await waitFor(() => expect(getCosts).toHaveBeenCalled());
    expect(scopeSelect()).toBeTruthy();
  });

  it('hides the picker and scopes to the current workspace elsewhere', async () => {
    selectedWorkspace = 'team';
    show();
    await waitFor(() => expect(getCosts).toHaveBeenCalled());
    expect(scopeSelect()).toBeUndefined();
    expect(getCosts.mock.calls[0][0]).toMatchObject({ workspace: 'team' });
  });
});

// The Runs tab lists every counted run with its own cost, loaded in batches
// (GET /api/costs/runs, common/costs_report.py costs_runs).
describe('Costs runs tab', () => {
  it('stays off until the tab is opened', async () => {
    show();
    await waitFor(() => expect(getCosts).toHaveBeenCalled());
    expect(getCostRuns).not.toHaveBeenCalled();
  });

  it('lists runs with their cost, says when a model has no price, and loads more', async () => {
    show('/costs?tab=runs');
    await waitFor(() => expect(screen.getByText('$0.0041')).toBeInTheDocument());
    expect(getCostRuns.mock.calls[0][0]).toMatchObject({ limit: 20, offset: 0, sort: 'started', order: 'desc' });
    expect(screen.getByText('no price')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Load 1 more'));
    await waitFor(() => expect(getCostRuns).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 40 })));
  });

  it('opens from the tab bar', async () => {
    show();
    fireEvent.click(screen.getAllByRole('button', { name: /runs/i })[0]);
    await waitFor(() => expect(getCostRuns).toHaveBeenCalled());
  });

  it('filters by agent and status in its own filter bar, and clears them', async () => {
    show('/costs?tab=runs');
    await waitFor(() => expect(getCostRuns).toHaveBeenCalled());
    const select = (text) => screen.getAllByRole('combobox').find((s) => s.innerHTML.includes(text));
    fireEvent.change(select('All agents'), { target: { value: 'a2' } });
    await waitFor(() => expect(getCostRuns).toHaveBeenLastCalledWith(expect.objectContaining({ agent_id: 'a2' })));
    fireEvent.change(select('All statuses'), { target: { value: 'failed' } });
    await waitFor(() => expect(getCostRuns).toHaveBeenLastCalledWith(
      expect.objectContaining({ agent_id: 'a2', status: 'failed' })));
    fireEvent.click(screen.getByText('Clear'));
    await waitFor(() => expect(getCostRuns).toHaveBeenLastCalledWith(
      expect.objectContaining({ agent_id: undefined, status: undefined })));
  });

  it('moves the scope picker from the header into the filter bar', async () => {
    show('/costs?tab=runs');
    await waitFor(() => expect(getCostRuns).toHaveBeenCalled());
    const scopeSelects = screen.getAllByRole('combobox').filter((s) => s.innerHTML.includes('value="all"'));
    expect(scopeSelects).toHaveLength(1);
  });
});
