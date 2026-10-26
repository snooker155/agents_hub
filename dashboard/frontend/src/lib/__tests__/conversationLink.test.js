import { describe, it, expect } from 'vitest';
import { conversationLink } from '../conversationLink';

describe('conversationLink', () => {
  it('opens a Chat conversation in Chat', () => {
    expect(conversationLink('5b1f0c2e-1111-2222-3333-444455556666'))
      .toBe('/chat/5b1f0c2e-1111-2222-3333-444455556666');
  });

  it('opens an entity build chat on its entity page', () => {
    expect(conversationLink('viewchat:vw_2e6959f3c8ec4629')).toBe('/views/vw_2e6959f3c8ec4629');
    expect(conversationLink('viewchat:vw_1:3')).toBe('/views/vw_1');
    expect(conversationLink('loopchat:lp1')).toBe('/loops/lp1');
    expect(conversationLink('teamchat:t1')).toBe('/teams/t1');
    expect(conversationLink('agentdefchat:main-agent')).toBe('/agents/main-agent');
    expect(conversationLink('projectschat:default')).toBe('/projects');
    expect(conversationLink('memorychat:m1')).toBe('/memory');
  });

  it('opens a flow chat on its flow', () => {
    expect(conversationLink('flow-chat-f1-123', { flow_id: 'f1' })).toBe('/flows/f1');
    expect(conversationLink('flow-chat-f1-123')).toBeNull();
  });

  it('gives no link for nothing or an unknown chat kind', () => {
    expect(conversationLink('')).toBeNull();
    expect(conversationLink(null)).toBeNull();
    expect(conversationLink('oddchat:x')).toBeNull();
  });
});
