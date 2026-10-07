import { act, renderHook } from '@testing-library/react';
import { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const startChatOverSSE = vi.fn(() => Promise.resolve({ data: {} }));
const streamChat = vi.fn();
const stopMessage = vi.fn(() => Promise.resolve({}));
vi.mock('../../../api', () => ({
  startChatOverSSE: (...a) => startChatOverSSE(...a),
  streamChat: (...a) => streamChat(...a),
  stopMessage: (...a) => stopMessage(...a),
}));
const steerRun = vi.fn();
vi.mock('../../../api/steering', () => ({ steerRun: (...a) => steerRun(...a) }));
vi.mock('../../../api/palette', () => ({
  getMyPreferences: () => Promise.reject({ response: { status: 404 } }),
  putMyPreferences: () => Promise.resolve({}),
}));

import { DETACHED, streamChatOverChannel } from '../send/channelStream';
import { useChatTurns } from '../useChatTurns';
import { useChatSteering } from '../useChatSteering';
import { useChatSend } from '../useChatSend';

// Several conversations answering at once in one tab, each with its own turn.

function fakeStream() {
  const handlers = new Map();
  const acquired = [];
  return {
    on: (channel, fn) => {
      if (!handlers.has(channel)) handlers.set(channel, new Set());
      handlers.get(channel).add(fn);
      return () => handlers.get(channel).delete(fn);
    },
    acquireChannel: (channel) => { acquired.push(channel); return () => acquired.splice(acquired.indexOf(channel), 1); },
    emit: (channel, event) => { for (const fn of [...(handlers.get(channel) || [])]) fn(event); },
    listeners: (channel) => (handlers.get(channel)?.size || 0),
    acquired,
  };
}

describe('streamChatOverChannel', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('follows only its own turn and ends on the turn end', async () => {
    const stream = fakeStream();
    const onEvent = vi.fn();
    const done = streamChatOverChannel({
      body: { conversation_id: 'c1', client_turn_id: 'k1', client_id: 'tab' },
      onEvent, stream,
    });
    expect(startChatOverSSE).toHaveBeenCalledWith(expect.objectContaining({ client_turn_id: 'k1' }));
    expect(stream.acquired).toEqual(['chat:c1']);
    stream.emit('chat:c1', { type: 'turn_start', client_turn_id: 'k1' });
    stream.emit('chat:c1', { type: 'token', token: 'other', client_turn_id: 'k0' });
    stream.emit('chat:c1', { type: 'chat_saved' });
    stream.emit('chat:c1', { type: 'token', token: 'mine', client_turn_id: 'k1' });
    stream.emit('chat:c1', { type: 'chat_stream_end', client_turn_id: 'k1' });
    await done;
    expect(onEvent.mock.calls.map(([e]) => e.token)).toEqual(['mine']);
    expect(stream.listeners('chat:c1')).toBe(0);
    expect(stream.acquired).toEqual([]);
  });

  it('stops a run that names itself after the stop', async () => {
    const stream = fakeStream();
    const ctrl = new AbortController();
    const onLateRun = vi.fn();
    const done = streamChatOverChannel({
      body: { conversation_id: 'c1', client_turn_id: 'k1' }, onEvent: vi.fn(), stream,
      signal: ctrl.signal, onLateRun,
    });
    ctrl.abort();
    await expect(done).rejects.toMatchObject({ name: 'AbortError' });
    stream.emit('chat:c1', { type: 'meta', run_id: 'run-9', client_turn_id: 'k1' });
    expect(onLateRun).toHaveBeenCalledWith('run-9');
    stream.emit('chat:c1', { type: 'chat_stream_end', client_turn_id: 'k1' });
    expect(stream.listeners('chat:c1')).toBe(0);
  });

  it('lets go at once when detached, stopping nothing', async () => {
    const stream = fakeStream();
    const ctrl = new AbortController();
    const onLateRun = vi.fn();
    const done = streamChatOverChannel({
      body: { conversation_id: 'c1', client_turn_id: 'k1' }, onEvent: vi.fn(), stream,
      signal: ctrl.signal, onLateRun,
    });
    ctrl.abort(DETACHED);
    await expect(done).rejects.toMatchObject({ name: 'AbortError' });
    expect(stream.listeners('chat:c1')).toBe(0);
    stream.emit('chat:c1', { type: 'meta', run_id: 'run-9', client_turn_id: 'k1' });
    expect(onLateRun).not.toHaveBeenCalled();
  });

  it('fails when the turn cannot be started', async () => {
    startChatOverSSE.mockRejectedValueOnce({ response: { data: { detail: 'no such agent' } } });
    const stream = fakeStream();
    await expect(streamChatOverChannel({
      body: { conversation_id: 'c1', client_turn_id: 'k1' }, onEvent: vi.fn(), stream,
    })).rejects.toThrow('no such agent');
    expect(stream.listeners('chat:c1')).toBe(0);
  });
});

