import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

const getAgentCapabilityOverride = vi.fn();
const updateAgentCapabilityOverride = vi.fn();
const getBudget = vi.fn();
const setBudget = vi.fn();
vi.mock('../../../api', () => ({
  getAgentCapabilityOverride: (...a) => getAgentCapabilityOverride(...a),
  updateAgentCapabilityOverride: (...a) => updateAgentCapabilityOverride(...a),
  getBudget: (...a) => getBudget(...a),
  setBudget: (...a) => setBudget(...a),
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
    getAgentCapabilityOverride.mockResolvedValue({ data: { can_edit: true } });
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

  it('shows the reason without the button to a viewer', async () => {
    getAgentCapabilityOverride.mockResolvedValue({ data: { can_edit: false } });
    show(<RefusalCard refusal={GUARD} />);
    await screen.findByText(/Ask an editor/);
    expect(screen.queryByRole('button', { name: 'Allow for this agent' })).toBeNull();
    expect(screen.getByText(/reads private data/)).toBeTruthy();
  });

  it('has no button for a rule that cannot be softened', () => {
    show(<RefusalCard refusal={{ ...GUARD, override_allowed: false }} />);
    expect(screen.queryByRole('button', { name: 'Allow for this agent' })).toBeNull();
    expect(getAgentCapabilityOverride).not.toHaveBeenCalled();
  });

  it('says so when the caller may not edit', async () => {
    updateAgentCapabilityOverride.mockRejectedValue({ response: { status: 403 } });
    show(<RefusalCard refusal={GUARD} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Allow for this agent' }));
    await screen.findByText(/editor of this workspace/);
  });
});

describe('RefusalCard, budget', () => {
  it('links the workspace limit and retries', () => {
    const onRetry = vi.fn();
    show(<RefusalCard refusal={{ code: 'budget', kind: 'workspace', spent_usd: 12, limit_usd: 10 }} onRetry={onRetry} />);
    expect(screen.getByText(/\$12\.00 of its \$10\.00/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Raise the workspace limit' }).getAttribute('href')).toBe('/costs');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('opens the person limit on the Users page', () => {
    show(<RefusalCard refusal={{ code: 'budget', kind: 'person', spent_usd: 5, limit_usd: 5 }} />);
    expect(screen.getByRole('link', { name: 'Raise this limit' }).getAttribute('href')).toBe('/users');
  });
});

describe('MessageBubble with a refusal', () => {
  it('renders the card instead of the error text', () => {
    getAgentCapabilityOverride.mockResolvedValue({ data: { can_edit: true } });
    show(<MessageBubble msg={{ id: 'm1', role: 'agent', error: true, content: 'Error: Agent blocked', refusal: GUARD }} />);
    expect(screen.queryByText('Error: Agent blocked')).toBeNull();
    expect(screen.getByText('This agent is not allowed to run with these tools')).toBeTruthy();
  });
});

describe('RefusalCard, workspace limit raised in place', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('raises the hard limit keeping the other settings, then retries', async () => {
    getBudget.mockResolvedValue({ data: { hard_limit_usd: 5, soft_limit_usd: 4, period: 'daily', run_limit_usd: 1, fail_closed: true } });
    setBudget.mockResolvedValue({ data: {} });
    const onRetry = vi.fn();
    show(<RefusalCard refusal={{ code: 'budget', kind: 'workspace', workspace: 'w1', spent_usd: 5, limit_usd: 5 }} onRetry={onRetry} />);
    const input = screen.getByLabelText('New limit');
    expect(input.value).toBe('10');
    fireEvent.change(input, { target: { value: '25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Raise and try again' }));
    await waitFor(() => expect(setBudget).toHaveBeenCalledWith('w1', {
      hard_limit_usd: 25, soft_limit_usd: 4, period: 'daily', run_limit_usd: 1, fail_closed: true,
    }));
    await screen.findByText(/now \$25.00/);
    expect(onRetry).toHaveBeenCalled();
  });

  it('offers no inline raise for a person limit', () => {
    show(<RefusalCard refusal={{ code: 'budget', kind: 'person', spent_usd: 5, limit_usd: 5 }} />);
    expect(screen.queryByLabelText('New limit')).toBeNull();
  });
});
