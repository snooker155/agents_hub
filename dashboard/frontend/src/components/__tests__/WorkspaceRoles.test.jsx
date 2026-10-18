import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Workspace roles (agents/roles.py): each role is held by the product's own
// agent until the workspace gives it to another of its agents.

const ok = (data) => Promise.resolve({ data });

const getWorkspaceRoles = vi.fn();
const updateWorkspaceRole = vi.fn();
const getAgents = vi.fn(() => ok([
  { id: 'swe_agent', name: 'SWE Agent' }, { id: 'claude-code', name: 'Claude Code' },
]));

vi.mock('../../api', () => ({
  getWorkspaceRoles: (...a) => getWorkspaceRoles(...a),
  updateWorkspaceRole: (...a) => updateWorkspaceRole(...a),
  getAgents: (...a) => getAgents(...a),
}));

import WorkspaceRoles from '../workspace/WorkspaceRoles';

const wrap = (node) => render(<MemoryRouter><I18nProvider>{node}</I18nProvider></MemoryRouter>);

const coder = (overrides) => ({
  role: 'coder', ref: '@coder', summary: '', default: 'swe_agent', default_name: 'SWE Agent',
  agent: 'swe_agent', agent_name: 'SWE Agent', bound: null, stale: false,
  callers: ['main-agent', 'orchestrator'], ...overrides,
});

describe('WorkspaceRoles', () => {
  beforeEach(() => vi.clearAllMocks());

  it('gives a role to another agent of the workspace and back to the default', async () => {
    getWorkspaceRoles.mockReturnValue(ok({ roles: [coder()] }));
    updateWorkspaceRole.mockReturnValue(ok({ roles: [coder({ agent: 'claude-code', bound: 'claude-code' })] }));
    wrap(<WorkspaceRoles workspace="alpha" />);

    const select = await screen.findByLabelText('Coder');
    expect(screen.getByText('Called by: main-agent, orchestrator')).toBeTruthy();
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Claude Code' })).toBeTruthy());
    expect(within(select).getByRole('option', { name: 'SWE Agent (default)' })).toBeTruthy();

    fireEvent.change(select, { target: { value: 'claude-code' } });
    await waitFor(() => expect(updateWorkspaceRole).toHaveBeenCalledWith('alpha', 'coder', 'claude-code'));
    await waitFor(() => expect(select).toHaveValue('claude-code'));

    updateWorkspaceRole.mockReturnValue(ok({ roles: [coder()] }));
    fireEvent.change(select, { target: { value: 'swe_agent' } });
    await waitFor(() => expect(updateWorkspaceRole).toHaveBeenLastCalledWith('alpha', 'coder', ''));
  });

  it('says when the bound agent left the workspace', async () => {
    getWorkspaceRoles.mockReturnValue(ok({ roles: [coder({ bound: 'codex', stale: true })] }));
    wrap(<WorkspaceRoles workspace="alpha" />);
    expect(await screen.findByText('codex is no longer in this workspace, so swe_agent holds the role.')).toBeTruthy();
  });
});
