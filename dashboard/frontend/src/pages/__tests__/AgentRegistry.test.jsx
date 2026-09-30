import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The registry page is where an admin decides what gets to be shared: an
// agent somebody published, or an MCP server somebody asked to attach
// anywhere. Two things are worth holding onto: an admin sees the approve/
// reject actions and a plain operator does not, and the two hub toggles are
// admin-only controls, not just admin-only reading.

const ok = (data) => Promise.resolve({ data });

const REGISTRY = {
  agents: [
    {
      id: 'agent-a', name: 'Agent A', domain: 'general', system: false,
      owner_workspace: 'acme', shared: true, owner_user: 'u1',
      review_status: 'in_review', review_note: null, reviewed_by: null,
      reviewed_at: null, workspaces: ['acme'],
    },
    {
      id: 'agent-b', name: 'Agent B', domain: 'general', system: false,
      owner_workspace: 'acme', shared: false, owner_user: 'u2',
      review_status: 'draft', review_note: null, reviewed_by: null,
      reviewed_at: null, workspaces: ['acme'],
    },
  ],
  flows: [
    {
      id: 'flow-a', name: 'Flow A', owner_workspace: 'acme', shared: true,
      owner_user: 'u1', review_status: 'in_review', review_note: null,
      reviewed_by: null, reviewed_at: null, workspaces: ['acme'],
    },
  ],
  skills: [
    {
      id: 'skill-a', name: 'Skill A', owner_workspace: 'acme', shared: false,
      owner_user: 'u2', review_status: 'draft', review_note: null,
      reviewed_by: null, reviewed_at: null, workspaces: ['acme'],
    },
  ],
  mcp_servers: [
    { workspace: 'acme', id: 'tickets', name: 'Tickets', transport: 'stdio', enabled: true, capabilities: {}, last_error: '', approved: false },
  ],
  mcp_catalog: [
    { id: 'tickets', name: 'Tickets', transport: 'stdio', command: 'npx', args: [], url: '', owner_user: 'u1', status: 'requested', note: '' },
  ],
  settings: { registry_require_review: true, mcp_allowlist_only: false },
};

const getRegistry = vi.fn(() => ok(REGISTRY));
const submitAgentForReview = vi.fn(() => ok({}));
const approveAgent = vi.fn(() => ok({}));
const rejectAgent = vi.fn(() => ok({}));
const submitFlowForReview = vi.fn(() => ok({}));
const approveFlow = vi.fn(() => ok({}));
const rejectFlow = vi.fn(() => ok({}));
const submitSkillForReview = vi.fn(() => ok({}));
const approveSkill = vi.fn(() => ok({}));
const rejectSkill = vi.fn(() => ok({}));
const approveMcpCatalogEntry = vi.fn(() => ok({}));
const blockMcpCatalogEntry = vi.fn(() => ok({}));
const requestMcpCatalogEntry = vi.fn(() => ok({}));
const updateRegistrySettings = vi.fn(() => ok({ registry_require_review: false, mcp_allowlist_only: false }));

vi.mock('../../api/registry', () => ({
  getRegistry: (...a) => getRegistry(...a),
  submitAgentForReview: (...a) => submitAgentForReview(...a),
  approveAgent: (...a) => approveAgent(...a),
  rejectAgent: (...a) => rejectAgent(...a),
  submitFlowForReview: (...a) => submitFlowForReview(...a),
  approveFlow: (...a) => approveFlow(...a),
  rejectFlow: (...a) => rejectFlow(...a),
  submitSkillForReview: (...a) => submitSkillForReview(...a),
  approveSkill: (...a) => approveSkill(...a),
  rejectSkill: (...a) => rejectSkill(...a),
  approveMcpCatalogEntry: (...a) => approveMcpCatalogEntry(...a),
  blockMcpCatalogEntry: (...a) => blockMcpCatalogEntry(...a),
  requestMcpCatalogEntry: (...a) => requestMcpCatalogEntry(...a),
  getRegistrySettings: () => ok({ registry_require_review: true, mcp_allowlist_only: false }),
  updateRegistrySettings: (...a) => updateRegistrySettings(...a),
}));

