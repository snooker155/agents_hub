import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../api', () => ({ streamChat: vi.fn(), stopMessage: vi.fn() }));
vi.mock('../../../api/chat', () => ({ activateEntityChatSession: vi.fn(() => Promise.resolve({ data: {} })) }));
vi.mock('../../../api/assistant', async (importOriginal) => ({
  ...(await importOriginal()),
  streamAssistantTurn: vi.fn(),
  stopAssistant: vi.fn(() => Promise.resolve({})),
}));

import { streamChat } from '../../../api';
import { activateEntityChatSession } from '../../../api/chat';
import { AssistantError, stopAssistant, streamAssistantTurn } from '../../../api/assistant';
import { forkMessages, markLive, threadKey } from '../send/assistantThread';
import { useChatSend } from '../useChatSend';

/**
 * A conversation with the Assistant, continued on the Chat page: through
 * /api/assistant while the Assistant is picked (a past one made live first),
 * as a new ordinary conversation carrying its text when another agent is.
 */

const PAST = 'assistant~user-bob~s1';
const LIVE = 'assistant~user-bob~s2';
const thread = (id, active, extra = {}) => ({
  id, origin: 'assistant', agent_id: 'assistant', workspace: 'sales', title: 'Old topic',
  assistant_thread: { mode: 'personal', session_id: id.split('~').pop(), active },
  messages: [
    { id: 's-0', role: 'user', content: 'what failed today', voice: true },
    { id: 's-1', role: 'assistant', content: 'Two tasks failed.' },
  ],
  ...extra,
});

function setup({ convs, current = PAST, agent = 'assistant', input = 'and yesterday?' }) {
  let state = convs;
  const deps = {
    beginTurn: vi.fn(), noteTurn: vi.fn(), endTurn: vi.fn(), isTurnRunning: () => false,
    currentConvIdRef: { current }, clientId: '', conversations: state, currentConvId: current,
    input, loadProcessData: vi.fn(), mergeArtifact: vi.fn(), navigate: vi.fn(),
    pendingAttachments: [], pendingReferences: [], processOpen: false, reloadChat: vi.fn(),
    selectCommand: vi.fn(), selectedAgent: agent, selectedFlow: '', selectedProject: '', selectedTeam: '',
    selectedWorkspace: 'default', setActiveRunId: vi.fn(), setAttachmentError: vi.fn(),
    setConversations: (fn) => { state = typeof fn === 'function' ? fn(state) : fn; },
    setCurrentConvId: vi.fn(), setGraphRun: vi.fn(), setInput: vi.fn(), setPendingAttachments: vi.fn(),
    setPendingReferences: vi.fn(), setProcessInsights: vi.fn(), setSelectedAgent: vi.fn(),
    setSessionId: vi.fn(), t: (k) => k, targetMode: 'agent', textareaRef: { current: null },
  };
  const { result } = renderHook(() => useChatSend(deps));
  return { deps, send: (...a) => result.current.sendMessage(...a), get: () => state };
}

describe('the helpers', () => {
  it('read the thread key and carry the text over', () => {
    expect(threadKey(PAST)).toBe('user-bob');
    expect(threadKey('assistant~service-1~abc')).toBe('service-1');
    expect(forkMessages(thread(PAST, false).messages)).toEqual([
      { id: 'fork-0-s-0', role: 'user', content: 'what failed today', voice: true },
      { id: 'fork-1-s-1', role: 'agent', content: 'Two tasks failed.', agent_id: 'assistant' },
    ]);
  });

  it('mark one conversation of a thread live', () => {
    const out = markLive([thread(PAST, false), thread(LIVE, true), { id: 'c1', messages: [] }], PAST);
    expect(out.map((c) => c.assistant_thread?.active)).toEqual([true, false, undefined]);
  });
});

