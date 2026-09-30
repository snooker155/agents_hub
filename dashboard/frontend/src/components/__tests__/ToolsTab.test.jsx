import React from 'react';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const api = vi.hoisted(() => ({
  getAgentToolPolicy: vi.fn(),
  updateAgentToolPolicy: vi.fn(),
  getToolPolicyDecisions: vi.fn(),
}));
vi.mock('../../api/toolPolicy', () => api);
vi.mock('../../api', () => ({
  updateAgentClarifyGate: vi.fn(), updateAgentReasoning: vi.fn(),
  updateAgentResponseFormat: vi.fn(), updateAgentSelfDelegation: vi.fn(),
}));
vi.mock('../agent/AgentSecretsCard', () => ({ default: () => null }));
vi.mock('../agent/SystemAgentWarning', () => ({ default: () => null }));

const page = vi.hoisted(() => ({ current: null }));
vi.mock('../agent/context', () => ({ useAgentPage: () => page.current }));

import ToolsTab from '../agent/ToolsTab';

const t = (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k);

const policyBody = (toolPolicy = {}) => ({
  agent_id: 'a1',
  workspace: 'w1',
  tool_policy: toolPolicy,
  workspace_policy: {},
  effective: [
    { tool: 'read_file', mode: 'always_allow', source: 'default' },
    { tool: 'run_shell', mode: 'always_ask', source: 'approval_list' },
  ],
  default: { mode: 'always_allow', source: 'default' },
  modes: ['always_allow', 'always_ask', 'auto'],
  gate_enabled: true,
  classifier_model: 'anthropic/claude-haiku',
});

function makePage(overrides = {}) {
  const selected = overrides.selectedTools || ['read_file'];
  const setSelectedTools = vi.fn();
  return {
    PLAN_FORMATS: [], THINKING_LEVELS: [], THINK_MODES: [],
    agent: { id: 'a1', system: false }, allAgents: [],
    capabilityOverridden: false, capabilityViolation: null, capabilityOverride: false,
    capabilityOverrideSaving: false, capabilityGuardInfo: { guard_mode: 'block', honoured_at_build: true },
    capabilitySoftened: false, handleToggleCapabilityOverride: vi.fn(),
    delegatesViolation: null, toolsServerWarning: null,
    clarifyGate: false, clarifyGateSaving: false, delegates: [], delegatesDirty: { current: false },
    delegatesMessage: '', delegatesSaving: false,
    formatCategory: (c) => c, markDelegatesDirty: vi.fn(),
    handleSaveDelegates: vi.fn(), handleSaveTools: vi.fn().mockResolvedValue(undefined), id: 'a1',
    reasoningSettings: { thinkMode: 'off', thinkingLevel: 'low', planFormat: 'md' },
    regularToolIds: ['read_file', 'run_shell', 'web_search'],
    responseFormat: 'none', responseFormatSaving: false, selectedTools: selected, selfDelegation: false,
    selfDelegationSaving: false, setCategoryTools: vi.fn(), setClarifyGate: vi.fn(), setClarifyGateSaving: vi.fn(),
    setDelegates: vi.fn(), setDelegatesMessage: vi.fn(), setReasoningSettings: vi.fn(), setResponseFormat: vi.fn(),
    setResponseFormatSaving: vi.fn(), setSelfDelegation: vi.fn(), setSelfDelegationSaving: vi.fn(), t,
    toggleDelegate: vi.fn(), toggleTool: vi.fn(), toolCategories: [
      { category: 'files', ids: ['read_file', 'run_shell'] },
      { category: 'web', ids: ['web_search'] },
    ],
    toolsDirty: false, toolsMessage: '',
    toolsMeta: {
      read_file: { label: 'Read file', description: 'Reads a file', category: 'files' },
      run_shell: { label: 'Run shell', description: 'Runs a command', category: 'files' },
      web_search: { label: 'Web search', description: 'Searches the web', category: 'web' },
    },
    toolsSaving: false, fetchData: vi.fn(), selectedWorkspace: 'w1', setSelectedTools, autoTools: [],
    ...overrides,
  };
}

