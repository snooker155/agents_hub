import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import BatchRunPanel from '../BatchRunPanel';
import { I18nProvider } from '../../../i18n';

const pollEvalRun = vi.fn();
const cancelEvalRun = vi.fn();

vi.mock('../../../api/evalBatches', () => ({
  pollEvalRun: (...args) => pollEvalRun(...args),
  cancelEvalRun: (...args) => cancelEvalRun(...args),
}));
vi.mock('../../../api', () => ({ getEvalRun: vi.fn(() => Promise.resolve({ data: {} })) }));

const RUN = {
  eval_run_id: 'evrun_1', status: 'batch_pending', mode: 'batch',
  batch: {
    pending: 1,
    batches: [{
      batch_row_id: 'evb_1', phase: 'target', provider: 'openai', model: 'gpt-4o-mini',
      provider_batch_id: 'batch_1', status: 'submitted', requests: 4, recorded: 0,
      checked_at: '2026-09-25T10:00:00Z', error: null,
    }],
  },
};

const show = (run, onChange = () => {}) => render(
  <I18nProvider><BatchRunPanel run={run} onChange={onChange} /></I18nProvider>,
);

beforeEach(() => {
  pollEvalRun.mockReset();
  cancelEvalRun.mockReset();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('BatchRunPanel', () => {
  it('lists the provider batches of a waiting run', () => {
    show(RUN);
    expect(screen.getByText('Waiting on the provider batches')).toBeTruthy();
    expect(screen.getByText('openai / gpt-4o-mini')).toBeTruthy();
    expect(screen.getByText('0 of 4 recorded')).toBeTruthy();
    expect(screen.getByText('at the provider')).toBeTruthy();
  });

  it('checks now and hands the fresh run back', async () => {
    const onChange = vi.fn();
    pollEvalRun.mockResolvedValue({ data: { ...RUN, status: 'completed' } });
    show(RUN, onChange);
    fireEvent.click(screen.getByText('Check now'));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'completed' })));
    expect(pollEvalRun).toHaveBeenCalledWith('evrun_1');
  });

  it('cancels after a confirmation', async () => {
    cancelEvalRun.mockResolvedValue({ data: { ...RUN } });
    show(RUN);
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(cancelEvalRun).toHaveBeenCalledWith('evrun_1'));
  });

  it('shows no actions once the run finished', () => {
    show({ ...RUN, status: 'completed' });
    expect(screen.getByText('Batch run finished')).toBeTruthy();
    expect(screen.queryByText('Check now')).toBeNull();
  });
});
