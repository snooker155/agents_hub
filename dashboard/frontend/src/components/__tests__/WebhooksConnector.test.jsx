import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  listNotifyEndpoints: vi.fn(),
  createNotifyEndpoint: vi.fn(),
  deleteNotifyEndpoint: vi.fn(),
  testNotifyEndpoint: vi.fn(),
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

import WebhooksConnector from '../connectors/WebhooksConnector';

describe('WebhooksConnector alert rules', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listNotifyEndpoints.mockResolvedValue({ data: { endpoints: [] } });
    api.listNotifyRules.mockResolvedValue({ data: { rules: [
      { id: 'r1', kind: 'online_eval', agent_id: 'a1', sample_rate: 0.2, min_score: 0.7,
        channels: ['dashboard'], enabled: true, threshold_usd: 0 },
      { id: 'r2', kind: 'spend_run_over', threshold_usd: 1.5, channels: ['dashboard'], enabled: true },
    ] } });
    api.createNotifyRule.mockResolvedValue({ data: { rule: { id: 'r3', kind: 'online_eval' } } });
  });

  it('summarizes an online_eval rule by sample and min score instead of dollars', async () => {
    render(<WebhooksConnector />);
    expect(await screen.findByText('connectors.webhooks.onlineEvalSummary {"rate":"20%","min":"0.70"}'))
      .toBeInTheDocument();
    expect(screen.getByText('$1.50')).toBeInTheDocument();
    expect(screen.queryByText('$0.00')).toBeNull();
  });

  it('creates an online_eval rule with one grader', async () => {
    render(<WebhooksConnector />);
    await screen.findByText('$1.50');
    const kindSelect = screen.getAllByRole('combobox').find((el) =>
      [...el.options].some((o) => o.value === 'online_eval'));
    fireEvent.change(kindSelect, { target: { value: 'online_eval' } });
    fireEvent.click(screen.getByRole('button', { name: /connectors.webhooks.addRule/ }));
    await waitFor(() => expect(api.createNotifyRule).toHaveBeenCalled());
    const [payload, workspace] = api.createNotifyRule.mock.calls[0];
    expect(workspace).toBe('w1');
    expect(payload).toMatchObject({
      kind: 'online_eval', sample_rate: 0.2, min_score: 0.7, threshold_usd: 0,
      graders: [{ kind: 'llm_judge', params: {}, weight: 1 }],
    });
  });
});
