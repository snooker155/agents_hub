import { act, render, renderHook, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../api', () => ({ streamChat: vi.fn() }));
vi.mock('../context', async () => {
  const { createContext } = await import('react');
  return { useChatPage: vi.fn(), ChatPageContext: createContext(null) };
});

import { streamChat } from '../../../api';
import { I18nProvider } from '../../../i18n';
import { reduceLiveTurn } from '../../chatLiveTurn';
import ChatMessageList from '../ChatMessageList';
import { useChatPage } from '../context';
import HandoffDivider from '../HandoffDivider';
import { applyHandoff, liveHandoffBubbles } from '../handoff';
import { handleAgentEvent } from '../send/handleAgentResponse';
import { useChatSend } from '../useChatSend';

/**
 * A chat turn that changes hands (chat/handoff.py): the handing agent's bubble
 * closes with its reply, a divider says who took over and why, the receiving
 * agent's bubble streams below it, and the conversation (and the top bar)
 * moves to the receiving agent.
 */

const HANDOFF = {
  type: 'handoff',
  from_agent_id: 'front',
  from_agent_name: 'Front desk',
  to_agent_id: 'billing',
  to_agent_name: 'Billing',
  reason: 'invoice question',
  history_filter: 'full',
  run_id: 'r1',
  next_run_id: 'r2',
  from_response: 'Let me pass you to Billing.',
  usage: { inbound_tokens: 10, outbound_tokens: 5, total_tokens: 15 },
  tool_calls: 1,
  duration_ms: 800,
};

const conversation = () => ([{
  id: 'c1',
  agent_id: 'front',
  messages: [
    { id: 'u1', role: 'user', content: 'my invoice 42 is wrong' },
    { id: 'a1', role: 'agent', agent_id: 'front', content: 'Let me pass', run_id: 'r1' },
  ],
}]);

describe('applyHandoff', () => {
  it('closes the handing bubble, opens the receiving one and moves the conversation', () => {
    const [conv] = applyHandoff(conversation(), 'c1', 'a1', 'a2', HANDOFF);
    expect(conv.agent_id).toBe('billing');
    const [, first, second] = conv.messages;
    expect(first).toMatchObject({ id: 'a1', content: 'Let me pass you to Billing.', run_id: 'r1',
      inbound_tokens: 10, outbound_tokens: 5, tool_calls: 1, duration_ms: 800 });
    expect(first.handoff).toBeUndefined();
    expect(second).toMatchObject({ id: 'a2', role: 'agent', agent_id: 'billing', content: '', run_id: 'r2' });
    expect(second.handoff).toMatchObject({ to_agent_id: 'billing', reason: 'invoice question', next_run_id: 'r2' });
    expect(second.handoff.type).toBeUndefined();
    expect(second.handoff.usage).toBeUndefined();
  });

  it('leaves other conversations alone', () => {
    const other = { id: 'c2', agent_id: 'x', messages: [] };
    const out = applyHandoff([...conversation(), other], 'c1', 'a1', 'a2', HANDOFF);
    expect(out[1]).toBe(other);
  });
});

describe('HandoffDivider', () => {
  it('says who took over and why', () => {
    render(<I18nProvider><HandoffDivider handoff={HANDOFF} /></I18nProvider>);
    const line = screen.getByTestId('handoff-divider');
    expect(line).toHaveTextContent('Billing');
    expect(line).toHaveTextContent('invoice question');
    expect(line).toHaveAttribute('role', 'separator');
  });

  it('prefers the agent name the page knows', () => {
    render(<I18nProvider><HandoffDivider handoff={HANDOFF} agentName="Billing team" /></I18nProvider>);
    expect(screen.getByTestId('handoff-divider')).toHaveTextContent('Billing team');
  });
});

describe('the stream handler', () => {
  it('routes the events after a handoff into the receiving bubble', () => {
    let convs = conversation();
    const setConversations = (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; };
    const setActiveRunId = vi.fn();
    const ctx = {
      convId: 'c1', setConversations, setActiveRunId, setSessionId: vi.fn(), setGraphRun: vi.fn(),
      setProcessInsights: vi.fn(), mergeArtifact: vi.fn(), processOpen: false, isMultiAgent: false,
      isFlowMode: false, assistantId: 'a1', userMsgText: 'my invoice 42 is wrong', t: (k) => k,
      state: { runId: 'r1', finalPayload: null, currentNodeId: null, nodeMsgIds: {}, handoffs: [] },
    };

    handleAgentEvent(HANDOFF, ctx);
    expect(ctx.assistantId).not.toBe('a1');
    expect(setActiveRunId).toHaveBeenCalledWith('r2');
    expect(ctx.state.handoffs).toHaveLength(1);

    handleAgentEvent({ type: 'meta', run_id: 'r2', agent_id: 'billing' }, ctx);
    handleAgentEvent({ type: 'token', token: 'Corrected.' }, ctx);
    handleAgentEvent({ type: 'done', ok: true, response: 'Invoice 42 is corrected.', run_id: 'r2',
      agent_id: 'billing', handoff: { ...HANDOFF, type: undefined } }, ctx);

    const [, first, second] = convs[0].messages;
    expect(first.content).toBe('Let me pass you to Billing.');
    expect(second.agent_id).toBe('billing');
    expect(second.content).toBe('Invoice 42 is corrected.');
    expect(second.run_id).toBe('r2');
    expect(convs[0].agent_id).toBe('billing');
  });
});

describe('the transcript', () => {
  const page = (messages) => ({
    agentName: 'Front desk', agents: [{ id: 'front', name: 'Front desk' }, { id: 'billing', name: 'Billing' }],
    artifacts: {}, currentTelegramBinding: null, flows: [], jumpToArtifact: vi.fn(), liveMessages: [],
    liveTurn: null, loading: false, messages, messagesEndRef: { current: null }, renderedMessages: messages,
    runTimelineByRunId: {}, selectedFlow: '', selectedTeam: '', selectedWorkspace: 'default',
    sendMessage: vi.fn(), t: (k) => k, targetMode: 'agent', teams: [], telegramReplyAllowed: false,
    viewMode: 'chat',
  });

  it('draws the divider between the two agents, and names each bubble', () => {
    const [conv] = applyHandoff(conversation(), 'c1', 'a1', 'a2', HANDOFF);
    const messages = conv.messages.map((m) => (m.id === 'a2' ? { ...m, content: 'Invoice 42 is corrected.' } : m));
    useChatPage.mockReturnValue(page(messages));
    render(<I18nProvider><ChatMessageList /></I18nProvider>);

    const divider = screen.getByTestId('handoff-divider');
    const first = screen.getByText('Let me pass you to Billing.');
    const second = screen.getByText('Invoice 42 is corrected.');
    // Document order: first reply, divider, second reply.
    expect(first.compareDocumentPosition(divider) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(divider.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('Front desk')).toBeInTheDocument();
    expect(screen.getAllByText('Billing').length).toBeGreaterThan(0);
  });

  it('draws no divider for a plain turn', () => {
    useChatPage.mockReturnValue(page(conversation()[0].messages));
    render(<I18nProvider><ChatMessageList /></I18nProvider>);
    expect(screen.queryByTestId('handoff-divider')).toBeNull();
  });
});

describe('the target switch', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('moves the selected agent to the one that answered once the turn is over', async () => {
    streamChat.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'meta', run_id: 'r1', agent_id: 'front' });
      onEvent(HANDOFF);
      onEvent({ type: 'meta', run_id: 'r2', agent_id: 'billing' });
      onEvent({ type: 'done', ok: true, response: 'Invoice 42 is corrected.', run_id: 'r2', agent_id: 'billing' });
    });
    let convs = [{ id: 'c1', agent_id: 'front', workspace: 'default', messages: [] }];
    const setConversations = vi.fn((fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; });
    const setSelectedAgent = vi.fn();
    const deps = {
      abortCtrlRef: { current: null }, clientId: 'tab', conversations: convs, currentConvId: 'c1',
      input: 'my invoice 42 is wrong', loadProcessData: vi.fn(), loading: false, mergeArtifact: vi.fn(),
      navigate: vi.fn(), pendingAttachments: [], pendingReferences: [], processOpen: false,
      selectCommand: vi.fn(), selectedAgent: 'front', selectedFlow: '', selectedProject: '',
      selectedTeam: '', selectedWorkspace: 'default', setActiveRunId: vi.fn(), setAttachmentError: vi.fn(),
      setConversations, setCurrentConvId: vi.fn(), setGraphRun: vi.fn(), setInput: vi.fn(),
      setLoading: vi.fn(), setPendingAttachments: vi.fn(), setPendingReferences: vi.fn(),
      setProcessInsights: vi.fn(), setSelectedAgent, setSessionId: vi.fn(), t: (k) => k,
      targetMode: 'agent', textareaRef: { current: null },
    };
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });

    expect(setSelectedAgent).toHaveBeenCalledWith('billing');
    expect(convs[0].agent_id).toBe('billing');
    const agentBubbles = convs[0].messages.filter((m) => m.role === 'agent');
    expect(agentBubbles.map((m) => m.agent_id)).toEqual(['front', 'billing']);
    expect(agentBubbles[1].content).toBe('Invoice 42 is corrected.');
  });

  it('leaves the selected agent alone when nobody handed over', async () => {
    streamChat.mockImplementation(async ({ onEvent }) => {
      onEvent({ type: 'meta', run_id: 'r1', agent_id: 'front' });
      onEvent({ type: 'done', ok: true, response: 'Sure.', run_id: 'r1', agent_id: 'front' });
    });
    const setSelectedAgent = vi.fn();
    let convs = [{ id: 'c1', agent_id: 'front', workspace: 'default', messages: [] }];
    const deps = {
      abortCtrlRef: { current: null }, clientId: 'tab', conversations: convs, currentConvId: 'c1',
      input: 'hello', loadProcessData: vi.fn(), loading: false, mergeArtifact: vi.fn(), navigate: vi.fn(),
      pendingAttachments: [], pendingReferences: [], processOpen: false, selectCommand: vi.fn(),
      selectedAgent: 'front', selectedFlow: '', selectedProject: '', selectedTeam: '',
      selectedWorkspace: 'default', setActiveRunId: vi.fn(), setAttachmentError: vi.fn(),
      setConversations: (fn) => { convs = typeof fn === 'function' ? fn(convs) : fn; },
      setCurrentConvId: vi.fn(), setGraphRun: vi.fn(), setInput: vi.fn(), setLoading: vi.fn(),
      setPendingAttachments: vi.fn(), setPendingReferences: vi.fn(), setProcessInsights: vi.fn(),
      setSelectedAgent, setSessionId: vi.fn(), t: (k) => k, targetMode: 'agent', textareaRef: { current: null },
    };
    const { result } = renderHook(() => useChatSend(deps));
    await act(async () => { await result.current.sendMessage(); });
    expect(setSelectedAgent).not.toHaveBeenCalled();
  });
});

