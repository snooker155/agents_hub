import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The create wizard's template path: pick a ready agent, adjust its name,
// and create it through the template route with the template's prompt and tools.

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
const navigate = vi.fn();

vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getAgents: vi.fn(() => ok([])),
  getTasks: vi.fn(() => ok([])),
  getAgentTools: vi.fn(() => ok({ all: [] })),
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

    const idInput = screen.getByDisplayValue('data_agent_2');
    fireEvent.change(idInput, { target: { value: 'sales_data' } });
    fireEvent.change(screen.getByDisplayValue('Data agent'), { target: { value: 'Sales data' } });
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    expect(screen.getByText('- Every number comes from a query.')).toBeInTheDocument();
    expect(screen.getByText('How did weekly active users change?')).toBeInTheDocument();
    const create = screen.getAllByRole('button', { name: /create agent/i });
    fireEvent.click(create[create.length - 1]);

    await waitFor(() => expect(createAgentFromTemplate).toHaveBeenCalled());
    expect(createAgentFromTemplate).toHaveBeenCalledWith('data_agent', expect.objectContaining({
      id: 'sales_data',
      name: 'Sales data',
      system_prompt: 'You are a data agent.\n',
      tools: ['db_query', 'create_view'],
      workspace: 'acme',
    }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/agents/sales_data'));
  });
});
