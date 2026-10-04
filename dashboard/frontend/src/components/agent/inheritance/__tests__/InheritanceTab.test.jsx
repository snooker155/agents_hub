import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../../i18n';

const ok = (data) => Promise.resolve({ data });

const getAgents = vi.fn();
const getAgentVersions = vi.fn();
vi.mock('../../../../api', () => ({
  getAgents: (...a) => getAgents(...a),
  getAgentVersions: (...a) => getAgentVersions(...a),
}));

const getAgentInheritance = vi.fn();
const setAgentExtends = vi.fn();
const resetAgentOverride = vi.fn();
vi.mock('../../../../api/agentInheritance', () => ({
  getAgentInheritance: (...a) => getAgentInheritance(...a),
  setAgentExtends: (...a) => setAgentExtends(...a),
  resetAgentOverride: (...a) => resetAgentOverride(...a),
}));

import InheritanceTab from '../InheritanceTab';

const INHERITANCE_DATA = {
  extends: 'analyst',
  extends_version: null,
  chain: [{ id: 'analyst', name: 'Analyst', version: 5, pinned: false }],
  children: [{ id: 'finance_analyst', name: 'Finance Analyst' }],
  fields: {
    model: { value: 'inherit-model', source: 'analyst', overridden: false },
    temperature: { value: 0.9, source: 'finance_analyst', overridden: true },
  },
  list_deltas: { tools: { add: ['web_search'], remove: [] } },
  effective_lists: {
    tools: [
      { value: 'read_file', source: 'analyst', added: false },
      { value: 'web_search', source: 'finance_analyst', added: true },
    ],
    removed: { tools: [] },
  },
  prompt: {
    parent_sections: [{ heading: '## Tone', source: 'analyst' }],
    own_sections: [{ heading: '## Domain', mode: 'new' }],
    inherited_instructions: 'Be precise.',
    effective: 'Be precise.\n\n## Domain\nFocus on finance.',
  },
};

const show = (props = {}) => render(
  <MemoryRouter>
    <I18nProvider>
      <InheritanceTab agentId="finance_analyst" agent={{ id: 'finance_analyst', system: false }} {...props} />
    </I18nProvider>
  </MemoryRouter>,
);

describe('InheritanceTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgentInheritance.mockImplementation(() => ok(INHERITANCE_DATA));
    getAgents.mockImplementation(() => ok([
      { id: 'analyst', name: 'Analyst', system: true },
      { id: 'finance_analyst', name: 'Finance Analyst', system: false },
    ]));
    getAgentVersions.mockImplementation(() => ok({ versions: [] }));
    setAgentExtends.mockImplementation(() => ok({}));
    resetAgentOverride.mockImplementation(() => ok({}));
  });

  it('shows the chain, the children and the field sources', async () => {
    show();
    expect(await screen.findByText('Analyst')).toBeTruthy();
    expect(screen.getByTestId('inheritance-children')).toHaveTextContent('Finance Analyst');
    expect(screen.getByText('inherit-model')).toBeTruthy();
    expect(screen.getByText('Overridden')).toBeTruthy();
  });

  it('marks an added tool and lets the delta be reset', async () => {
    const onChanged = vi.fn();
    show({ onChanged });
    expect(await screen.findByText('web_search')).toBeTruthy();
    const resetButtons = screen.getAllByText('Reset');
    // The list field's own reset sits beside the tools chips.
    fireEvent.click(resetButtons[resetButtons.length - 1]);
    await waitFor(() => expect(resetAgentOverride).toHaveBeenCalledWith('finance_analyst', 'tools'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('resets an overridden scalar field to inherited', async () => {
    show();
    await screen.findByText('Overridden');
    fireEvent.click(screen.getAllByText('Reset')[0]);
    await waitFor(() => expect(resetAgentOverride).toHaveBeenCalledWith('finance_analyst', 'temperature'));
  });

  it('detaches after confirming', async () => {
    const onChanged = vi.fn();
    show({ onChanged });
    fireEvent.click(await screen.findByText('Detach'));
    fireEvent.click(screen.getByText('Detach', { selector: 'button.bg-amber-600' }));
    await waitFor(() => expect(setAgentExtends).toHaveBeenCalledWith('finance_analyst', { extends: null, extends_version: null }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  const standaloneData = {
    extends: null, extends_version: null, chain: [], children: [], fields: {}, list_deltas: {},
    effective_lists: {}, prompt: null,
  };

  it('offers to attach a parent for a standalone agent', async () => {
    getAgentInheritance.mockImplementation(() => ok(standaloneData));
    show({ agent: { id: 'lonely', system: false } });
    expect(await screen.findByText('Make this a child of another agent')).toBeTruthy();
  });

  it('hides the attach action for a system agent', async () => {
    getAgentInheritance.mockImplementation(() => ok(standaloneData));
    show({ agent: { id: 'sys', system: true } });
    expect(await screen.findByText('A system agent cannot extend another agent, it can still be a parent to others.')).toBeTruthy();
    expect(screen.queryByText('Make this a child of another agent')).toBeNull();
  });
});
