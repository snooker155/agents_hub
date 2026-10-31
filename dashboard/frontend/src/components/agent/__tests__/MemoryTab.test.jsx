import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import MemoryTab from '../MemoryTab';
import { AgentPageContext } from '../context';
import { I18nProvider, translate } from '../../../i18n';

const api = vi.hoisted(() => ({ updateAgentMemoryWrite: vi.fn(() => Promise.resolve({ data: {} })) }));

vi.mock('../../../api', () => ({
  updateAgentEpisodicConfig: vi.fn(),
  updateAgentMemoryWrite: (...a) => api.updateAgentMemoryWrite(...a),
  getAgentPersonalMemory: () => Promise.resolve({ data: {} }),
}));

const POOLS = [
  { id: 'p1', name: 'Main pool', description: '' },
  { id: 'p2', name: 'Notes pool', description: 'Save meeting decisions here' },
  { id: 'p3', name: 'Bare pool', description: '' },
  { id: 'p4', name: 'Locked pool', description: 'Save facts' },
];

const show = (memoryReadOnly = new Set(), overrides = {}) => {
  const page = {
    agent: { memory_type: 'shared' }, connectedPool: null, memoryType: 'shared',
    memoryPools: ['p1', 'p2', 'p3', 'p4'], memoryReadOnly, sharedMemories: POOLS,
    selectedWorkspace: 'default', t: (k, v) => translate('en', k, v), id: 'scout',
    markMemoryDraftDirty() {}, toggleReadOnly() {}, setPersonalMemory() {},
    fetchData: vi.fn(() => Promise.resolve()), setToolsSaving() {},
    ...overrides,
  };
  render(
    <MemoryRouter><I18nProvider>
      <AgentPageContext.Provider value={page}><MemoryTab /></AgentPageContext.Provider>
    </I18nProvider></MemoryRouter>,
  );
};

describe('MemoryTab additional pools', () => {
  it('says a pool without a description is read only and a described one writes', () => {
    show(new Set(['p4']));
    // p3 has no description; p2 writes (description as tooltip); p4 is marked read only.
    expect(screen.getAllByText('read only: no description')).toHaveLength(1);
    expect(screen.getByText('writes')).toHaveAttribute('title', 'Save meeting decisions here');
    expect(screen.getAllByText('writes')).toHaveLength(1);
  });
});

describe('MemoryTab memory writes', () => {
  it('turns every memory write off through the agent switch', async () => {
    show(new Set());
    const button = screen.getByRole('button', { name: 'Write Memory' });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(button);
    await waitFor(() => expect(api.updateAgentMemoryWrite).toHaveBeenCalledWith('scout', false));
  });

  it('shows the switch off for an agent with writes off, and for personal memory alone', () => {
    show(new Set(), {
      agent: { memory_type: 'none', memory_write: false }, memoryType: 'none', memoryPools: [],
      personalMemory: { effective: true },
    });
    expect(screen.getByRole('button', { name: 'Write Memory' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('no longer offers the local memory type', () => {
    show();
    expect(screen.queryByRole('option', { name: /local/i })).toBeNull();
  });
});
