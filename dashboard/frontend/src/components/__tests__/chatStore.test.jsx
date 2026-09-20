import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api', () => ({
  listChats: vi.fn(),
  getChat: vi.fn(),
  saveChat: vi.fn(),
  deleteChat: vi.fn(),
  importChats: vi.fn(),
}));

// The live stream is a separate concern with its own tests; here it only has to
// exist, so the store can name the writer on a save and listen for other
// writers' saves.
const handlers = new Map();
vi.mock('../stream', () => ({
  useStream: () => ({ clientId: 'this-tab', on: () => () => {}, acquireChannel: () => () => {} }),
  useChannel: (channel, handler) => { if (channel) handlers.set(channel, handler); },
}));

import { deleteChat, getChat, importChats, listChats, saveChat } from '../../api';
import {
  LEGACY_STORAGE_KEY, MIGRATED_FLAG_KEY, SYNC_DEBOUNCE_MS, useConversationStore,
} from '../chatStore';

/**
 * What the Chat page's history has to do now that it is a service record and
 * not a `localStorage` key: come back from the server, be written back when it
 * changes, and — the part that can quietly destroy a transcript — never be
 * written back when it was never read.
 */

const CONV = {
  id: 'c1', title: 'first', workspace: 'default',
  messages: [{ id: 'm1', role: 'user', content: 'hello' }],
};

const summary = (conv) => ({
  ...conv, messages: [], message_count: (conv.messages || []).length, preview: 'hello',
});

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
  localStorage.clear();
  localStorage.setItem(MIGRATED_FLAG_KEY, '1');   // migration covered separately
  listChats.mockResolvedValue({ data: { items: [], total: 0 } });
  getChat.mockResolvedValue({ data: CONV });
  saveChat.mockResolvedValue({ data: {} });
  deleteChat.mockResolvedValue({ data: { deleted: true } });
  importChats.mockResolvedValue({ data: { imported: 0, skipped: 0 } });
});

// A test that fails mid-way must not leave fake timers on for the next one.
afterEach(() => { vi.useRealTimers(); });

const settle = async () => {
  await act(async () => { vi.advanceTimersByTime(SYNC_DEBOUNCE_MS + 50); });
};

