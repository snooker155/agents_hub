import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../i18n';

const ok = (data) => Promise.resolve({ data });
const assignAgent = vi.fn();
let mode = 'any';
vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getAgents: () => ok([
    { id: 'swe_agent', name: 'SWE Agent', has_running_node: true },
    { id: 'writer', name: 'Writer', has_running_node: false },
  ]),
  listFlows: () => ok([{ id: 'fl_1', name: 'Release flow' }]),
  getTeams: () => ok({ teams: [{ team_id: 'tm_1', name: 'Site team' }] }),
  getLoops: () => ok({ loops: [{ loop_id: 'lp_1', name: 'Polish loop' }] }),
  getSettings: () => ok({ task_assignment_mode: mode }),
  assignAgent: (...a) => { assignAgent(...a); return ok({ run_id: 'run-9' }); },
}));

import AssignExecutorModal from '../task/AssignExecutorModal';

const TASK = { id: 't1', title: 'Menu page', workspace: 'zerno' };
const open = (props = {}) => render(
  <I18nProvider><AssignExecutorModal task={TASK} onClose={vi.fn()} onAssigned={vi.fn()} {...props} /></I18nProvider>);

beforeEach(() => { assignAgent.mockClear(); mode = 'any'; });

describe('AssignExecutorModal', () => {
  it('assigns an agent picked from the searchable list', async () => {
    const onAssigned = vi.fn();
    open({ onAssigned });
    await waitFor(() => expect(screen.getByTestId('assign-agent-picker')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('assign-agent-picker'));
    fireEvent.click(screen.getByRole('option', { name: /SWE Agent/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }));
    await waitFor(() => expect(assignAgent).toHaveBeenCalledWith('t1', { agent_id: 'swe_agent' }));
    expect(onAssigned).toHaveBeenCalledWith({ run_id: 'run-9' });
  });

  it.each([
    ['Team', 'team', 'Site team', 'tm_1'],
    ['Loop', 'loop', 'Polish loop', 'lp_1'],
    ['Flow', 'flow', 'Release flow', 'fl_1'],
  ])('assigns a %s as the executor', async (tab, kind, name, id) => {
    open();
    fireEvent.click(screen.getByRole('tab', { name: tab }));
    await waitFor(() => expect(screen.getByTestId(`assign-${kind}-picker`)).not.toBeDisabled());
    fireEvent.click(screen.getByTestId(`assign-${kind}-picker`));
    fireEvent.click(screen.getByRole('option', { name: new RegExp(name) }));
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }));
    await waitFor(() => expect(assignAgent).toHaveBeenCalledWith('t1', { executor: { kind, id } }));
  });

  it('in nodes-only mode an agent without a running node cannot be picked', async () => {
    mode = 'nodes_only';
    open();
    await waitFor(() => expect(screen.getByTestId('assign-agent-picker')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('assign-agent-picker'));
    const writer = screen.getByRole('option', { name: /Writer/ });
    expect(writer).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(writer);
    expect(screen.getByRole('button', { name: 'Assign' })).toBeDisabled();
  });
});
