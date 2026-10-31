import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../api', () => ({ streamChat: vi.fn(), stopMessage: vi.fn() }));

import { streamChat } from '../../../api';
import { insertMention, mentionAt, mentionSuggestions, takeMention } from '../agentMention';
import { useAgentMention } from '../useAgentMention';
import { useChatSend } from '../useChatSend';

/**
 * `@agent` in a composer: the list opens on `@`, a pick writes `@<id> `, and
 * the send hands the chat to the named agent (useChatSend).
 */

const AGENTS = [
  { id: 'main-agent', name: 'Main agent' },
  { id: 'swe_agent', name: 'Coder' },
  { id: 'researcher', name: 'Web researcher' },
];

describe('mentionAt', () => {
  it('finds the @word at the caret', () => {
    expect(mentionAt('ask @co')).toEqual({ start: 4, query: 'co' });
    expect(mentionAt('@')).toEqual({ start: 0, query: '' });
    expect(mentionAt('ask @co now', 7)).toEqual({ start: 4, query: 'co' });
  });

  it('is null outside one', () => {
    expect(mentionAt('mail me at a@b')).toBeNull();
    expect(mentionAt('ask @coder now')).toBeNull();
    expect(mentionAt('')).toBeNull();
  });
});

describe('mentionSuggestions', () => {
  it('puts a prefix of the id or a name word first', () => {
    expect(mentionSuggestions(AGENTS, 'co').map((a) => a.id)).toEqual(['swe_agent']);
    expect(mentionSuggestions(AGENTS, 'res').map((a) => a.id)).toEqual(['researcher']);
    expect(mentionSuggestions(AGENTS, 'agent').map((a) => a.id)).toEqual(['main-agent', 'swe_agent']);
    expect(mentionSuggestions(AGENTS, '')).toHaveLength(3);
  });
});

describe('insertMention', () => {
  it('replaces what was typed after @ with the id and a space', () => {
    expect(insertMention('ask @co', 7, { start: 4, query: 'co' }, 'swe_agent'))
      .toEqual({ text: 'ask @swe_agent ', caret: 15 });
    expect(insertMention('@co fix it', 3, { start: 0, query: 'co' }, 'swe_agent'))
      .toEqual({ text: '@swe_agent fix it', caret: 11 });
  });
});

describe('takeMention', () => {
  it('takes the first known agent off the text', () => {
    expect(takeMention('@swe_agent fix the build', AGENTS)).toEqual({ agent: AGENTS[1], text: 'fix the build' });
    expect(takeMention('please @SWE_AGENT, look at it', AGENTS)?.text).toBe('please, look at it');
    expect(takeMention('@swe_agent', AGENTS)).toEqual({ agent: AGENTS[1], text: '' });
  });

  it('leaves an @word that is no agent', () => {
    expect(takeMention('@nobody hi', AGENTS)).toBeNull();
    expect(takeMention('write to a@swe_agent', AGENTS)).toBeNull();
  });
});

describe('useAgentMention', () => {
  it('opens on @, moves with the arrows and picks with Enter', () => {
    let text = 'hi @';
    const setText = vi.fn((v) => { text = v; });
    const { result, rerender } = renderHook(() => useAgentMention({
      text, setText, textareaRef: { current: null }, agents: AGENTS,
    }));
    act(() => result.current.onChange({ target: { value: 'hi @', selectionStart: 4 } }));
    expect(result.current.open).toBe(true);
    expect(result.current.items).toHaveLength(3);
    const key = (k) => ({ key: k, shiftKey: false, preventDefault: vi.fn() });
    act(() => { result.current.onKeyDown(key('ArrowDown')); });
    let took;
    act(() => { took = result.current.onKeyDown(key('Enter')); });
    expect(took).toBe(true);
    expect(setText).toHaveBeenCalledWith('hi @swe_agent ');
    rerender();
    expect(result.current.open).toBe(false);
    expect(result.current.named?.id).toBe('swe_agent');
  });

  it('lets the key through when closed', () => {
    const { result } = renderHook(() => useAgentMention({
      text: 'hello', setText: vi.fn(), textareaRef: { current: null }, agents: AGENTS,
    }));
    expect(result.current.onKeyDown({ key: 'Enter', preventDefault: vi.fn() })).toBe(false);
  });
});

function sendDeps(overrides) {
  return {
    agents: AGENTS,
    beginTurn: vi.fn(), noteTurn: vi.fn(), endTurn: vi.fn(), isTurnRunning: () => false,
    currentConvIdRef: { current: 'c1' }, clientId: 'tab', currentConvId: 'c1',
    loadProcessData: vi.fn(), mergeArtifact: vi.fn(), navigate: vi.fn(),
    pendingAttachments: [], pendingReferences: [], processOpen: false, selectCommand: vi.fn(),
    selectedAgent: 'main-agent', selectedFlow: '', selectedProject: '', selectedTeam: '',
    selectedWorkspace: 'default', setActiveRunId: vi.fn(), setAttachmentError: vi.fn(),
    setCurrentConvId: vi.fn(), setGraphRun: vi.fn(), setInput: vi.fn(),
    setPendingAttachments: vi.fn(), setPendingReferences: vi.fn(), setProcessInsights: vi.fn(),
    setSelectedAgent: vi.fn(), setSessionId: vi.fn(), setTargetMode: vi.fn(), t: (k) => k,
    targetMode: 'agent', textareaRef: { current: null },
    ...overrides,
  };
}

