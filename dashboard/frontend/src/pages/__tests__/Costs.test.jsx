import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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

vi.mock('../../api', () => ({
  getCosts: (...a) => getCosts(...a),
  getBudget: (...a) => getBudget(...a),
  setBudget: (...a) => setBudget(...a),
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

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default' }),
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

const show = () => render(<I18nProvider><Costs /></I18nProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  getCosts.mockImplementation(() => ok({
    totals: { runs: 0, total_tokens: 0, cached_tokens: 0, cost: 0 },
    by_model: [], by_agent: [], by_workspace: [], by_project: [],
  }));
  getBudget.mockImplementation(() => ok(null));
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