const renderTab = (overrides) => {
  page.current = makePage(overrides);
  return render(<MemoryRouter><ToolsTab /></MemoryRouter>);
};

describe('ToolsTab', () => {
  beforeEach(() => {
    api.getAgentToolPolicy.mockReset();
    api.updateAgentToolPolicy.mockReset();
    api.getToolPolicyDecisions.mockReset();
    api.getAgentToolPolicy.mockResolvedValue({ data: policyBody() });
    api.updateAgentToolPolicy.mockResolvedValue({ data: policyBody({ run_shell: 'auto', '*': 'always_ask' }) });
    api.getToolPolicyDecisions.mockResolvedValue({
      data: { decisions: [{ id: 'd1', tool: 'run_shell', decision: 'deny', by: 'auto', reason: 'deletes the repo' }] },
    });
  });

  it('shows every group open with its count and master switch', async () => {
    renderTab();
    await screen.findByLabelText('toolPolicy.defaultMode');
    expect(api.getAgentToolPolicy).toHaveBeenCalledWith('a1', 'w1');
    expect(screen.getByRole('checkbox', { name: 'Read file' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Web search' })).toBeInTheDocument();
    const group = screen.getByTestId('tool-category-files');
    expect(within(group).getByText('1/2')).toBeInTheDocument();
    fireEvent.click(within(group).getByRole('switch'));
    expect(page.current.setCategoryTools).toHaveBeenCalledWith(['read_file', 'run_shell'], true);
  });

  it('renders tools as cards, and a card click toggles the tool', async () => {
    renderTab();
    await screen.findByLabelText('toolPolicy.defaultMode');
    const card = await screen.findByRole('checkbox', { name: 'Read file' });
    expect(card).toHaveAttribute('aria-checked', 'true');
    expect(card.className).toMatch(/bg-green-50/);
    const off = screen.getByRole('checkbox', { name: 'Run shell' });
    expect(off).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(off);
    expect(page.current.toggleTool).toHaveBeenCalledWith('run_shell');
  });

  it('edits a tool policy inside the card without toggling the tool, and saves it with the default', async () => {
    renderTab();
    await screen.findByLabelText('toolPolicy.defaultMode');
    const select = await screen.findByLabelText('toolPolicy.modeFor {"tool":"run_shell"}');
    fireEvent.click(select);
    expect(page.current.toggleTool).not.toHaveBeenCalled();

    const save = screen.getByRole('button', { name: /agentDetails.saveTools/ });
    expect(save).toBeDisabled();
    fireEvent.change(select, { target: { value: 'auto' } });
    fireEvent.change(screen.getByLabelText('toolPolicy.defaultMode'), { target: { value: 'always_ask' } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await waitFor(() => expect(api.updateAgentToolPolicy).toHaveBeenCalledWith(
      'a1', { run_shell: 'auto', '*': 'always_ask' }, 'w1'));
    // The tool list was not dirty, so only the policy was saved.
    expect(page.current.handleSaveTools).not.toHaveBeenCalled();
    expect(await screen.findByText('toolPolicy.saved')).toBeInTheDocument();
    expect(page.current.fetchData).toHaveBeenCalled();
  });

  it('saves the tool list and the policy with the one button when both changed', async () => {
    renderTab({ toolsDirty: true });
    await screen.findByLabelText('toolPolicy.defaultMode');
    fireEvent.change(screen.getByLabelText('toolPolicy.defaultMode'), { target: { value: 'auto' } });
    fireEvent.click(screen.getByRole('button', { name: /agentDetails.saveTools/ }));
    await waitFor(() => expect(api.updateAgentToolPolicy).toHaveBeenCalled());
    expect(page.current.handleSaveTools).toHaveBeenCalled();
  });

  it('shows the refusal the backend sends back', async () => {
    api.updateAgentToolPolicy.mockRejectedValueOnce({ response: { data: { detail: 'Administrator access required' } } });
    renderTab();
    await screen.findByLabelText('toolPolicy.defaultMode');
    fireEvent.change(screen.getByLabelText('toolPolicy.defaultMode'), { target: { value: 'auto' } });
    fireEvent.click(screen.getByRole('button', { name: /agentDetails.saveTools/ }));
    expect(await screen.findByText('Administrator access required')).toBeInTheDocument();
  });

  it('lists the recent decisions and the classifier behind a toggle', async () => {
    renderTab();
    await screen.findByLabelText('toolPolicy.defaultMode');
    expect(screen.queryByText('deletes the repo')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /toolPolicy.recent/ }));
    expect(await screen.findByText('deletes the repo')).toBeInTheDocument();
    expect(screen.getByText('toolPolicy.decision.deny')).toBeInTheDocument();
    expect(screen.getByText(/anthropic\/claude-haiku/)).toBeInTheDocument();
  });

  it('lists the tools added automatically as read-only cards with their reason', async () => {
    renderTab({ autoTools: [
      { id: 'handoff_to_agent', label: 'Handoff To Agent', description: 'Give the conversation away', reason: 'handoffs' },
    ] });
    await screen.findByLabelText('toolPolicy.defaultMode');
    const group = screen.getByTestId('tool-category-auto');
    expect(within(group).getByText('Handoff To Agent')).toBeInTheDocument();
    expect(within(group).getByText('agentDetails.autoToolReasons.handoffs')).toBeInTheDocument();
    // Not a toggle: clicking the card changes nothing.
    fireEvent.click(within(group).getByText('Handoff To Agent'));
    expect(page.current.toggleTool).not.toHaveBeenCalled();
    // The policy still applies by id.
    fireEvent.change(within(group).getByLabelText('toolPolicy.modeFor {"tool":"handoff_to_agent"}'), { target: { value: 'always_ask' } });
    fireEvent.click(screen.getByRole('button', { name: /agentDetails.saveTools/ }));
    await waitFor(() => expect(api.updateAgentToolPolicy).toHaveBeenCalledWith('a1', { handoff_to_agent: 'always_ask' }, 'w1'));
  });

  it('keeps the delegation card visible but inactive without a delegation tool', async () => {
    renderTab({ allAgents: [{ id: 'b', name: 'Agent B' }] });
    await screen.findByLabelText('toolPolicy.defaultMode');
    const card = screen.getByTestId('delegation-card');
    expect(within(card).getByTestId('delegation-inactive')).toBeInTheDocument();
    const toggle = within(card).getByRole('button', { name: 'Off' });
    expect(toggle).toBeDisabled();
    expect(within(card).getByRole('button', { name: 'agentDetails.saveDelegation' })).toBeDisabled();
  });

  it('activates the delegation card once a delegation tool is on', async () => {
    renderTab({ selectedTools: ['read_file', 'delegate_task_tool'], allAgents: [{ id: 'b', name: 'Agent B' }] });
    await screen.findByLabelText('toolPolicy.defaultMode');
    const card = screen.getByTestId('delegation-card');
    expect(within(card).queryByTestId('delegation-inactive')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'Off' }));
    expect(page.current.toggleDelegate).toHaveBeenCalledWith('b');
  });

  it('shows which MCP group a tool came from', async () => {
    api.getAgentToolPolicy.mockResolvedValue({ data: {
      ...policyBody(),
      effective: [{ tool: 'mcp__tickets__search', mode: 'always_allow', source: 'default' }],
      groups: { 'mcp:tickets': ['mcp__tickets__search'] },
    } });
    renderTab({
      regularToolIds: ['mcp__tickets__search'],
      toolCategories: [{ category: 'mcp', ids: ['mcp__tickets__search'] }],
      toolsMeta: {},
    });
    await screen.findByLabelText('toolPolicy.defaultMode');
    expect(await screen.findByText(/toolPolicy.fromGroup.*mcp:tickets/)).toBeTruthy();
  });
});