describe('useConversationStore', () => {
  it('loads the list without transcripts and fetches the open chat', async () => {
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    const { result } = renderHook(() => useConversationStore('c1'));

    await waitFor(() => expect(result.current.ready).toBe(true));
    await waitFor(() => expect(result.current.conversations[0].messages).toHaveLength(1));
    expect(listChats).toHaveBeenCalledTimes(1);
    expect(getChat).toHaveBeenCalledWith('c1');
  });

  it('does not write a chat back just because it was opened', async () => {
    vi.useFakeTimers();
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    const { result } = renderHook(() => useConversationStore('c1'));

    await act(async () => {});
    await settle();

    expect(result.current.conversations[0].messages).toHaveLength(1);
    expect(saveChat).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('writes a changed conversation once the change settles', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useConversationStore(null));
    await act(async () => {});

    act(() => { result.current.setConversations([CONV]); });
    expect(saveChat).not.toHaveBeenCalled();          // still inside the debounce
    await settle();

    expect(saveChat).toHaveBeenCalledTimes(1);
    expect(saveChat.mock.calls[0][0].id).toBe('c1');
    expect(saveChat.mock.calls[0][1]).toBe('this-tab');
    vi.useRealTimers();
  });

  it('strips the live-turn fields that belong to the run record', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useConversationStore(null));
    await act(async () => {});

    act(() => {
      result.current.setConversations([{
        ...CONV,
        messages: [{
          id: 'm1', role: 'agent', content: 'hi',
          timeline: [{ step: 1 }], running_tool: 'shell', thinking_live: 'thinking…',
        }],
      }]);
    });
    await settle();

    const [stored] = saveChat.mock.calls[0];
    expect(stored.messages[0]).toEqual({ id: 'm1', role: 'agent', content: 'hi' });
    vi.useRealTimers();
  });

  it('never writes back a conversation whose transcript was not read', async () => {
    // The unread record carries an empty `messages` that means "unknown", not
    // "empty" — writing it would erase the stored transcript.
    vi.useFakeTimers();
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    getChat.mockRejectedValue({ message: 'network down' });
    const { result } = renderHook(() => useConversationStore('c1'));

    await act(async () => {});
    act(() => {
      result.current.setConversations((prev) => prev.map((c) => ({ ...c, title: 'renamed' })));
    });
    await settle();

    expect(saveChat).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('leaves a Telegram thread to the rebuild that owns it', async () => {
    // Its transcript comes from the runs behind the binding, not from here.
    listChats.mockResolvedValue({
      data: { items: [summary({ ...CONV, origin: 'telegram' })], total: 1 },
    });
    const { result } = renderHook(() => useConversationStore('c1'));

    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(getChat).not.toHaveBeenCalled();
  });

  it('takes in a save made somewhere else', async () => {
    // The same chat open on a phone, or answered from Telegram: the other
    // writer announces it and this tab reloads instead of drifting.
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    const { result } = renderHook(() => useConversationStore('c1'));
    await waitFor(() => expect(result.current.conversations[0].messages).toHaveLength(1));

    getChat.mockResolvedValue({
      data: { ...CONV, messages: [...CONV.messages, { id: 'm2', role: 'agent', content: 'answered' }] },
    });
    await act(async () => {
      handlers.get('chat:c1')?.({ type: 'chat_saved', chat_id: 'c1', origin_client: 'other-tab' });
    });

    await waitFor(() => expect(result.current.conversations[0].messages).toHaveLength(2));
  });

  it('does not reload on its own save', async () => {
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    const { result } = renderHook(() => useConversationStore('c1'));
    await waitFor(() => expect(result.current.ready).toBe(true));
    await waitFor(() => expect(getChat).toHaveBeenCalledTimes(1));  // the initial read

    await act(async () => {
      handlers.get('chat:c1')?.({ type: 'chat_saved', chat_id: 'c1', origin_client: 'this-tab' });
    });

    expect(getChat).toHaveBeenCalledTimes(1);
  });

  it('holds a reload until this tab is done writing', async () => {
    // Reloading mid-turn would drop the answer being streamed here.
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    const { result, rerender } = renderHook(
      ({ paused }) => useConversationStore('c1', { paused }),
      { initialProps: { paused: true } },
    );
    await waitFor(() => expect(result.current.ready).toBe(true));
    const readsBefore = getChat.mock.calls.length;

    await act(async () => {
      handlers.get('chat:c1')?.({ type: 'chat_saved', chat_id: 'c1', origin_client: 'other-tab' });
    });
    expect(getChat).toHaveBeenCalledTimes(readsBefore);

    rerender({ paused: false });
    await waitFor(() => expect(getChat.mock.calls.length).toBe(readsBefore + 1));
  });

  it('deletes a conversation on the server as well as in the list', async () => {
    listChats.mockResolvedValue({ data: { items: [summary(CONV)], total: 1 } });
    const { result } = renderHook(() => useConversationStore(null));
    await waitFor(() => expect(result.current.conversations).toHaveLength(1));

    await act(async () => { await result.current.removeConversation('c1'); });

    expect(deleteChat).toHaveBeenCalledWith('c1');
    expect(result.current.conversations).toHaveLength(0);
  });

  it('reports a failing write instead of losing it silently', async () => {
    vi.useFakeTimers();
    saveChat.mockRejectedValue({ message: 'offline' });
    const { result } = renderHook(() => useConversationStore(null));
    await act(async () => {});

    act(() => { result.current.setConversations([CONV]); });
    for (let i = 0; i < 6; i += 1) await settle();

    expect(result.current.syncError).toBe(true);
    // Retries are bounded: a backend that is down must not become a write per second.
    expect(saveChat.mock.calls.length).toBeLessThanOrEqual(3);
  });

  it('picks the backend up again once the cooldown has passed', async () => {
    vi.useFakeTimers();
    saveChat.mockRejectedValue({ message: 'offline' });
    const { result } = renderHook(() => useConversationStore(null));
    await act(async () => {});

    act(() => { result.current.setConversations([CONV]); });
    for (let i = 0; i < 6; i += 1) await settle();
    expect(result.current.syncError).toBe(true);
    const spentBudget = saveChat.mock.calls.length;

    saveChat.mockResolvedValue({ data: {} });
    await act(async () => { vi.advanceTimersByTime(31000); });
    act(() => {
      result.current.setConversations([{ ...CONV, title: 'typed something new' }]);
    });
    await settle();

    expect(saveChat.mock.calls.length).toBeGreaterThan(spentBudget);
    expect(result.current.syncError).toBe(false);
  });

  it('shows what this browser still holds when the backend is unreachable', async () => {
    listChats.mockRejectedValue({ message: 'offline' });
    localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify([CONV]));
    const { result } = renderHook(() => useConversationStore(null));

    await waitFor(() => expect(result.current.conversations).toHaveLength(1));
    expect(result.current.conversations[0].title).toBe('first');
  });

  it('hands the old localStorage history to the server exactly once', async () => {
    localStorage.removeItem(MIGRATED_FLAG_KEY);
    localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify([CONV]));

    const first = renderHook(() => useConversationStore(null));
    await waitFor(() => expect(importChats).toHaveBeenCalledWith([CONV]));
    first.unmount();

    renderHook(() => useConversationStore(null));
    await waitFor(() => expect(listChats).toHaveBeenCalledTimes(2));
    expect(importChats).toHaveBeenCalledTimes(1);
    // The browser's copy is kept as a safety net, not deleted.
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeTruthy();
  });

  it('retries the migration if the server could not take it', async () => {
    localStorage.removeItem(MIGRATED_FLAG_KEY);
    localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify([CONV]));
    importChats.mockRejectedValue({ message: 'offline' });

    const first = renderHook(() => useConversationStore(null));
    await waitFor(() => expect(importChats).toHaveBeenCalledTimes(1));
    first.unmount();
    importChats.mockResolvedValue({ data: { imported: 1, skipped: 0 } });

    renderHook(() => useConversationStore(null));
    await waitFor(() => expect(importChats).toHaveBeenCalledTimes(2));
  });
});