describe('useChatTurns', () => {
  it('keeps one turn per conversation, and a late end does not end the next turn', () => {
    const { result } = renderHook(() => useChatTurns());
    const first = new AbortController();
    const second = new AbortController();
    act(() => { result.current.begin('a', first); result.current.begin('b', second); });
    expect(Object.keys(result.current.turns).sort()).toEqual(['a', 'b']);
    act(() => result.current.note('a', { runId: 'r1' }));
    expect(result.current.get('a').runId).toBe('r1');
    act(() => { result.current.end('a'); result.current.begin('a', new AbortController()); });
    act(() => result.current.end('a', first));
    expect(result.current.isRunning('a')).toBe(true);
    expect(result.current.isRunning('b')).toBe(true);
  });

  it('detaches every turn when the page goes away', () => {
    const { result, unmount } = renderHook(() => useChatTurns());
    const detach = vi.fn();
    act(() => result.current.begin('a', new AbortController(), { detach }));
    unmount();
    expect(detach).toHaveBeenCalledTimes(1);
  });
});

describe('useChatSteering with several turns', () => {
  beforeEach(() => { vi.clearAllMocks(); window.localStorage.clear(); });

  function useHarness(sendMessage, initialTurns) {
    const [conversations, setConversations] = useState([
      { id: 'a', messages: [{ id: 'u', role: 'user', content: 'go' }, { id: 'x', role: 'agent', content: '', run_id: 'run-a' }] },
      { id: 'b', messages: [] },
    ]);
    const [turns, setTurns] = useState(initialTurns);
    const [currentConvId, setCurrentConvId] = useState('a');
    const [input, setInput] = useState('');
    const steering = useChatSteering({
      conversations, setConversations, currentConvId, input, setInput,
      loading: Boolean(turns[currentConvId]), sendMessage, stopGeneration: vi.fn(),
      targetMode: 'agent', turns,
    });
    return { steering, setTurns, setCurrentConvId, input };
  }

  it('sends what waited for a turn to its own conversation after the person moved on', async () => {
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage, { a: {}, b: {} }));
    await act(async () => { await result.current.steering.steer('also check March', 'queue'); });
    act(() => result.current.setCurrentConvId('b'));
    expect(result.current.steering.busy).toBe(true);
    act(() => result.current.setTurns({ b: {} }));
    expect(sendMessage).toHaveBeenCalledWith('also check March', { convId: 'a' });
  });

  it('talks to a turn this tab is not sending, without a queue', async () => {
    steerRun.mockResolvedValue({ data: { message: { msg_id: 'm1' } } });
    const sendMessage = vi.fn();
    const { result } = renderHook(() => {
      const [conversations, setConversations] = useState([{ id: 'a', messages: [] }]);
      const [input, setInput] = useState('');
      return useChatSteering({
        conversations, setConversations, currentConvId: 'a', input, setInput, loading: false,
        liveRunId: 'run-elsewhere', sendMessage, stopGeneration: vi.fn(), targetMode: 'agent', turns: {},
      });
    });
    expect(result.current.busy).toBe(true);
    expect(result.current.modes).toEqual(['inject', 'interrupt', 'system']);
    await act(async () => { await result.current.steer('use EUR', 'inject'); });
    expect(steerRun).toHaveBeenCalledWith('run-elsewhere', 'use EUR', 'inject');
    await act(async () => { await result.current.steer('start over', 'interrupt'); });
    expect(steerRun).toHaveBeenCalledWith('run-elsewhere', 'start over', 'interrupt', { send: true });
    expect(sendMessage).not.toHaveBeenCalled();
  });
});

