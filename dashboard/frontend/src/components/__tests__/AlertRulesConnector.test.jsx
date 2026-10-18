import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  listNotifyRules: vi.fn(),
  createNotifyRule: vi.fn(),
  updateNotifyRule: vi.fn(),
  deleteNotifyRule: vi.fn(),
}));
vi.mock('../../api', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'w1' }) }));
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import AlertRulesConnector from '../connectors/AlertRulesConnector';

describe('AlertRulesConnector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listNotifyRules.mockResolvedValue({ data: { rules: [
      { id: 'r2', kind: 'spend_run_over', threshold_usd: 1.5, channels: ['dashboard'], enabled: true },
    ] } });
    api.createNotifyRule.mockResolvedValue({ data: { rule: { id: 'r3', kind: 'spend_daily_over' } } });
  });

  it('shows a spend rule by its dollar threshold', async () => {
    render(<AlertRulesConnector />);
    expect(await screen.findByText('$1.50')).toBeInTheDocument();
  });

  it('creates a spend rule for the inbox only', async () => {
    render(<AlertRulesConnector />);
    await screen.findByText('$1.50');
    const kindSelect = screen.getAllByRole('combobox').find((el) =>
      [...el.options].some((o) => o.value === 'spend_daily_over'));
    fireEvent.change(kindSelect, { target: { value: 'spend_daily_over' } });
    fireEvent.click(screen.getByRole('button', { name: /connectors.alerts.addRule/ }));
    await waitFor(() => expect(api.createNotifyRule).toHaveBeenCalled());
    const [payload, workspace] = api.createNotifyRule.mock.calls[0];
    expect(workspace).toBe('w1');
    expect(payload).toMatchObject({ kind: 'spend_daily_over', threshold_usd: 1, channels: ['dashboard'] });
  });

  it('shows a run_failed rule with no dollar threshold, on the table and in the form', async () => {
    api.listNotifyRules.mockResolvedValue({ data: { rules: [
      { id: 'r4', kind: 'run_failed', channels: ['dashboard'], enabled: true, threshold_usd: 0, state: null },
    ] } });
    api.createNotifyRule.mockResolvedValue({ data: { rule: { id: 'r5', kind: 'run_failed' } } });
    render(<AlertRulesConnector />);
    await screen.findAllByText('connectors.alerts.ruleKinds.run_failed');
    // The existing row reads "—", not a dollar amount. The same text also
    // appears as an <option> in the kind picker below, so the match with a
    // <tr> ancestor is the table row.
    const row = screen.getAllByText('connectors.alerts.ruleKinds.run_failed')
      .map((el) => el.closest('tr')).find(Boolean);
    expect(row).toHaveTextContent('—');

    fireEvent.click(screen.getByRole('button', { name: /connectors.alerts.addRule/ }));
    await waitFor(() => expect(api.createNotifyRule).toHaveBeenCalled());
    const [payload] = api.createNotifyRule.mock.calls[0];
    expect(payload).toMatchObject({ kind: 'run_failed', threshold_usd: 0 });
  });
});
