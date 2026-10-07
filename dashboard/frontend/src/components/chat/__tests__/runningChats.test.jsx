import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getRunningChats = vi.fn();
vi.mock('../../../api', () => ({ getRunningChats: (...a) => getRunningChats(...a) }));

import { useRunningChats } from '../useRunningChats';
import ChatSidebar from '../ChatSidebar';
import { ChatPageContext } from '../context';
import { StreamContext } from '../../stream';

const handlers = new Map();
const refetchers = new Set();
const streamValue = {
  on: (channel, fn) => { handlers.set(channel, fn); return () => handlers.delete(channel); },
  onRefetch: (fn) => { refetchers.add(fn); return () => refetchers.delete(fn); },
  acquireChannel: () => () => {},
  clientId: 'tab',
};
const wrapper = ({ children }) => <StreamContext.Provider value={streamValue}>{children}</StreamContext.Provider>;

// The list marks every conversation being answered, whoever started the turn.

describe('useRunningChats', () => {
  beforeEach(() => { vi.clearAllMocks(); handlers.clear(); refetchers.clear(); });

  it('asks on mount and again when a turn starts or ends', async () => {
    vi.useFakeTimers();
    try {
      getRunningChats.mockResolvedValueOnce({ data: { conversations: ['a'] } });
      const { result } = renderHook(() => useRunningChats(), { wrapper });
      await act(async () => { await Promise.resolve(); });
      expect([...result.current]).toEqual(['a']);

      getRunningChats.mockResolvedValueOnce({ data: { conversations: ['a', 'b'] } });
      act(() => handlers.get('app')({ type: 'chat_turns.changed' }));
      await act(async () => { vi.advanceTimersByTime(350); await Promise.resolve(); });
      expect([...result.current].sort()).toEqual(['a', 'b']);

      getRunningChats.mockResolvedValueOnce({ data: { conversations: [] } });
      act(() => [...refetchers][0]());
      await act(async () => { await Promise.resolve(); });
      expect(result.current.size).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('ChatSidebar', () => {
  it('marks a conversation another tab is answering', async () => {
    const page = {
      currentConvId: 'a', deleteConversation: vi.fn(), flows: [], listOpen: false, navigate: vi.fn(),
      newConversation: vi.fn(), projects: [], setListOpen: vi.fn(), selectableAgents: [],
      selectedWorkspace: 'default', setConversations: vi.fn(), setSelectedAgent: vi.fn(),
      setSelectedFlow: vi.fn(), setSelectedProject: vi.fn(), setSelectedTeam: vi.fn(),
      setTargetMode: vi.fn(), syncError: false, t: (k) => k, teams: [],
      turns: { a: {} }, runningChats: new Set(['b']),
      visibleConversations: [
        { id: 'a', title: 'mine' }, { id: 'b', title: 'elsewhere' }, { id: 'c', title: 'idle' },
      ],
      visibleTelegramBindings: [],
    };
    render(<ChatPageContext.Provider value={page}><ChatSidebar /></ChatPageContext.Provider>);
    await waitFor(() => expect(screen.getAllByTestId('chat-conv-running')).toHaveLength(2));
    const rows = screen.getAllByRole('button').filter((el) => el.tagName === 'DIV');
    const marked = rows.filter((row) => row.querySelector('[data-testid="chat-conv-running"]'));
    expect(marked.map((row) => row.textContent)).toEqual([
      expect.stringContaining('mine'), expect.stringContaining('elsewhere'),
    ]);
  });
});
