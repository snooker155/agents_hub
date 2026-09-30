import React from 'react';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const api = vi.hoisted(() => ({
  getModelsCatalog: vi.fn(),
}));
const outcomesApi = vi.hoisted(() => ({
  setTaskOutcome: vi.fn(),
  deleteTaskOutcome: vi.fn(),
  gradeTaskOutcome: vi.fn(),
}));
vi.mock('../../api', () => api);
vi.mock('../../api/outcomes', () => outcomesApi);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import TaskOutcomeCard from '../task/TaskOutcomeCard';

const RUBRIC = '- **Tests pass**: green\n- **Docs**: README';

const evaluation = (over = {}) => ({
  iteration: 1, trigger: 'run', passed: false, score: 0.7, cost_usd: 0.0042,
  grader: { provider: 'openai', model: 'gpt-grader' }, graded_at: '2026-09-24T10:00:00Z',
  feedback: 'Close, docs missing.',
  criteria: [
    { name: 'Tests pass', passed: true, score: 1, feedback: '' },
    { name: 'Docs', passed: false, score: 0.4, feedback: 'README does not mention --flag.' },
  ],
  ...over,
});

const taskWith = (outcome, evaluations = []) => ({
  id: 't1', outcome, outcome_evaluations: evaluations,
});

describe('TaskOutcomeCard', () => {
  beforeEach(() => {
    api.getModelsCatalog.mockResolvedValue({ data: { providers: {
      openai: { models: [{ id: 'gpt-grader', enabled: true }, { id: 'off', enabled: false }] },
    } } });
    outcomesApi.setTaskOutcome.mockResolvedValue({ data: {
      outcome: { rubric: RUBRIC, max_iterations: 4, grader: { provider: 'openai', model: 'gpt-grader' }, threshold: null },
      evaluations: [],
    } });
    outcomesApi.deleteTaskOutcome.mockResolvedValue({ data: { outcome: null, evaluations: [] } });
    outcomesApi.gradeTaskOutcome.mockResolvedValue({ data: {
      outcome: { rubric: RUBRIC, max_iterations: 3 },
      evaluations: [evaluation(), evaluation({ iteration: 2, trigger: 'manual', passed: true, score: 1 })],
    } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => vi.restoreAllMocks());

  it('is a compact affordance when the task has no outcome', () => {
    render(<TaskOutcomeCard task={taskWith(null)} onChanged={vi.fn()} />);
    expect(screen.getByText('outcomes.noneHint')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /outcomes.add/ })).toBeInTheDocument();
    expect(screen.queryByTestId('task-outcome-card')).toBeNull();
  });

  it('adds an outcome with the rubric, attempts, grader and threshold', async () => {
    const onChanged = vi.fn();
    render(<TaskOutcomeCard task={taskWith(null)} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole('button', { name: /outcomes.add/ }));
    fireEvent.change(screen.getByLabelText('outcomes.rubric'), { target: { value: RUBRIC } });
    fireEvent.change(screen.getByLabelText('outcomes.maxIterations'), { target: { value: '4' } });
    await screen.findByRole('option', { name: 'openai/gpt-grader' });
    expect(screen.queryByRole('option', { name: 'openai/off' })).toBeNull();
    fireEvent.change(screen.getByLabelText('outcomes.grader'), { target: { value: 'openai/gpt-grader' } });
    fireEvent.change(screen.getByLabelText('outcomes.threshold'), { target: { value: '0.8' } });
    fireEvent.click(screen.getByRole('button', { name: 'outcomes.save' }));

    await waitFor(() => expect(outcomesApi.setTaskOutcome).toHaveBeenCalledWith('t1', {
      rubric: RUBRIC, max_iterations: 4, grader: 'openai/gpt-grader', threshold: 0.8,
    }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByText('outcomes.noEvaluations')).toBeInTheDocument();
  });

  it('refuses an empty rubric without calling the server', () => {
    render(<TaskOutcomeCard task={taskWith(null)} />);
    fireEvent.click(screen.getByRole('button', { name: /outcomes.add/ }));
    fireEvent.click(screen.getByRole('button', { name: 'outcomes.save' }));
    expect(screen.getByText('outcomes.rubricRequired')).toBeInTheDocument();
    expect(outcomesApi.setTaskOutcome).not.toHaveBeenCalled();
  });

  it('lists gradings newest first with per-criterion feedback and cost', () => {
    const evals = [evaluation(), evaluation({ iteration: 2, passed: true, score: 1, cost_usd: 0.01,
      criteria: [{ name: 'Tests pass', passed: true, score: 1 }, { name: 'Docs', passed: true, score: 1 }] })];
    render(<TaskOutcomeCard task={taskWith({ rubric: RUBRIC, max_iterations: 3, threshold: null }, evals)} />);
    const rows = screen.getAllByTestId('outcome-evaluation');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText(/outcomes.iteration .*"n":2/)).toBeInTheDocument();
    expect(within(rows[0]).getByText('$0.0100')).toBeInTheDocument();
    // The newest is open; the older one opens on click and shows what failed.
    fireEvent.click(within(rows[1]).getByRole('button'));
    expect(within(rows[1]).getByText('README does not mention --flag.')).toBeInTheDocument();
    expect(within(rows[1]).getByText('outcomes.notMet')).toBeInTheDocument();
    expect(within(rows[1]).getByText(/Close, docs missing./)).toBeInTheDocument();
    expect(screen.getByText(/outcomes.attempts .*"used":2,"max":3/)).toBeInTheDocument();
  });

  it('grades now and shows the new grading', async () => {
    const onChanged = vi.fn();
    render(<TaskOutcomeCard task={taskWith({ rubric: RUBRIC, max_iterations: 3 }, [evaluation()])} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole('button', { name: /outcomes.gradeNow/ }));
    await waitFor(() => expect(outcomesApi.gradeTaskOutcome).toHaveBeenCalledWith('t1'));
    await waitFor(() => expect(screen.getAllByTestId('outcome-evaluation')).toHaveLength(2));
    expect(screen.getByText('outcomes.manual')).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it('shows the server refusal when grading is not possible', async () => {
    outcomesApi.gradeTaskOutcome.mockRejectedValueOnce({ response: { data: { detail: 'the task has no completed run to grade' } } });
    render(<TaskOutcomeCard task={taskWith({ rubric: RUBRIC, max_iterations: 3 })} />);
    fireEvent.click(screen.getByRole('button', { name: /outcomes.gradeNow/ }));
    expect(await screen.findByText('the task has no completed run to grade')).toBeInTheDocument();
  });

  it('marks a grader failure apart from an unmet outcome', () => {
    render(<TaskOutcomeCard task={taskWith({ rubric: RUBRIC, max_iterations: 3 },
      [evaluation({ error: 'the grader did not return per-criterion JSON', criteria: [] })])} />);
    expect(screen.getAllByText('outcomes.graderError').length).toBeGreaterThan(0);
    expect(screen.getByText('the grader did not return per-criterion JSON')).toBeInTheDocument();
  });

  it('removes the outcome after a confirmation', async () => {
    render(<TaskOutcomeCard task={taskWith({ rubric: RUBRIC, max_iterations: 3, grader: { provider: 'x', model: 'custom' } })} />);
    fireEvent.click(screen.getByRole('button', { name: /outcomes.edit/ }));
    // A grader outside the enabled catalogue is still offered, as it is stored.
    expect(screen.getByLabelText('outcomes.grader')).toHaveValue('x/custom');
    fireEvent.click(screen.getByRole('button', { name: /outcomes.remove/ }));
    await waitFor(() => expect(outcomesApi.deleteTaskOutcome).toHaveBeenCalledWith('t1'));
    expect(await screen.findByText('outcomes.noneHint')).toBeInTheDocument();
  });
});