describe('the live mirror', () => {
  it('keeps the handing reply and follows the receiving run', () => {
    let turn = reduceLiveTurn(null, { type: 'turn_start', message: 'my invoice', agent_id: 'front' });
    turn = reduceLiveTurn(turn, { type: 'meta', run_id: 'r1', agent_id: 'front' });
    turn = reduceLiveTurn(turn, { type: 'token', token: 'Let me pass' });
    turn = reduceLiveTurn(turn, HANDOFF);
    expect(turn).toMatchObject({ runId: 'r2', agentId: 'billing', text: '' });
    expect(turn.handoffs).toHaveLength(1);
    turn = reduceLiveTurn(turn, { type: 'meta', run_id: 'r2', agent_id: 'billing' });
    turn = reduceLiveTurn(turn, { type: 'token', token: 'Corrected' });
    expect(turn.text).toBe('Corrected');

    const bubbles = liveHandoffBubbles(turn.handoffs);
    expect(bubbles).toEqual([expect.objectContaining({
      role: 'agent', agent_id: 'front', content: 'Let me pass you to Billing.', run_id: 'r1',
    })]);
    expect(bubbles[0].handoff).toBeUndefined();
  });

  it('gives a chain\'s middle agent the handoff it received', () => {
    const second = { ...HANDOFF, from_agent_id: 'billing', to_agent_id: 'refunds', run_id: 'r2', next_run_id: 'r3' };
    const bubbles = liveHandoffBubbles([HANDOFF, second]);
    expect(bubbles[1].agent_id).toBe('billing');
    expect(bubbles[1].handoff.to_agent_id).toBe('billing');
  });
});
