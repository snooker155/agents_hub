import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import ConfigTab from '../ConfigTab';
import { AgentPageContext } from '../context';

const t = (key, vars) => (vars ? `${key} ${JSON.stringify(vars)}` : key);

function renderTab(definition, over = {}) {
  const page = {
    agent: { id: 'researcher', system: true },
    agentDefinition: { instructions: 'x', capabilities: '', usage: '', system_prompt: '', ...definition },
    defChat: { gridClass: '', mainClass: '', open: false },
    defDraft: { instructions: 'x', capabilities: '', usage: '' },
    defError: {}, defSaving: {}, definitionChat: null,
    handleDefinitionDraftChange: vi.fn(), handleResetDefinitionField: vi.fn(),
    handleRestoreShippedDefinition: vi.fn(), handleSaveDefinitionField: vi.fn(),
    restoringShipped: false, setActiveTab: vi.fn(), t,
    ...over,
  };
  render(<AgentPageContext.Provider value={page}><ConfigTab /></AgentPageContext.Provider>);
  return page;
}

describe('ConfigTab shipped text', () => {
  it('offers the restore only for an edited system agent', () => {
    renderTab({ system_definition: true, customized: false, customized_parts: [] });
    expect(screen.queryByText('agentDetails.shippedText.restore')).toBeNull();
  });

  it('names the edited files, marks them and restores on click', () => {
    const page = renderTab({ system_definition: true, customized: true, customized_parts: ['instructions'] });
    expect(screen.getByText(/instructions\.md"/)).toBeTruthy();
    expect(screen.getAllByText('agentDetails.shippedText.edited')).toHaveLength(1);
    fireEvent.click(screen.getByText('agentDetails.shippedText.restore'));
    expect(page.handleRestoreShippedDefinition).toHaveBeenCalledTimes(1);
  });
});