describe('sending to a conversation with the Assistant', () => {
  beforeEach(() => {
    streamChat.mockReset();
    streamAssistantTurn.mockReset();
    activateEntityChatSession.mockClear();
    stopAssistant.mockClear();
  });

  it('reopens a past one and sends through the Assistant with its workspace', async () => {
    streamAssistantTurn.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'meta', kind: 'assistant' });
      onEvent({ type: 'run', run_id: 'r9' });
      onEvent({ type: 'token', token: 'One failed.' });
      onEvent({ type: 'done', run_id: 'r9' });
    });
    const { deps, send, get } = setup({ convs: [thread(PAST, false), thread(LIVE, true)] });
    await act(async () => { await send(); });

    expect(activateEntityChatSession).toHaveBeenCalledWith({ kind: 'assistant', id: 'user-bob' }, 's1');
    expect(streamAssistantTurn).toHaveBeenCalledWith(expect.objectContaining({
      body: { message: 'and yesterday?', workspace: 'sales', mode: 'personal' },
    }));
    expect(streamChat).not.toHaveBeenCalled();
    const [past, live] = get();
    expect(past.assistant_thread.active).toBe(true);
    expect(live.assistant_thread.active).toBe(false);
    expect(past.messages.slice(-2)).toEqual([
      expect.objectContaining({ role: 'user', content: 'and yesterday?' }),
      expect.objectContaining({ role: 'agent', agent_id: 'assistant', content: 'One failed.' }),
    ]);
    expect(deps.noteTurn).toHaveBeenCalledWith(PAST, { runId: 'r9' });
    expect(deps.reloadChat).toHaveBeenCalledWith(PAST);
    // Stop goes to the Assistant's own route.
    deps.beginTurn.mock.calls[0][2].stop();
    expect(stopAssistant).toHaveBeenCalledWith('personal');
  });

  it('sends a voice message as said, without reopening the live one', async () => {
    streamAssistantTurn.mockImplementation(async ({ onEvent }) => onEvent({ type: 'done' }));
    const { send, get } = setup({ convs: [thread(LIVE, true)], current: LIVE, input: '' });
    await act(async () => { await send('hello there', { voice: true }); });
    expect(activateEntityChatSession).not.toHaveBeenCalled();
    expect(streamAssistantTurn.mock.calls[0][0].body).toMatchObject({ message: 'hello there', voice: true });
    expect(get()[0].messages.at(-2)).toMatchObject({ role: 'user', voice: true });
  });

  it('shows a refusal in the answer and keeps it', async () => {
    streamAssistantTurn.mockRejectedValue(new AssistantError(409, { code: 'busy', message: 'busy' }));
    const { deps, send, get } = setup({ convs: [thread(LIVE, true)], current: LIVE });
    await act(async () => { await send(); });
    expect(get()[0].messages.at(-1)).toMatchObject({ error: true, content: 'chat.assistantThread.busy' });
    expect(deps.reloadChat).not.toHaveBeenCalled();
  });

  it('starts a new conversation with its text when another agent is picked', async () => {
    streamChat.mockImplementation(async ({ onEvent }) => onEvent({ type: 'done', ok: true, response: 'Sure.' }));
    const { deps, send, get } = setup({ convs: [thread(PAST, false)], agent: 'researcher' });
    await act(async () => { await send(); });

    expect(streamAssistantTurn).not.toHaveBeenCalled();
    const [created, original] = get();
    expect(original.id).toBe(PAST);
    expect(original.messages).toHaveLength(2);
    expect(created.id).not.toBe(PAST);
    expect(created.agent_id).toBe('researcher');
    expect(created.messages.map((m) => m.content)).toEqual(
      ['what failed today', 'Two tasks failed.', 'and yesterday?', 'Sure.']);
    expect(deps.navigate).toHaveBeenCalledWith(`/chat/${created.id}`);
    const body = streamChat.mock.calls[0][0].body;
    expect(JSON.stringify(body)).toContain('Two tasks failed.');
  });
});
