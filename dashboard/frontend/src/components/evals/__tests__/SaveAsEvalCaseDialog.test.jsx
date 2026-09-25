import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SaveAsEvalCaseDialog from '../SaveAsEvalCaseDialog';
import { I18nProvider } from '../../../i18n';

const getEvalSetsForRun = vi.fn();
const createEvalSet = vi.fn();
const addEvalCase = vi.fn();

vi.mock('../../../api', () => ({
  getEvalSetsForRun: (...a) => getEvalSetsForRun(...a),
  createEvalSet: (...a) => createEvalSet(...a),
  addEvalCase: (...a) => addEvalCase(...a),
}));

const FOR_RUN_OK = {
  run: { run_id: 'run_1', target_kind: 'agent', target_id: 'writer', workspace: 'acme', status: 'completed', failed: false, finished: true },
  eval_sets: [{ eval_set_id: 'evs_1', name: 'Regression set' }],
  preview: { input: 'hi there', expected: 'hello world', metadata: {} },
  preview_error: null,
};

const FOR_RUN_FAILED = {
  run: { run_id: 'run_2', target_kind: 'agent', target_id: 'writer', workspace: 'acme', status: 'failed', failed: true, finished: true },
  eval_sets: [],
  preview: { input: 'add 2 and 2', expected: null, metadata: { error: 'rate limited' } },
  preview_error: null,
};

const show = (props = {}) => render(
  <I18nProvider>
    <SaveAsEvalCaseDialog runId="run_1" workspace="acme" onClose={() => {}} onSaved={() => {}} {...props} />
  </I18nProvider>,
);

beforeEach(() => {
  getEvalSetsForRun.mockReset();
  createEvalSet.mockReset();
  addEvalCase.mockReset();
});

describe('SaveAsEvalCaseDialog', () => {
  it('loads the fitting sets and preseeds input/expected from the preview', async () => {
    getEvalSetsForRun.mockResolvedValue({ data: FOR_RUN_OK });
    show();
    await waitFor(() => expect(getEvalSetsForRun).toHaveBeenCalledWith('run_1'));
    expect(await screen.findByDisplayValue('hi there')).toBeTruthy();
    expect(screen.getByDisplayValue('hello world')).toBeTruthy();
    expect(screen.getByText('Regression set')).toBeTruthy();
  });

  it('saves the case to the selected set', async () => {
    getEvalSetsForRun.mockResolvedValue({ data: FOR_RUN_OK });
    addEvalCase.mockResolvedValue({ data: { eval_set: { eval_set_id: 'evs_1' }, case: { case_id: 'c1' } } });
    const onSaved = vi.fn();
    show({ onSaved });
    await screen.findByDisplayValue('hi there');
    fireEvent.click(screen.getByText('Save case'));
    await waitFor(() => expect(addEvalCase).toHaveBeenCalledWith('evs_1', expect.objectContaining({
      from_run_id: 'run_1', input: 'hi there', expected: 'hello world',
    })));
    expect(onSaved).toHaveBeenCalled();
  });

  it('creates a new set when none is picked', async () => {
    getEvalSetsForRun.mockResolvedValue({ data: { ...FOR_RUN_OK, eval_sets: [] } });
    createEvalSet.mockResolvedValue({ data: { eval_set_id: 'evs_new' } });
    addEvalCase.mockResolvedValue({ data: { eval_set: {}, case: {} } });
    show();
    await screen.findByDisplayValue('hi there');
    fireEvent.change(screen.getByPlaceholderText('New eval set name'), { target: { value: 'my new set' } });
    fireEvent.click(screen.getByText('Save case'));
    await waitFor(() => expect(createEvalSet).toHaveBeenCalledWith(expect.objectContaining({
      name: 'my new set', target: { kind: 'agent', id: 'writer' },
    })));
    expect(addEvalCase).toHaveBeenCalledWith('evs_new', expect.objectContaining({ from_run_id: 'run_1' }));
  });

  it('starts a failed run with expected blank and shows the failed-run hint', async () => {
    getEvalSetsForRun.mockResolvedValue({ data: FOR_RUN_FAILED });
    render(
      <I18nProvider>
        <SaveAsEvalCaseDialog runId="run_2" workspace="acme" onClose={() => {}} />
      </I18nProvider>,
    );
    await screen.findByDisplayValue('add 2 and 2');
    expect(screen.getByText(/This run failed/)).toBeTruthy();
    expect(screen.getByText('What should have happened')).toBeTruthy();
    expect(screen.getByPlaceholderText('Leave blank, or describe the correct answer').value).toBe('');
  });

  it('disables Save when the run has no usable input', async () => {
    getEvalSetsForRun.mockResolvedValue({
      data: { run: { run_id: 'run_3', target_kind: 'scenario', target_id: 'scn1', workspace: 'acme', status: 'completed', failed: false, finished: true },
              eval_sets: [], preview: null, preview_error: 'Run has no recorded input to build a case from' },
    });
    render(
      <I18nProvider>
        <SaveAsEvalCaseDialog runId="run_3" workspace="acme" onClose={() => {}} />
      </I18nProvider>,
    );
    await waitFor(() => expect(getEvalSetsForRun).toHaveBeenCalled());
    expect(await screen.findByText('Run has no recorded input to build a case from')).toBeTruthy();
    expect(screen.getByText('Save case').closest('button')).toBeDisabled();
  });

  it('calls onClose from the header close button', async () => {
    getEvalSetsForRun.mockResolvedValue({ data: FOR_RUN_OK });
    const onClose = vi.fn();
    show({ onClose });
    await screen.findByDisplayValue('hi there');
    fireEvent.click(screen.getByTestId('save-as-eval-case-dialog').querySelector('button'));
    expect(onClose).toHaveBeenCalled();
  });
});
