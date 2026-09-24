import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';

vi.mock('../../../api', () => ({
  getAgents: vi.fn(async () => ({ data: { agents: [{ id: 'alpha', name: 'Alpha' }] } })),
  getTeams: vi.fn(async () => ({ data: { teams: [{ team_id: 'team_1', name: 'Crew' }] } })),
}));
vi.mock('../../../api/lab', () => ({
  getScenarioTemplates: vi.fn(async () => ({
    data: { templates: [{ id: 'lab', name: 'Research lab', description: 'A lab', environment: 'lab' }] },
  })),
  createScenarioFromTemplate: vi.fn(async () => ({ data: { scenario_id: 'scn_1' } })),
}));

import { createScenarioFromTemplate } from '../../../api/lab';
import { TemplateMenu, TemplateModal } from '../template-modal';

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);
const TEMPLATE = { id: 'lab', name: 'Research lab', description: 'A lab' };

describe('TemplateMenu', () => {
  it('lists the templates and hands the chosen one back', async () => {
    const onPick = vi.fn();
    wrap(<TemplateMenu onPick={onPick} />);
    fireEvent.click(screen.getByText('New from template'));
    fireEvent.click(await screen.findByText('Research lab'));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'lab' }));
  });
});

describe('TemplateModal', () => {
  beforeEach(() => { createScenarioFromTemplate.mockClear(); });

  it('creates the scenario with one agent for every role', async () => {
    const onCreated = vi.fn();
    wrap(<TemplateModal template={TEMPLATE} workspace="ws" onClose={() => {}} onCreated={onCreated} />);
    await screen.findByRole('option', { name: 'Alpha' });
    fireEvent.click(screen.getByText('Create'));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ scenario_id: 'scn_1' }));
    expect(createScenarioFromTemplate).toHaveBeenCalledWith({
      template: 'lab', workspace: 'ws', agentId: 'alpha', teamId: undefined,
    });
  });

  it('casts by team and refuses without one', async () => {
    wrap(<TemplateModal template={TEMPLATE} workspace="ws" onClose={() => {}} onCreated={() => {}} />);
    fireEvent.click(screen.getByText('A team'));
    await screen.findByRole('option', { name: 'Crew' });
    fireEvent.click(screen.getByText('Create'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Pick a team.');
    fireEvent.change(screen.getByLabelText('Team'), { target: { value: 'team_1' } });
    fireEvent.click(screen.getByText('Create'));
    await waitFor(() => expect(createScenarioFromTemplate).toHaveBeenCalledWith({
      template: 'lab', workspace: 'ws', agentId: undefined, teamId: 'team_1',
    }));
  });
});
