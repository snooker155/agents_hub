import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import PromptSuggestionPanel from '../PromptSuggestionPanel';
import { I18nProvider } from '../../../i18n';

const getPromptSuggestions = vi.fn();
const suggestPromptFix = vi.fn();
const applyPromptSuggestion = vi.fn();
const dismissPromptSuggestion = vi.fn();

vi.mock('../../../api', () => ({
  getPromptSuggestions: (...a) => getPromptSuggestions(...a),
  suggestPromptFix: (...a) => suggestPromptFix(...a),
  applyPromptSuggestion: (...a) => applyPromptSuggestion(...a),
  dismissPromptSuggestion: (...a) => dismissPromptSuggestion(...a),
}));

const AGENT_SET = { eval_set_id: 'evs_1', target_kind: 'agent' };
const FLOW_SET = { eval_set_id: 'evs_2', target_kind: 'flow' };

const RUN_WITH_FAILURES = {
  eval_run_id: 'evrun_1', status: 'completed',
  summary: { baseline: { total: 2, passed: 1 } },
};
const RUN_ALL_PASSED = {
  eval_run_id: 'evrun_2', status: 'completed',
  summary: { baseline: { total: 2, passed: 2 } },
};

const SUGGESTION = {
  suggestion_id: 'sugg_1', eval_run_id: 'evrun_1', agent_id: 'writer', status: 'pending',
  old_instructions: 'Be terse.\nAnswer questions.',
  new_instructions: 'Be terse.\nAnswer questions.\nShow your work.',
  rationale: 'c_fail shows the sum was skipped.',
};

const show = (props = {}) => render(
  <I18nProvider>
    <PromptSuggestionPanel evalRun={RUN_WITH_FAILURES} evalSet={AGENT_SET} {...props} />
  </I18nProvider>,
);

beforeEach(() => {
  getPromptSuggestions.mockReset();
  suggestPromptFix.mockReset();
  applyPromptSuggestion.mockReset();
  dismissPromptSuggestion.mockReset();
  getPromptSuggestions.mockResolvedValue({ data: { suggestions: [] } });
});

describe('PromptSuggestionPanel', () => {
  it('renders nothing for a non-agent target', async () => {
    const { container } = render(
      <I18nProvider><PromptSuggestionPanel evalRun={RUN_WITH_FAILURES} evalSet={FLOW_SET} /></I18nProvider>,
    );
    await waitFor(() => expect(getPromptSuggestions).not.toHaveBeenCalled());
    expect(container.querySelector('[data-testid="prompt-suggestion-panel"]')).toBeNull();
  });

  it('renders nothing when every case passed', async () => {
    const { container } = render(
      <I18nProvider><PromptSuggestionPanel evalRun={RUN_ALL_PASSED} evalSet={AGENT_SET} /></I18nProvider>,
    );
    await waitFor(() => expect(getPromptSuggestions).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="prompt-suggestion-panel"]')).toBeNull();
  });

  it('offers to build a suggestion when there are failures', async () => {
    show();
    expect(await screen.findByText('Suggest prompt fix')).toBeTruthy();
  });

  it('builds a suggestion and shows the diff and rationale', async () => {
    suggestPromptFix.mockResolvedValue({ data: SUGGESTION });
    show();
    fireEvent.click(await screen.findByText('Suggest prompt fix'));
    await waitFor(() => expect(suggestPromptFix).toHaveBeenCalledWith('evrun_1'));
    expect(await screen.findByText(/c_fail shows the sum was skipped/)).toBeTruthy();
    expect(screen.getByText('Apply')).toBeTruthy();
    expect(screen.getByText('Dismiss')).toBeTruthy();
    expect(screen.getByText(/Show your work/)).toBeTruthy();
  });

  it('shows an existing pending suggestion without rebuilding it', async () => {
    getPromptSuggestions.mockResolvedValue({ data: { suggestions: [SUGGESTION] } });
    show();
    await screen.findByText('Apply');
    expect(suggestPromptFix).not.toHaveBeenCalled();
    expect(screen.queryByText('Suggest prompt fix')).toBeNull();
  });

  it('applies the suggestion and reports what happened', async () => {
    getPromptSuggestions.mockResolvedValue({ data: { suggestions: [SUGGESTION] } });
    applyPromptSuggestion.mockResolvedValue({
      data: { suggestion: { ...SUGGESTION, status: 'applied' }, eval_run: null },
    });
    show();
    fireEvent.click(await screen.findByText('Apply'));
    await waitFor(() => expect(applyPromptSuggestion).toHaveBeenCalledWith('sugg_1', { rerun: false }));
    expect(await screen.findByText('Applied to instructions.md')).toBeTruthy();
    expect(screen.queryByText('Apply')).toBeNull();
  });

  it('applies with rerun and hands the new run back', async () => {
    getPromptSuggestions.mockResolvedValue({ data: { suggestions: [SUGGESTION] } });
    const newRun = { eval_run_id: 'evrun_3', status: 'completed' };
    applyPromptSuggestion.mockResolvedValue({
      data: { suggestion: { ...SUGGESTION, status: 'applied' }, eval_run: newRun },
    });
    const onApplied = vi.fn();
    show({ onApplied });
    await screen.findByText('Apply');
    fireEvent.click(screen.getByLabelText('Run the set again to compare'));
    fireEvent.click(screen.getByText('Apply'));
    await waitFor(() => expect(applyPromptSuggestion).toHaveBeenCalledWith('sugg_1', { rerun: true }));
    expect(onApplied).toHaveBeenCalledWith(newRun);
  });

  it('dismisses the suggestion', async () => {
    getPromptSuggestions.mockResolvedValue({ data: { suggestions: [SUGGESTION] } });
    dismissPromptSuggestion.mockResolvedValue({ data: { ...SUGGESTION, status: 'dismissed' } });
    show();
    fireEvent.click(await screen.findByText('Dismiss'));
    await waitFor(() => expect(dismissPromptSuggestion).toHaveBeenCalledWith('sugg_1'));
    await waitFor(() => expect(screen.queryByText('Apply')).toBeNull());
  });

  it('shows a build error without crashing', async () => {
    suggestPromptFix.mockRejectedValue({ response: { data: { detail: 'no failures' } } });
    show();
    fireEvent.click(await screen.findByText('Suggest prompt fix'));
    expect(await screen.findByText('no failures')).toBeTruthy();
  });
});
