import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

const updateAgentCapabilityOverride = vi.fn();
vi.mock('../../../api', () => ({
  updateAgentCapabilityOverride: (...a) => updateAgentCapabilityOverride(...a),
}));

import RefusalCard from '../RefusalCard';
import { MessageBubble } from '../MessageBubble';

const GUARD = {
  code: 'capability_guard', agent_id: 'scout', rule_id: 'lethal_trifecta',
  capabilities: ['ingests_untrusted', 'reads_private', 'can_exfiltrate'], override_allowed: true,
};
const show = (ui) => render(<I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>);

describe('RefusalCard, capability guard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('allows the agent in place and offers a retry', async () => {
    updateAgentCapabilityOverride.mockResolvedValue({ data: { capability_override: true, honoured_at_build: true } });
    const onRetry = vi.fn();
    show(<RefusalCard refusal={GUARD} onRetry={onRetry} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Allow for this agent' }));
    await waitFor(() => expect(updateAgentCapabilityOverride).toHaveBeenCalledWith('scout', true));
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('has no button for a rule that cannot be softened', () => {
    show(<RefusalCard refusal={{ ...GUARD, override_allowed: false }} />);
    expect(screen.queryByRole('button', { name: 'Allow for this agent' })).toBeNull();
  });

  it('says so when the exception could not be saved', async () => {
    updateAgentCapabilityOverride.mockRejectedValue({ response: { status: 500 } });
    show(<RefusalCard refusal={GUARD} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Allow for this agent' }));
    await screen.findByText(/Could not save the exception/);
  });
});

describe('MessageBubble with a refusal', () => {
  it('renders the card instead of the error text', () => {
    show(<MessageBubble msg={{ id: 'm1', role: 'agent', error: true, content: 'Error: Agent blocked', refusal: GUARD }} />);
    expect(screen.queryByText('Error: Agent blocked')).toBeNull();
    expect(screen.getByText('This agent is not allowed to run with these tools')).toBeTruthy();
  });
});

describe('RefusalCard, guardrail', () => {
  const TRIP = { code: 'guardrail', name: 'no-cards', stage: 'input', kind: 'pii',
    detail: { detectors: ['credit_card'] }, reason: 'contains what looks like credit card' };

  it('says which guardrail stopped the message and what it found', () => {
    show(<RefusalCard refusal={TRIP} />);
    expect(screen.getByText('The message was stopped by the “no-cards” guardrail')).toBeTruthy();
    expect(screen.getByText('The text contains what looks like a credit card number.')).toBeTruthy();
  });

  it('names the answer when the output was stopped', () => {
    show(<RefusalCard refusal={{ ...TRIP, stage: 'output' }} />);
    expect(screen.getByText('The agent’s answer was stopped by the “no-cards” guardrail')).toBeTruthy();
  });

  it('replaces the English error in the bubble', () => {
    show(<MessageBubble msg={{ id: 'm2', role: 'agent', error: true, refusal: TRIP,
      content: "Error: Guardrail 'no-cards' stopped the run on its input" }} />);
    expect(screen.queryByText(/stopped the run on its input/)).toBeNull();
    expect(screen.getByText('The message was stopped by the “no-cards” guardrail')).toBeTruthy();
  });
});

describe('GuardrailWarnings under a reply', () => {
  const WARN = { guardrail_id: 'g-w', name: 'no-cards', stage: 'input', kind: 'pii',
    detail: { detectors: ['credit_card'] }, reason: 'contains what looks like credit card' };

  it('keeps the reply and says what the warn guardrail found', () => {
    show(<MessageBubble msg={{ id: 'm3', role: 'agent', content: 'Here is the answer', guardrail_warnings: [WARN] }} />);
    expect(screen.getByText('Here is the answer')).toBeTruthy();
    const note = screen.getByTestId('guardrail-warnings');
    expect(note.textContent).toContain('The “no-cards” guardrail flagged the message.');
    expect(note.textContent).toContain('a credit card number');
  });
});