let authState = { mode: 'multi', user: { id: 'admin1', role: 'admin' } };
vi.mock('../../components/auth', () => ({
  useAuth: () => authState,
  isAdmin: (auth) => auth?.mode === 'multi' && auth?.user?.role === 'admin',
  isMultiUser: (auth) => auth?.mode === 'multi',
}));

import AgentRegistry from '../AgentRegistry';

const show = () => render(
  <I18nProvider><MemoryRouter><AgentRegistry /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  [getRegistry, submitAgentForReview, approveAgent, rejectAgent,
    submitFlowForReview, approveFlow, rejectFlow,
    submitSkillForReview, approveSkill, rejectSkill,
    approveMcpCatalogEntry, blockMcpCatalogEntry, requestMcpCatalogEntry, updateRegistrySettings,
  ].forEach((fn) => fn.mockClear());
  getRegistry.mockImplementation(() => ok(REGISTRY));
  authState = { mode: 'multi', user: { id: 'admin1', role: 'admin' } };
});

describe('AgentRegistry — an admin viewer', () => {
  it('lists agents with their owner and review status', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    expect(screen.getByText('Agent B')).toBeInTheDocument();
    expect(screen.getAllByText('In review').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Draft').length).toBeGreaterThan(0);
  });

  it('can approve an agent waiting on review', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Approve'));
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(approveAgent).toHaveBeenCalledWith('agent-a', ''));
  });

  it('can reject an agent waiting on review', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Reject'));
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(rejectAgent).toHaveBeenCalledWith('agent-a', ''));
  });

  it('shows the two hub toggles and can flip one', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/Require review before publishing/i)).toBeInTheDocument());
    const checkbox = screen.getByText(/Require review before publishing/i)
      .closest('label').querySelector('input[type="checkbox"]');
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    await waitFor(() => expect(updateRegistrySettings).toHaveBeenCalledWith({ registry_require_review: false }));
  });

  it('switches to the MCP tab and shows the catalog request form', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /MCP servers/ }));
    await waitFor(() => expect(screen.getAllByText('Tickets').length).toBeGreaterThan(0));
    expect(screen.getByPlaceholderText('Server id')).toBeInTheDocument();
  });

  it('switches to the Flows tab and can approve one waiting on review', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Flows/ }));
    await waitFor(() => expect(screen.getByText('Flow A')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Approve'));
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(approveFlow).toHaveBeenCalledWith('flow-a', ''));
  });

  it('switches to the Skills tab and lists its draft skill', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Skills/ }));
    await waitFor(() => expect(screen.getByText('Skill A')).toBeInTheDocument());
    expect(screen.getAllByText('Draft').length).toBeGreaterThan(0);
  });
});

describe('AgentRegistry — a non-admin viewer', () => {
  beforeEach(() => {
    authState = { mode: 'multi', user: { id: 'u2', role: 'member' } };
  });

  it('does not show approve/reject or the hub toggles', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent A')).toBeInTheDocument());
    expect(screen.queryByText('Approve')).not.toBeInTheDocument();
    expect(screen.queryByText('Reject')).not.toBeInTheDocument();
    expect(screen.queryByText(/Require review before publishing/i)).not.toBeInTheDocument();
  });

  it('can submit its own draft agent for review', async () => {
    show();
    await waitFor(() => expect(screen.getByText('Agent B')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Submit for review'));
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(submitAgentForReview).toHaveBeenCalledWith('agent-b', ''));
  });

  it('can submit its own draft skill for review, but not one it does not own', async () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: /Skills/ }));
    await waitFor(() => expect(screen.getByText('Skill A')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Submit for review'));
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(submitSkillForReview).toHaveBeenCalledWith('skill-a', ''));

    fireEvent.click(screen.getByRole('button', { name: /Flows/ }));
    await waitFor(() => expect(screen.getByText('Flow A')).toBeInTheDocument());
    // Flow A is owned by u1 and already in_review: no submit button for u2.
    expect(screen.queryByText('Submit for review')).not.toBeInTheDocument();
  });
});
