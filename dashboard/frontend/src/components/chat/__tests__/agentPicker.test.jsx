import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import AgentPicker from '../AgentPicker';
import {
  agentDomains, chatCounts, initials, matchScore, pickerSections, recentAgentIds,
} from '../agentPickerModel';

const AGENTS = [
  { id: 'main-agent', name: 'Main Agent', system: true, domain: 'general', is_default_chat_agent: true, tools: ['recall'] },
  { id: 'researcher', name: 'Researcher', system: true, domain: 'research', description: 'Finds sources', tools: ['web_search'] },
  { id: 'plot-manager', name: 'Plot Manager', system: false, domain: 'development', tools: ['run_shell'] },
  { id: 'writer', name: 'Writer', system: true, domain: 'Writing', tools: [] },
];

const CONVS = [
  { id: 'a', agent_id: 'writer', updated_at: '2026-10-26T10:00:00Z' },
  { id: 'b', agent_id: 'researcher', updated_at: '2026-10-26T12:00:00Z' },
  { id: 'c', agent_id: 'writer', updated_at: '2026-10-25T10:00:00Z' },
  { id: 'd', agent_id: 'plot-manager', flow_id: 'f', target_mode: 'flow', updated_at: '2026-10-26T13:00:00Z' },
];

describe('agentPickerModel', () => {
  it('lists recent agents newest first, once each, agent conversations only', () => {
    expect(recentAgentIds(CONVS)).toEqual(['researcher', 'writer']);
    expect(chatCounts(CONVS).get('writer')).toBe(2);
    expect(chatCounts(CONVS).has('plot-manager')).toBe(false);
  });

  it('puts each agent in the first section that has it', () => {
    const sections = pickerSections(AGENTS, { recent: ['researcher', 'main-agent'] });
    expect(sections.map((s) => [s.key, s.items.map((a) => a.id)])).toEqual([
      ['default', ['main-agent']],
      ['recent', ['researcher']],
      ['own', ['plot-manager']],
      ['system', ['writer']],
    ]);
  });

  it('filters by scope and by domain whatever its case', () => {
    expect(pickerSections(AGENTS, { scope: 'own' }).flatMap((s) => s.items).map((a) => a.id)).toEqual(['plot-manager']);
    expect(pickerSections(AGENTS, { domain: 'writing' }).flatMap((s) => s.items).map((a) => a.id)).toEqual(['writer']);
    expect(agentDomains(AGENTS).map((d) => d.key)).toEqual(['development', 'general', 'research', 'writing']);
  });

  it('ranks a search: name start, then a word, then description, then a tool', () => {
    expect(matchScore(AGENTS[0], 'main')).toBe(5);
    expect(matchScore(AGENTS[2], 'manager')).toBe(4);
    expect(matchScore(AGENTS[1], 'sources')).toBe(2);
    expect(matchScore(AGENTS[1], 'web_')).toBe(1);
    expect(matchScore(AGENTS[3], 'zzz')).toBe(0);
    const [only] = pickerSections(AGENTS, { query: 'man' });
    expect(only.key).toBe('matches');
    expect(only.items.map((a) => a.id)).toEqual(['plot-manager']);
  });

  it('makes initials from the name', () => {
    expect(initials({ id: 'x', name: 'Plot Manager' })).toBe('PM');
    expect(initials({ id: 'writer' })).toBe('WR');
  });
});

describe('AgentPicker', () => {
  const renderPicker = (onChange = vi.fn()) => {
    render(
      <I18nProvider>
        <MemoryRouter>
          <AgentPicker agents={AGENTS} value="main-agent" onChange={onChange} conversations={CONVS} />
        </MemoryRouter>
      </I18nProvider>,
    );
    return onChange;
  };

  it('shows the picked agent and opens a panel with sections and details', () => {
    renderPicker();
    expect(screen.getByTestId('agent-picker').textContent).toContain('Main Agent');
    fireEvent.click(screen.getByTestId('agent-picker'));
    expect(screen.getByTestId('agent-picker-panel')).toBeTruthy();
    expect(screen.getByText('Recent')).toBeTruthy();
    fireEvent.mouseEnter(screen.getByRole('option', { name: /Researcher/ }));
    expect(screen.getByTestId('agent-picker-details').textContent).toContain('Finds sources');
  });

  it('picks with the keyboard after a search', () => {
    const onChange = renderPicker();
    fireEvent.click(screen.getByTestId('agent-picker'));
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'writ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('writer');
    expect(screen.queryByTestId('agent-picker-panel')).toBeNull();
  });

  it('works as a form field with a no agent row and counts of its own', () => {
    const onChange = vi.fn();
    render(
      <I18nProvider>
        <MemoryRouter>
          <AgentPicker
            variant="field" agents={AGENTS} value="writer" onChange={onChange}
            noneOption={{ label: 'Let the orchestrator decide', hint: 'It picks one' }}
            recent={['researcher']} counts={new Map([['researcher', 3]])}
            labels={{ recent: 'In the plan', count: 'Jobs', pick: 'Pick', current: 'Picked' }}
          />
        </MemoryRouter>
      </I18nProvider>,
    );
    fireEvent.click(screen.getByTestId('agent-picker'));
    expect(screen.getByText('In the plan')).toBeTruthy();
    fireEvent.mouseEnter(screen.getByRole('option', { name: /Researcher/ }));
    const details = screen.getByTestId('agent-picker-details');
    expect(details.textContent).toContain('Jobs');
    expect(details.textContent).toContain('3');
    fireEvent.mouseEnter(screen.getByRole('option', { name: /orchestrator/ }));
    expect(screen.getByTestId('agent-picker-details').textContent).toContain('It picks one');
    fireEvent.click(screen.getByRole('button', { name: 'Pick' }));
    expect(onChange).toHaveBeenCalledWith('');
  });
});
