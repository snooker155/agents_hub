import { describe, it, expect, vi } from 'vitest';
import { buildCompactionNotice } from '../turnState';
import { handleAgentEvent } from '../send/handleAgentResponse';

const t = (key, vars) => (key === 'chat.compactionNotice' ? `Earlier messages were folded into a summary (${vars.count} turns).` : key);

describe('buildCompactionNotice', () => {
  it('builds a system-role notice with the folded-turn count', () => {
    const notice = buildCompactionNotice(t, { type: 'compaction', folded: 12, summary_chars: 900 });
    expect(notice.role).toBe('system');
    expect(notice.kind).toBe('compaction');
    expect(notice.content).toBe('Earlier messages were folded into a summary (12 turns).');
    expect(typeof notice.id).toBe('string');
  });

  it('defaults the count to 0 when the event carries none', () => {
    const notice = buildCompactionNotice(t, { type: 'compaction' });
    expect(notice.content).toBe('Earlier messages were folded into a summary (0 turns).');
  });
});

// Feeding a `compaction` stream event through the same handler `useChatSend`
// wires up for every other event type: it should insert one system-role
// message into the conversation, without disturbing the rest.
describe('handleAgentEvent: compaction', () => {
  function makeCtx(overrides = {}) {
    let conversations = [{ id: 'c1', messages: [{ id: 'assistant-1', role: 'agent', content: '' }] }];
    const setConversations = vi.fn((updater) => { conversations = updater(conversations); });
    const ctx = {
      convId: 'c1',
      setConversations,
      setActiveRunId: vi.fn(),
      setSessionId: vi.fn(),
      setGraphRun: vi.fn(),
      setProcessInsights: vi.fn(),
      mergeArtifact: vi.fn(),
      processOpen: false,
      isMultiAgent: false,
      isFlowMode: false,
      assistantId: 'assistant-1',
      userMsgText: 'hi',
      t,
      state: { runId: null, finalPayload: null, currentNodeId: null, nodeMsgIds: {} },
      ...overrides,
    };
    return { ctx, getConversations: () => conversations };
  }

  it('inserts the notice right before the already-created assistant bubble (agent mode)', () => {
    const { ctx, getConversations } = makeCtx();
    handleAgentEvent({ type: 'compaction', folded: 5 }, ctx);
    const messages = getConversations()[0].messages;
    expect(messages).toHaveLength(2);
    expect(messages[0].role).toBe('system');
    expect(messages[0].kind).toBe('compaction');
    expect(messages[0].content).toContain('5 turns');
    expect(messages[1].id).toBe('assistant-1');
  });

  it('appends the notice when there is no bubble yet (flow/team mode)', () => {
    const { ctx } = makeCtx({ assistantId: null, isMultiAgent: true, isFlowMode: true });
    // Simulate flow mode: no bubble has been created for this turn yet.
    const conversations = [{ id: 'c1', messages: [] }];
    ctx.setConversations = vi.fn((updater) => { conversations[0] = updater(conversations)[0]; });
    handleAgentEvent({ type: 'compaction', folded: 3 }, ctx);
    expect(conversations[0].messages).toHaveLength(1);
    expect(conversations[0].messages[0].role).toBe('system');
  });
});
