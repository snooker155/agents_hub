import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The Evals page accepts a deep link, /evals?set=<id>&run=<id>, which is what
// the scenario page's reproducibility button hands out after starting a
// sweep. The page must open that set and that run, not the newest run, and
// must consume the link once so a later click is not undone by the URL.

const ok = (data) => Promise.resolve({ data });

const getEvalSets = vi.fn();
const getEvalSet = vi.fn();
const getEvalRuns = vi.fn();
const getEvalRun = vi.fn();

vi.mock('../../api', () => ({
  getEvalSets: (...a) => getEvalSets(...a),
  getEvalSet: (...a) => getEvalSet(...a),
  getEvalRuns: (...a) => getEvalRuns(...a),
  getEvalRun: (...a) => getEvalRun(...a),
  createEvalSet: () => ok({}),
  deleteEvalSet: () => ok({}),
  addEvalCase: () => ok({}),
  deleteEvalCase: () => ok({}),
  estimateEvalRun: () => ok({}),
  runEvalSet: () => ok({}),
  getEvalRunDiff: () => ok({}),
  getEvalGraders: () => ok({ graders: [] }),
  getAgents: () => ok({ agents: [] }),
  listFlows: () => ok({ flows: [] }),
  getTeams: () => ok({ teams: [] }),
  getLoops: () => ok({ loops: [] }),
  getScenarios: () => ok({ scenarios: [] }),
  getEvalChat: () => ok({ messages: [] }),
  clearEvalChat: () => ok({}),
  stopEvalChat: () => ok({}),
  evalChatUrl: () => '/evals/chat',
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default', liveUpdates: false }),
}));

vi.mock('../../components/EntityChat', () => ({ default: () => null }));

import Evals from '../Evals';

const SET = {
  eval_set_id: 's1', name: 'Reproducibility: lab', workspace: 'default',
  target: { kind: 'scenario', id: 'scn1' }, cases: [], graders: [],
};
const RUNS = [
  { eval_run_id: 'r-newest', eval_set_id: 's1', status: 'completed' },
  { eval_run_id: 'r-linked', eval_set_id: 's1', status: 'completed' },
];

const show = (path) => render(
  <I18nProvider>
    <MemoryRouter initialEntries={[path]}><Evals /></MemoryRouter>
  </I18nProvider>,
);

beforeEach(() => {
  getEvalSets.mockReset();
  getEvalSet.mockReset();
  getEvalRuns.mockReset();
  getEvalRun.mockReset();
  getEvalSets.mockImplementation(() => ok({ eval_sets: [SET] }));
  getEvalSet.mockImplementation(() => ok(SET));
  getEvalRuns.mockImplementation(() => ok({ eval_runs: RUNS }));
  getEvalRun.mockImplementation((id) => ok({
    eval_run_id: id, eval_set_id: 's1', status: 'completed', configs: [], results: [],
  }));
});

describe('Evals deep link', () => {
  it('opens the linked set and the linked run, not the newest one', async () => {
    show('/evals?set=s1&run=r-linked');
    await waitFor(() => expect(getEvalSet).toHaveBeenCalledWith('s1'));
    await waitFor(() => expect(getEvalRun).toHaveBeenCalledWith('r-linked'));
    expect(getEvalRun).not.toHaveBeenCalledWith('r-newest');
  });

  it('falls back to the newest run when the linked run is not in the set', async () => {
    show('/evals?set=s1&run=r-elsewhere');
    await waitFor(() => expect(getEvalRun).toHaveBeenCalledWith('r-newest'));
  });

  it('opens nothing without a set parameter', async () => {
    show('/evals');
    await waitFor(() => expect(getEvalSets).toHaveBeenCalled());
    expect(getEvalSet).not.toHaveBeenCalled();
  });
});
