import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { I18nProvider } from '../../i18n';

// The costs page draws the existing breakdowns from GET /api/costs and the
// new report section from GET /api/accounting/report (routes/accounting.py,
// docs/costs.md "Report"). What matters here: the report renders the rows
// and totals the backend returns, a group-by change re-fetches with the new
// parameter, and "Export CSV" downloads through the authenticated client
// rather than a bare link.

const ok = (data) => Promise.resolve({ data });

const getCosts = vi.fn(() => ok({
  totals: { runs: 0, total_tokens: 0, cached_tokens: 0, cost: 0 },
  by_model: [], by_agent: [], by_workspace: [], by_project: [],
}));
const getBudget = vi.fn(() => ok(null));
const setBudget = vi.fn();
const getCostRuns = vi.fn();
const getAgents = vi.fn(() => ok([{ id: 'a1', name: 'Writer' }, { id: 'a2', name: 'Local' }]));

vi.mock('../../api', () => ({
  getCosts: (...a) => getCosts(...a),
  getBudget: (...a) => getBudget(...a),
  setBudget: (...a) => setBudget(...a),
  getCostRuns: (...a) => getCostRuns(...a),
  getAgents: (...a) => getAgents(...a),
}));

const getAccountingReport = vi.fn();
const getAccountingReportCsv = vi.fn(() => ok(new Blob(['key,label\n'], { type: 'text/csv' })));
vi.mock('../../api/accounting', () => ({
  getAccountingReport: (...a) => getAccountingReport(...a),
  getAccountingReportCsv: (...a) => getAccountingReportCsv(...a),
}));

const saveBlobAs = vi.fn();
vi.mock('../../api/files', () => ({
  saveBlobAs: (...a) => saveBlobAs(...a),
}));

let selectedWorkspace = 'default';
vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace }),
}));

import Costs from '../Costs';

const REPORT = {
  group_by: 'user',
  rows: [
    { key: 'u1', label: 'Alice', runs: 3, calls: 1, inbound_tokens: 1000, cached_tokens: 0,
     outbound_tokens: 200, total_tokens: 1200, cost: 4.5 },
    { key: '(unknown)', label: '(unknown)', runs: 1, calls: 0, inbound_tokens: 100,
     cached_tokens: 0, outbound_tokens: 0, total_tokens: 100, cost: 0.1 },
  ],
  totals: { runs: 4, calls: 1, inbound_tokens: 1100, cached_tokens: 0, outbound_tokens: 200,
    total_tokens: 1300, cost: 4.6 },
};

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
  getBudget.mockImplementation(() => ok(null));
  getCostRuns.mockImplementation(() => ok(RUNS));
  getAgents.mockImplementation(() => ok([{ id: 'a1', name: 'Writer' }, { id: 'a2', name: 'Local' }]));
  getAccountingReport.mockImplementation(() => ok(REPORT));
  getAccountingReportCsv.mockImplementation(() => ok(new Blob(['key,label\n'], { type: 'text/csv' })));
});

describe('Costs report section', () => {
  it('loads the report grouped by user and shows its rows and totals', async () => {
    show();
    await waitFor(() => expect(getAccountingReport).toHaveBeenCalled());
    expect(getAccountingReport.mock.calls[0][0]).toMatchObject({ group_by: 'user' });
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());
    expect(screen.getByText('(unknown)')).toBeInTheDocument();
    expect(screen.getByText('$4.60')).toBeInTheDocument(); // the report's own total row
  });

  it('re-fetches with the chosen group-by', async () => {
    show();
    await waitFor(() => expect(getAccountingReport).toHaveBeenCalled());
    const selects = screen.getAllByRole('combobox');
    // The report's own group-by select is the one offering "By API key" etc.
    const groupBySelect = selects.find((s) => s.innerHTML.includes('By API key'));
    expect(groupBySelect).toBeTruthy();
    fireEvent.change(groupBySelect, { target: { value: 'project' } });
    await waitFor(() => expect(getAccountingReport).toHaveBeenLastCalledWith(
      expect.objectContaining({ group_by: 'project' })));
  });

  it('exports the report as CSV through the authenticated client', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());
    fireEvent.click(screen.getByText(/export csv/i));
    await waitFor(() => expect(getAccountingReportCsv).toHaveBeenCalled());
    await waitFor(() => expect(saveBlobAs).toHaveBeenCalled());
    expect(saveBlobAs.mock.calls[0][1]).toMatch(/accounting-report-user\.csv/);
  });
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
