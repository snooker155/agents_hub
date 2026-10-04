import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

const ok = (data) => Promise.resolve({ data });
const getAgentDefaultOutcome = vi.fn();
const updateAgentDefaultOutcome = vi.fn();
const getModelsCatalog = vi.fn(() => ok({ providers: {} }));

vi.mock('../../api/agentOutcome', () => ({
  getAgentDefaultOutcome: (...a) => getAgentDefaultOutcome(...a),
  updateAgentDefaultOutcome: (...a) => updateAgentDefaultOutcome(...a),
}));
vi.mock('../../api', () => ({
  getModelsCatalog: (...a) => getModelsCatalog(...a),
}));

import AgentOutcomeCard from '../agent/AgentOutcomeCard';

const show = (props = {}) => render(
  <I18nProvider><AgentOutcomeCard agentId="triage" {...props} /></I18nProvider>,
);

describe('AgentOutcomeCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getModelsCatalog.mockImplementation(() => ok({ providers: {} }));
  });

  it('shows the empty state and an Add outcome button when the agent has none', async () => {
    getAgentDefaultOutcome.mockImplementation(() => ok({ default_outcome: null }));
    show();
    expect(await screen.findByTestId('agent-outcome')).toHaveTextContent('No default outcome set');
    expect(screen.getByText('Add outcome')).toBeInTheDocument();
  });

  it('shows the existing rubric and lets it be edited', async () => {
    getAgentDefaultOutcome.mockImplementation(() => ok({
      default_outcome: { rubric: '- Answers from the knowledge pool.', max_iterations: 2, grader: null, threshold: 0.8 },
    }));
    show();
    const card = await screen.findByTestId('agent-outcome');
    expect(card).toHaveTextContent('Answers from the knowledge pool.');
    expect(card).toHaveTextContent('up to 2 attempts');
  });

  it('saves a new rubric', async () => {
    getAgentDefaultOutcome.mockImplementation(() => ok({ default_outcome: null }));
    updateAgentDefaultOutcome.mockImplementation((_id, body) => ok({ default_outcome: { ...body, grader: null } }));
    show();
    fireEvent.click(await screen.findByText('Add outcome'));
    const rubric = screen.getByLabelText('Rubric (markdown)');
    fireEvent.change(rubric, { target: { value: '- Escalates what it is not sure about.' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(updateAgentDefaultOutcome).toHaveBeenCalled());
    expect(updateAgentDefaultOutcome.mock.calls[0][0]).toBe('triage');
    expect(updateAgentDefaultOutcome.mock.calls[0][1].rubric).toBe('- Escalates what it is not sure about.');
    await waitFor(() => expect(screen.getByTestId('agent-outcome'))
      .toHaveTextContent('Escalates what it is not sure about.'));
  });

  it('clears the outcome with an empty PUT on remove', async () => {
    getAgentDefaultOutcome.mockImplementation(() => ok({
      default_outcome: { rubric: '- A rubric.', max_iterations: 3, grader: null, threshold: null },
    }));
    updateAgentDefaultOutcome.mockImplementation(() => ok({ default_outcome: null }));
    window.confirm = vi.fn(() => true);
    show();
    fireEvent.click(await screen.findByText('Edit'));
    fireEvent.click(screen.getByText('Remove'));
    await waitFor(() => expect(updateAgentDefaultOutcome).toHaveBeenCalledWith('triage', {}));
    await waitFor(() => expect(screen.getByTestId('agent-outcome')).toHaveTextContent('No default outcome set'));
  });

  it('does not show edit controls for a read only (system) agent', async () => {
    getAgentDefaultOutcome.mockImplementation(() => ok({ default_outcome: null }));
    show({ readOnly: true });
    await screen.findByTestId('agent-outcome');
    expect(screen.queryByText('Add outcome')).not.toBeInTheDocument();
  });
});
