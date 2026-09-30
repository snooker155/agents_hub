import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import MemoryManager from '../MemoryManager';
import { I18nProvider } from '../../i18n';

// The blocks tab is the only place the always-in-context layer of the prompt is
// visible, and the character counter is the only warning before a save is
// refused: the backend rejects a value over the block's limit with a 400. These
// tests cover that the tab renders a pool's blocks and that the counter turns
// red once the value passes the limit.

const ok = (data) => Promise.resolve({ data });

const POOL = {
  id: 'pool-1',
  name: 'Team pool',
  description: 'What the team knows',
  notes: [],
  structured_data: {},
  blocks: [
    { name: 'persona', value: 'You are careful.', limit_chars: 30, description: 'Who you are', read_only: false },
    { name: 'house_rules', value: '', limit_chars: 100, description: 'Standing rules', read_only: false },
  ],
};

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default', liveUpdates: false }),
}));

vi.mock('../../components/stream', () => ({
  useChannel: () => {},
  useLiveRefetch: () => {},
  useStream: () => ({ connected: false }),
}));

vi.mock('../../components/pageChat/pageChat', () => ({
  usePageChat: () => ({ open: false, toggle: () => {}, panel: null }),
  usePageChatPanel: () => ({ inlineSuppressed: false, setOpen: () => {} }),
}));

vi.mock('../../components/EntityChat', () => ({ default: () => null }));

vi.mock('../../api', () => ({
  getSharedMemories: () => ok([{ id: 'pool-1', name: 'Team pool', notes: [], structured_data: {} }]),
  getSharedMemory: () => ok(POOL),
  createSharedMemory: () => ok({}),
  deleteSharedMemory: () => ok({}),
  uploadMemoryFile: () => ok({}),
  deleteMemoryFile: () => ok({}),
  getMemoryChat: () => ok({ messages: [] }),
  clearMemoryChat: () => ok({}),
  stopMemoryChat: () => ok({}),
  memoryChatUrl: () => '/shared-memory/chat',
  indexMemoryFile: () => ok({}),
  deindexMemoryFile: () => ok({}),
  listMemoryFiles: () => ok([]),
  getRagConfig: () => ok({}),
  getAgents: () => ok([]),
  updateAgentMemory: () => ok({}),
  addMemoryNote: () => ok({}),
  updateMemoryNote: () => ok({}),
  deleteMemoryNote: () => ok({}),
  upsertMemoryBlock: () => ok(POOL),
  deleteMemoryBlock: () => ok(POOL),
  upsertMemoryStructuredSlot: () => ok({}),
  deleteMemoryStructuredSlot: () => ok({}),
  listMemoryEpisodes: () => ok({ episodes: [] }),
  getMemoryEpisodesStats: () => ok({ total: 0 }),
  deleteMemoryEpisode: () => ok({}),
  getMemoryGraph: () => ok({ nodes: [], edges: [] }),
  getMemoryGraphStats: () => ok({ node_count: 0 }),
  linkMemoryGraph: () => ok({}),
  deleteMemoryGraphNode: () => ok({}),
  deleteMemoryGraphEdge: () => ok({}),
  extractMemoryGraph: () => ok({}),
  mergeMemoryGraphSlots: () => ok({}),
  pruneMemoryGraphMirrors: () => ok({}),
}));

const renderPage = () => render(
  <I18nProvider>
    <MemoryRouter><MemoryManager /></MemoryRouter>
  </I18nProvider>,
);

/** Open the pool, then its Blocks tab. */
async function openBlocks() {
  renderPage();
  const pool = await screen.findByText('Team pool');
  fireEvent.click(pool);
  const tab = await screen.findByRole('button', { name: /Blocks/ });
  fireEvent.click(tab);
}

describe('MemoryManager blocks tab', () => {
  it('lists a pool\'s blocks with their description and counter', async () => {
    await openBlocks();
    await waitFor(() => expect(screen.getByText('persona')).toBeTruthy());
    expect(screen.getByText('house_rules')).toBeTruthy();
    expect(screen.getByText('Who you are')).toBeTruthy();
    // "You are careful." is 16 characters against a 30 character limit.
    expect(screen.getByText(/16 \/ 30 chars/)).toBeTruthy();
  });

  it('says blocks are always in the prompt', async () => {
    await openBlocks();
    await waitFor(() => expect(screen.getByText(/always in the agent/i)).toBeTruthy());
  });

  it('turns the counter red once the value is over the limit', async () => {
    await openBlocks();
    const box = await screen.findByDisplayValue('You are careful.');

    const before = screen.getByText(/16 \/ 30 chars/);
    expect(before.className).not.toMatch(/text-red/);

    fireEvent.change(box, { target: { value: 'x'.repeat(31) } });

    const after = await screen.findByText(/31 \/ 30 chars/);
    expect(after.className).toMatch(/text-red/);
    expect(after.textContent).toMatch(/1 over/);
  });
});