describe('useChatSend for a conversation that is not open', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('talks to that conversation\'s target and leaves the page alone', async () => {
    let convs = [
      { id: 'open', agent_id: 'front', workspace: 'default', messages: [] },
      { id: 'other', agent_id: 'billing', target_mode: 'agent', workspace: 'ws2', messages: [] },
    ];
    const stream = fakeStream();
    const deps = {
      beginTurn: vi.fn(), noteTurn: vi.fn(), endTurn: vi.fn(), isTurnRunning: () => false,
      clientId: 'tab', conversations: convs, currentConvId: 'open', currentConvIdRef: { current: 'open' },
      input: 'typed in the open chat', loadProcessData: vi.fn(), mergeArtifact: vi.fn(), navigate: vi.fn(),
      pendingAttachments: [{ filename: 'a.txt', content: 'x' }], pendingReferences: [], processOpen: true,
      selectCommand: vi.fn(), selectedAgent: 'front', selectedFlow: '', selectedProject: '', selectedTeam: '',
      selectedWorkspace: 'default', setActiveRunId: vi.fn(), setAttachmentError: vi.fn(),
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
      setCurrentConvId: vi.fn(), setGraphRun: vi.fn(), setInput: vi.fn(), setPendingAttachments: vi.fn(),
      setPendingReferences: vi.fn(), setProcessInsights: vi.fn(), setSelectedAgent: vi.fn(),
      setSessionId: vi.fn(), stream, t: (k) => k, targetMode: 'agent', textareaRef: { current: null },
    };
    const { result } = renderHook(() => useChatSend(deps));
    let sent;
    await act(async () => {
      sent = result.current.sendMessage('what waited', { convId: 'other' });
      await Promise.resolve();
    });
    const body = startChatOverSSE.mock.calls[0][0];
    expect(body).toMatchObject({ agent_id: 'billing', conversation_id: 'other', workspace: 'ws2', message: 'what waited', attachments: [] });
    const key = body.client_turn_id;
    await act(async () => {
      stream.emit('chat:other', { type: 'meta', run_id: 'r-other', session_id: 's-other', client_turn_id: key });
      stream.emit('chat:other', { type: 'done', ok: true, response: 'Done.', run_id: 'r-other', client_turn_id: key });
      stream.emit('chat:other', { type: 'chat_stream_end', client_turn_id: key });
      await sent;
    });
    expect(deps.beginTurn).toHaveBeenCalledWith('other', expect.any(AbortController), expect.any(Object));
    expect(deps.noteTurn).toHaveBeenCalledWith('other', { runId: 'r-other' });
    expect(deps.noteTurn).toHaveBeenCalledWith('other', { sessionId: 's-other' });
    expect(deps.setActiveRunId).not.toHaveBeenCalled();
    expect(deps.setSessionId).not.toHaveBeenCalled();
    expect(deps.setInput).not.toHaveBeenCalled();
    expect(deps.setPendingAttachments).not.toHaveBeenCalled();
    expect(deps.endTurn).toHaveBeenCalledWith('other', expect.any(AbortController));
    const other = convs.find((c) => c.id === 'other');
    expect(other.messages.map((m) => m.content)).toEqual(['what waited', 'Done.']);
    expect(other.messages[1].agent_id).toBe('billing');
  });
});
