import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// A new chat's title is named by its agent once the first answer is in, by
// the tab whose turn ended, and only once.

const suggestChatTitle = vi.fn();
vi.mock('../../../api', () => ({ suggestChatTitle: (...a) => suggestChatTitle(...a) }));

import { namingRequest, useChatTitles } from '../useChatTitles';

const CONV = {
  id: 'c1', title: 'how do I put the hub on my ubuntu box?', agent_id: 'writer',
  messages: [
    { role: 'user', content: 'how do I put the hub on my ubuntu box?' },
    { role: 'agent', content: 'First install Docker...' },
  ],
};

describe('namingRequest', () => {
  it('asks with the first message, the first answer and the agent', () => {
    expect(namingRequest(CONV)).toEqual({
      message: 'how do I put the hub on my ubuntu box?', reply: 'First install Docker...', agentId: 'writer',
    });
  });

  it('asks nothing of a named chat, one with no answer yet, or a connector or Assistant thread', () => {
    expect(namingRequest({ ...CONV, title_named: true })).toBeNull();
    expect(namingRequest({ ...CONV, messages: CONV.messages.slice(0, 1) })).toBeNull();
    expect(namingRequest({ ...CONV, messages: [CONV.messages[0], { role: 'agent', content: 'x', error: 'boom' }] })).toBeNull();
    expect(namingRequest({ ...CONV, origin: 'telegram' })).toBeNull();
    expect(namingRequest({ ...CONV, origin: 'assistant' })).toBeNull();
  });
});

describe('useChatTitles', () => {
  it('names the chat when its turn ends here, once', async () => {
    suggestChatTitle.mockResolvedValue({ data: { title: 'Hub on Ubuntu' } });
    let convs = [CONV];
    const setConversations = vi.fn((fn) => { convs = fn(convs); });
    const { rerender } = renderHook((props) => useChatTitles(props), {
      initialProps: { turns: { c1: {} }, conversations: convs, setConversations },
    });
    expect(suggestChatTitle).not.toHaveBeenCalled();
    rerender({ turns: {}, conversations: convs, setConversations });
    await waitFor(() => expect(convs[0]).toMatchObject({ title: 'Hub on Ubuntu', title_named: true }));
    expect(suggestChatTitle).toHaveBeenCalledWith({ message: CONV.messages[0].content, reply: 'First install Docker...', agentId: 'writer' });
    // Another turn of the same chat asks nothing more.
    rerender({ turns: { c1: {} }, conversations: convs, setConversations });
    rerender({ turns: {}, conversations: convs, setConversations });
    expect(suggestChatTitle).toHaveBeenCalledTimes(1);
  });

  it('keeps the first-message title when no title comes', async () => {
    suggestChatTitle.mockReset().mockRejectedValue(new Error('down'));
    let convs = [CONV];
    const setConversations = vi.fn((fn) => { convs = fn(convs); });
    const { rerender } = renderHook((props) => useChatTitles(props), {
      initialProps: { turns: { c1: {} }, conversations: convs, setConversations },
    });
    rerender({ turns: {}, conversations: convs, setConversations });
    await waitFor(() => expect(convs[0].title_named).toBe(true));
    expect(convs[0].title).toBe(CONV.title);
  });
});