describe('sending with @agent', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('hands the conversation to the named agent and sends the rest of the text to it', async () => {
    streamChat.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'meta', run_id: 'r1', agent_id: 'swe_agent' });
      onEvent({ type: 'done', ok: true, response: 'Fixed.', run_id: 'r1', agent_id: 'swe_agent' });
    });
    let convs = [{ id: 'c1', agent_id: 'main-agent', target_mode: 'agent', workspace: 'default',
      messages: [{ id: 'u0', role: 'user', content: 'hi' }, { id: 'a0', role: 'agent', agent_id: 'main-agent', content: 'Hello' }] }];
    const deps = sendDeps({
      conversations: convs, input: '@swe_agent fix the build',
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
    });
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });

    const body = streamChat.mock.calls[0][0].body;
    expect(body).toMatchObject({ agent_id: 'swe_agent', message: 'fix the build', handed_from: 'main-agent' });
    expect(convs[0]).toMatchObject({ agent_id: 'swe_agent', target_mode: 'agent' });
    const last = convs[0].messages[convs[0].messages.length - 1];
    expect(last).toMatchObject({ role: 'agent', agent_id: 'swe_agent', content: 'Fixed.' });
    expect(last.handoff).toMatchObject({ by: 'user', from_agent_id: 'main-agent', to_agent_id: 'swe_agent' });
    expect(deps.setSelectedAgent).toHaveBeenCalledWith('swe_agent');
  });

  it('hands the chat over without a turn when the mention is all there is', async () => {
    let convs = [{ id: 'c1', agent_id: 'main-agent', workspace: 'default',
      messages: [{ id: 'u0', role: 'user', content: 'hi' }] }];
    const deps = sendDeps({
      conversations: convs, input: '@researcher',
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
    });
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });

    expect(streamChat).not.toHaveBeenCalled();
    expect(convs[0].agent_id).toBe('researcher');
    expect(convs[0].messages[1]).toMatchObject({ role: 'system', handoff: { by: 'user', to_agent_id: 'researcher' } });
    expect(deps.setSelectedAgent).toHaveBeenCalledWith('researcher');
    expect(deps.setInput).toHaveBeenCalledWith('');
  });

  it('leaves the text alone when it names the agent the chat is with', async () => {
    streamChat.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'done', ok: true, response: 'Ok.', run_id: 'r1', agent_id: 'main-agent' });
    });
    let convs = [{ id: 'c1', agent_id: 'main-agent', workspace: 'default', messages: [] }];
    const deps = sendDeps({
      conversations: convs, input: '@main-agent hi',
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
    });
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });
    const body = streamChat.mock.calls[0][0].body;
    expect(body.message).toBe('@main-agent hi');
    expect(body.handed_from).toBeUndefined();
  });

  it('with a team as the target, gives the request to a member and keeps the chat with the team', async () => {
    streamChat.mockImplementation(async () => {});
    const teams = [{ team_id: 'crew', name: 'Crew', members: [
      { agent_id: 'swe_agent', display_name: 'Sam', role: 'engineer' },
      { agent_id: 'reviewer', display_name: 'Rae', role: 'reviewer' },
    ] }];
    let convs = [{ id: 'c1', team_id: 'crew', target_mode: 'team', workspace: 'default', messages: [] }];
    const deps = sendDeps({
      conversations: convs, input: '@reviewer check the diff', targetMode: 'team', selectedTeam: 'crew', teams,
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
    });
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });

    const body = streamChat.mock.calls[0][0].body;
    expect(body).toMatchObject({ team_id: 'crew', team_member: 'reviewer', message: 'check the diff', agent_id: null });
    expect(body.handed_from).toBeUndefined();
    expect(convs[0]).toMatchObject({ target_mode: 'team', team_id: 'crew' });
    expect(convs[0].messages[1]).toMatchObject({
      role: 'system', handoff: { by: 'user', member: true, to_agent_id: 'reviewer', to_agent_name: 'Rae' },
    });
    expect(deps.setSelectedAgent).not.toHaveBeenCalled();
  });

  it('with a team as the target, an agent off the team is just text', async () => {
    streamChat.mockImplementation(async () => {});
    const teams = [{ team_id: 'crew', members: [{ agent_id: 'reviewer', display_name: 'Rae' }] }];
    let convs = [{ id: 'c1', team_id: 'crew', target_mode: 'team', workspace: 'default', messages: [] }];
    const deps = sendDeps({
      conversations: convs, input: '@swe_agent build it', targetMode: 'team', selectedTeam: 'crew', teams,
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
    });
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });
    const body = streamChat.mock.calls[0][0].body;
    expect(body).toMatchObject({ team_id: 'crew', message: '@swe_agent build it' });
    expect(body.team_member).toBeUndefined();
    expect(convs[0].target_mode).toBe('team');
  });
});
