import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getAgentVersions: vi.fn(),
  getAgentExperiment: vi.fn(),
  getAgentExperimentReport: vi.fn(),
  putAgentExperiment: vi.fn(),
  endAgentExperiment: vi.fn(),
  getAgentOnlineEvalSummary: vi.fn(),
  getAgentOnlineEvals: vi.fn(),
  listNotifyRules: vi.fn(),
  createNotifyRule: vi.fn(),
  updateNotifyRule: vi.fn(),
  deleteNotifyRule: vi.fn(),
}));
vi.mock('../../api', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'w1' }) }));
vi.mock('../../i18n', () => ({ useI18n: () => ({ t: (k) => k }) }));

import ExperimentCard from '../agent/ExperimentCard';
import LiveQualityCard from '../agent/LiveQualityCard';
import { experimentArms, graderSpec } from '../agent/onlineEvals';

describe('onlineEvals helpers', () => {
  it('builds two arms whose shares sum to one', () => {
    expect(experimentArms('1', 'current', '30')).toEqual([
      { version: 1, share: 0.3 }, { version: 'current', share: 0.7 },
    ]);
    expect(experimentArms('1', '1', '50')).toBeNull();
    expect(experimentArms('1', '2', '0')).toBeNull();
  });

  it('turns a form row into a grader spec', () => {
    expect(graderSpec({ kind: 'regex', value: ' hel+o ', weight: '2' }))
      .toEqual({ kind: 'regex', params: { pattern: 'hel+o' }, weight: 2 });
    expect(graderSpec({ kind: 'json_valid', value: '', weight: 0 }))
      .toEqual({ kind: 'json_valid', params: {}, weight: 1 });
  });
});

describe('ExperimentCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgentVersions.mockResolvedValue({ data: { versions: [{ version: 1 }, { version: 2 }] } });
    api.getAgentExperiment.mockResolvedValue({ data: { experiment: null, active: false } });
    api.putAgentExperiment.mockResolvedValue({ data: {} });
  });

  it('starts an experiment between the picked versions', async () => {
    render(<ExperimentCard agentId="a1" />);
    await waitFor(() => expect(api.getAgentVersions).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: 'agentDetails.experiment.start' }));
    await waitFor(() => expect(api.putAgentExperiment).toHaveBeenCalledWith('a1', {
      enabled: true, note: '',
      arms: [{ version: 2, share: 0.5 }, { version: 'current', share: 0.5 }],
    }));
  });

  it('shows the report of a running experiment', async () => {
    api.getAgentExperiment.mockResolvedValue({ data: { active: true, experiment: {
      experiment_id: 'e1', enabled: true, note: '', arms: [{ version: 1, share: 0.5 }, { version: 2, share: 0.5 }],
    } } });
    api.getAgentExperimentReport.mockResolvedValue({ data: { arms: [
      { version: 1, share: 0.5, runs: 3, completed: 2, failed: 1, mean_score: 0.75, pass_rate: 0.5 },
      { version: 2, share: 0.5, runs: 4, completed: 4, failed: 0, mean_score: 1, pass_rate: 1 },
    ] } });
    render(<ExperimentCard agentId="a1" />);
    expect(await screen.findByText('agentDetails.experiment.running')).toBeInTheDocument();
    expect(screen.getByText('0.75')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'agentDetails.experiment.stop' })).toBeInTheDocument();
  });
});

describe('LiveQualityCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgentOnlineEvalSummary.mockResolvedValue({ data: {
      count: 2, mean_score: 0.5, pass_rate: 0.5, pending_jobs: 1,
      by_version: [{ definition_version: 3, definition_hash: 'abc', count: 2, mean_score: 0.5, pass_rate: 0.5 }],
    } });
    api.getAgentOnlineEvals.mockResolvedValue({ data: { results: [] } });
    api.listNotifyRules.mockResolvedValue({ data: { rules: [
      { id: 'r1', kind: 'online_eval', agent_id: 'a1', sample_rate: 0.1, min_score: 0.7, graders: [{ kind: 'exact' }], enabled: true },
      { id: 'r2', kind: 'run_failed', agent_id: 'a1', enabled: true },
    ] } });
    api.createNotifyRule.mockResolvedValue({ data: { rule: { id: 'r3', kind: 'online_eval', graders: [] } } });
  });

  it('shows the summary by version and only the online eval rules', async () => {
    render(<LiveQualityCard agentId="a1" />);
    expect(await screen.findByText('v3')).toBeInTheDocument();
    expect(screen.getAllByText('agentDetails.liveQuality.ruleSummary')).toHaveLength(1);
  });

  it('creates an online_eval rule for this agent', async () => {
    render(<LiveQualityCard agentId="a1" />);
    await screen.findByText('v3');
    fireEvent.click(screen.getByRole('button', { name: 'agentDetails.liveQuality.addRule' }));
    fireEvent.change(screen.getByLabelText('agentDetails.liveQuality.fields.rubric'),
      { target: { value: 'Is it correct?' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentDetails.liveQuality.createRule' }));
    await waitFor(() => expect(api.createNotifyRule).toHaveBeenCalled());
    const [payload, workspace] = api.createNotifyRule.mock.calls[0];
    expect(workspace).toBe('w1');
    expect(payload).toMatchObject({
      kind: 'online_eval', agent_id: 'a1', sample_rate: 0.1, min_score: 0.7,
      graders: [{ kind: 'llm_judge', params: { rubric: 'Is it correct?' }, weight: 1 }],
    });
  });
});
