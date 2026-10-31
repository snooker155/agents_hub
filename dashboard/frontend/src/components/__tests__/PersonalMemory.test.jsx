import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider, translate } from '../../i18n';

// Personal memory (memory/personal.py) is on or off per agent and workspace,
// and a workspace can turn it off for every agent: the agent card then shows
// it off and locked, and the Memory page's tab locks every agent's switch.

const ok = (data) => Promise.resolve({ data });

const getAgentPersonalMemory = vi.fn();
const updateAgentPersonalMemory = vi.fn();
const getWorkspacePersonalMemory = vi.fn();
const updateWorkspacePersonalMemory = vi.fn(() => ok({}));
const getAgents = vi.fn(() => ok([{ id: 'main-agent', name: 'Main' }, { id: 'scout', name: 'Scout' }]));

vi.mock('../../api', () => ({
  getAgentPersonalMemory: (...a) => getAgentPersonalMemory(...a),
  updateAgentPersonalMemory: (...a) => updateAgentPersonalMemory(...a),
  getWorkspacePersonalMemory: (...a) => getWorkspacePersonalMemory(...a),
  updateWorkspacePersonalMemory: (...a) => updateWorkspacePersonalMemory(...a),
  getAgents: (...a) => getAgents(...a),
}));

import PersonalMemoryCard from '../agent/PersonalMemoryCard';
import { PersonalMemoryTab } from '../memoryManager/PersonalMemoryTab';

const t = (key) => translate('en', key);
const wrap = (node) => render(<MemoryRouter><I18nProvider>{node}</I18nProvider></MemoryRouter>);

const card = (overrides) => ({
  workspace: 'alpha', enabled: true, workspace_enabled: true, is_main_agent: false,
  effective: true, has_own_pool: false, ...overrides,
});

describe('PersonalMemoryCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('switches the agent on and off in its workspace, with no auto mode', async () => {
    getAgentPersonalMemory.mockReturnValue(ok(card({ enabled: false, effective: false })));
    updateAgentPersonalMemory.mockReturnValue(ok(card()));
    wrap(<PersonalMemoryCard agentId="scout" workspace="alpha" t={t} />);
    const on = await screen.findByRole('button', { name: 'on' });
    expect(screen.queryByRole('button', { name: 'auto' })).toBeNull();
    fireEvent.click(on);
    await waitFor(() => expect(updateAgentPersonalMemory).toHaveBeenCalledWith('scout', true, 'alpha'));
  });

  it('is off and locked where the workspace has personal memory off', async () => {
    getAgentPersonalMemory.mockReturnValue(ok(card({ workspace_enabled: false, effective: false })));
    wrap(<PersonalMemoryCard agentId="scout" workspace="alpha" t={t} />);
    const off = await screen.findByRole('button', { name: 'off' });
    expect(off).toHaveAttribute('aria-pressed', 'true');
    expect(off).toBeDisabled();
    expect(screen.getByRole('button', { name: 'on' })).toBeDisabled();
    expect(screen.getByRole('link', { name: 'Memory → Personal memory' }))
      .toHaveAttribute('href', '/memory?tab=personal&workspace=alpha');
  });
});

describe('PersonalMemoryTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists the main agent first, on, and every other agent off', async () => {
    getWorkspacePersonalMemory.mockReturnValue(ok({
      workspace: 'alpha', enabled: true, main_agent: 'main-agent', agents: { 'main-agent': true },
    }));
    getAgents.mockReturnValueOnce(ok([{ id: 'scout', name: 'Scout' }, { id: 'main-agent', name: 'Main' }]));
    wrap(<PersonalMemoryTab workspaceFilter="alpha" />);
    const rows = await screen.findByTestId('personal-memory-agents');
    await waitFor(() => expect(rows).toHaveTextContent('Main'));
    const cards = [...rows.querySelectorAll('[role=checkbox]')];
    expect(cards.map((c) => c.getAttribute('aria-label'))).toEqual(['Main', 'Scout']);
    expect(cards.map((c) => c.getAttribute('aria-checked'))).toEqual(['true', 'false']);
    expect(cards.every((c) => !c.hasAttribute('aria-disabled'))).toBe(true);
    expect(getAgents).toHaveBeenCalledWith('alpha');
  });

  it('locks every agent off while the workspace has it off', async () => {
    getWorkspacePersonalMemory.mockReturnValue(ok({
      workspace: 'alpha', enabled: false, main_agent: 'main-agent', agents: { 'main-agent': true, scout: true },
    }));
    wrap(<PersonalMemoryTab workspaceFilter="alpha" />);
    const rows = await screen.findByTestId('personal-memory-agents');
    const cards = [...rows.querySelectorAll('[role=checkbox]')];
    expect(cards.map((c) => [c.getAttribute('aria-checked'), c.getAttribute('aria-disabled')]))
      .toEqual([['false', 'true'], ['false', 'true']]);
    fireEvent.click(cards[1]);
    expect(updateAgentPersonalMemory).not.toHaveBeenCalled();
  });

  it('turns an agent on with a click on its card', async () => {
    getWorkspacePersonalMemory.mockReturnValue(ok({
      workspace: 'alpha', enabled: true, main_agent: 'main-agent', agents: { 'main-agent': true },
    }));
    updateAgentPersonalMemory.mockReturnValue(ok({}));
    wrap(<PersonalMemoryTab workspaceFilter="alpha" />);
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Scout' }));
    await waitFor(() => expect(updateAgentPersonalMemory).toHaveBeenCalledWith('scout', true, 'alpha'));
  });
});
