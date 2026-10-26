import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The create wizard: the template path (pick a ready agent, adjust its name,
// create it through the template route with the template's prompt and tools)
// and the inherit path (pick a parent, add to it). Nobody types an id: the
// backend makes it from the name.

const ok = (data) => Promise.resolve({ data });

const SUMMARY = {
  id: 'data_agent',
  name: 'Data agent',
  description: 'Answers questions from the connected databases.',
  category: 'Data',
  icon: 'database',
  domain: 'analytics',
  tools: ['db_query', 'create_view'],
  connectors: { required: [{ name: 'databases', configured: false }], optional: [] },
  outcome: { rubric: '- Every number comes from a query.', max_iterations: 3 },
  starters: ['How did weekly active users change?'],
  next_steps: 'Connect a database.',
  suggested_id: 'data_agent_2',
};

const getAgentTemplates = vi.fn(() => ok([SUMMARY]));
const getAgentTemplate = vi.fn(() => ok({ ...SUMMARY, system_prompt: 'You are a data agent.\n' }));
const createAgentFromTemplate = vi.fn(() => ok({ agent: { id: 'sales_data' }, next_steps: 'Connect a database.' }));
const createCustomAgent = vi.fn(() => ok({ id: 'sql_reviewer' }));
const PARENT = { id: 'analyst', name: 'Analyst', description: 'Reads data.', tools: ['db_query', 'read_file'] };
const getAgent = vi.fn(() => ok(PARENT));
const navigate = vi.fn();

vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getAgents: vi.fn(() => ok([PARENT])),
  getAgent: (...args) => getAgent(...args),
  getAgentVersions: vi.fn(() => ok({ versions: [] })),
  createCustomAgent: (...args) => createCustomAgent(...args),
  addAgentToWorkspace: vi.fn(() => ok({})),
  getTasks: vi.fn(() => ok([])),
  getAgentTools: vi.fn(() => ok({ all: [
    { name: 'db_query', label: 'Database query', category: 'connectors', description: '' },
    { name: 'read_file', label: 'Read file', category: 'filesystem', description: '' },
    { name: 'write_file', label: 'Write file', category: 'filesystem', description: '' },
    { name: 'create_view', label: 'Create view', category: 'visualization', description: '' },
  ] })),
  getServices: vi.fn(() => ok({ items: [] })),
  getAgentModel: vi.fn(() => ok({})),
  getAgentTemplates: (...args) => getAgentTemplates(...args),
  getAgentTemplate: (...args) => getAgentTemplate(...args),
  createAgentFromTemplate: (...args) => createAgentFromTemplate(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'acme', workspaceFilter: 'acme', liveUpdates: false }),
}));

vi.mock('../../components/stream', () => ({ useLiveRefetch: () => {} }));

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

import AgentManager from '../AgentManager';

const show = () => render(
  <I18nProvider><MemoryRouter><AgentManager /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  getAgentTemplates.mockClear();
  getAgentTemplate.mockClear();
  createAgentFromTemplate.mockClear();
  createCustomAgent.mockClear();
  navigate.mockClear();
});

describe('AgentManager: create from a template', () => {
  it('creates the picked template with the edited name', async () => {
    show();
    const createButtons = await screen.findAllByRole('button', { name: /create agent/i });
    fireEvent.click(createButtons[0]);

    // The template path is the default choice on the first step.
    expect(screen.getByText('Start from a template')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    fireEvent.click(await screen.findByText('Data agent'));
    await waitFor(() => expect(getAgentTemplate).toHaveBeenCalledWith('data_agent', 'acme'));
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    expect(screen.queryByDisplayValue('data_agent_2')).not.toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue('Data agent'), { target: { value: 'Sales data' } });
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    expect(screen.queryByText('- Every number comes from a query.')).not.toBeInTheDocument();
    expect(screen.queryByText('How did weekly active users change?')).not.toBeInTheDocument();
    const create = screen.getAllByRole('button', { name: /create agent/i });
    fireEvent.click(create[create.length - 1]);

    await waitFor(() => expect(createAgentFromTemplate).toHaveBeenCalled());
    expect(createAgentFromTemplate.mock.calls[0][1]).not.toHaveProperty('id');
    expect(createAgentFromTemplate).toHaveBeenCalledWith('data_agent', expect.objectContaining({
      name: 'Sales data',
      system_prompt: 'You are a data agent.\n',
      tools: ['db_query', 'create_view'],
      workspace: 'acme',
    }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/agents/sales_data'));
  });
});

describe('AgentManager: create based on an agent', () => {
  it('sends extends with the parent tools and no id', async () => {
    show();
    const createButtons = await screen.findAllByRole('button', { name: /create agent/i });
    fireEvent.click(createButtons[0]);

    fireEvent.click(screen.getByText('Based on an agent'));
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    const next = () => screen.getByRole('button', { name: /next/i });
    expect(next()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Based on'), { target: { value: 'analyst' } });
    await waitFor(() => expect(getAgent).toHaveBeenCalledWith('analyst'));
    expect(await screen.findByText('Tools of the parent: 2')).toBeInTheDocument();
    fireEvent.click(next());

    expect(screen.queryByText('Agent ID')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/code reviewer/i), { target: { value: 'SQL reviewer' } });
    fireEvent.click(next());

    // The tool picker holds the parent's tools; search by group adds one more.
    expect(screen.getByText('Database query')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search by tool name or group'), { target: { value: 'files' } });
    fireEvent.click(screen.getByRole('option', { name: /Write File/ }));
    fireEvent.click(next());

    const create = screen.getAllByRole('button', { name: /create agent/i });
    fireEvent.click(create[create.length - 1]);
    await waitFor(() => expect(createCustomAgent).toHaveBeenCalled());
    const body = createCustomAgent.mock.calls[0][0];
    expect(body).not.toHaveProperty('id');
    expect(body).toMatchObject({
      name: 'SQL reviewer',
      extends: 'analyst',
      tools: ['db_query', 'read_file', 'write_file'],
      workspace: 'acme',
    });
  });
});
