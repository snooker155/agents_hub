import { describe, it, expect } from 'vitest';
import { buildNewConversation, appendUserMessage, buildAssistantBubble } from '../send/optimistic';

const t = (key) => (key === 'chat.newChat' ? 'New chat' : key);

describe('buildNewConversation', () => {
  it('locks the conversation to the agent target and current workspace', () => {
    const conv = buildNewConversation({
      convId: 'c1', text: 'Plan the launch', attachmentLine: '', t,
      targetMode: 'agent', isFlowMode: false, isTeamMode: false,
      selectedAgent: 'agent-1', selectedFlow: 'flow-1', selectedTeam: 'team-1',
      selectedWorkspace: 'ws1', selectedProject: 'proj-1',
    });
    expect(conv).toMatchObject({
      id: 'c1',
      title: 'Plan the launch',
      agent_id: 'agent-1',
      flow_id: null,
      team_id: null,
      target_mode: 'agent',
      workspace: 'ws1',
      project_id: 'proj-1',
      messages: [],
    });
  });

  it('locks to the flow target in flow mode', () => {
    const conv = buildNewConversation({
      convId: 'c1', text: '', attachmentLine: '', t,
      targetMode: 'flow', isFlowMode: true, isTeamMode: false,
      selectedAgent: 'agent-1', selectedFlow: 'flow-1', selectedTeam: 'team-1',
      selectedWorkspace: 'ws1', selectedProject: null,
    });
    expect(conv.agent_id).toBeNull();
    expect(conv.flow_id).toBe('flow-1');
    expect(conv.team_id).toBeNull();
    // No text and no attachment line: falls back to the "new chat" wording.
    expect(conv.title).toBe('New chat');
  });
});

describe('appendUserMessage', () => {
  const userMsg = { id: 'm1', role: 'user', content: 'hi' };

  it('appends the message and titles a brand new conversation from it', () => {
    const conversations = [{ id: 'c1', title: 'New chat', messages: [] }];
    const out = appendUserMessage(conversations, 'c1', userMsg, 'hi', '');
    expect(out[0].messages).toEqual([userMsg]);
    expect(out[0].title).toBe('hi');
  });

  it('leaves an existing title alone once the conversation already has messages', () => {
    const conversations = [{ id: 'c1', title: 'Earlier title', messages: [{ id: 'm0', role: 'user', content: 'first' }] }];
    const out = appendUserMessage(conversations, 'c1', userMsg, 'hi', '');
    expect(out[0].title).toBe('Earlier title');
    expect(out[0].messages).toHaveLength(2);
  });

  it('does not touch other conversations', () => {
    const conversations = [{ id: 'other', title: 'Other', messages: [] }, { id: 'c1', title: '', messages: [] }];
    const out = appendUserMessage(conversations, 'c1', userMsg, 'hi', '');
    expect(out[0]).toBe(conversations[0]);
  });
});

describe('buildAssistantBubble', () => {
  it('creates an empty, non-error bubble for the given agent', () => {
    const bubble = buildAssistantBubble('a1', 'agent-1');
    expect(bubble).toEqual({
      id: 'a1', role: 'agent', agent_id: 'agent-1', content: '', error: false,
      run_id: null, inbound_tokens: null, outbound_tokens: null, total_tokens: null,
      tool_calls: null, duration_ms: null,
    });
  });
});
