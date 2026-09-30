import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../api', () => ({ streamChat: vi.fn() }));
vi.mock('../context', async () => {
  const { createContext } = await import('react');
  return { useChatPage: vi.fn(), ChatPageContext: createContext(null) };
});

import { I18nProvider } from '../../../i18n';
import ChatMessageList from '../ChatMessageList';
import { useChatPage } from '../context';

/**
 * The transcript is laid out as turns so a prompt can stay at the top of the
 * scroller for its reply by CSS alone (useChatScroll): each prompt opens a
 * `[data-turn]` block and sits in a sticky `[data-turn-prompt]` slot, followed
 * by the spacer that keeps the replies still when a held prompt folds. Nothing
 * is duplicated: the prompt is rendered once, in place.
 */
const page = (messages, extra = {}) => ({
  agentName: 'Front desk', agents: [{ id: 'front', name: 'Front desk' }],
  artifacts: {}, currentTelegramBinding: null, flows: [], jumpToArtifact: vi.fn(), liveMessages: [],
  liveTurn: null, loading: false, messages, renderedMessages: messages,
  runTimelineByRunId: {}, selectedFlow: '', selectedTeam: '', selectedWorkspace: 'default',
  sendMessage: vi.fn(), t: (k) => k, targetMode: 'agent', teams: [], telegramReplyAllowed: false,
  viewMode: 'chat', ...extra,
});

const messages = [
  { id: 's0', role: 'system', content: 'Earlier turns were summarised.' },
  { id: 'u1', role: 'user', content: 'first question' },
  { id: 'a1', role: 'agent', agent_id: 'front', content: 'first answer' },
  { id: 'u1s', role: 'user', content: 'and be brief', steer: { status: 'delivered', step: 2 } },
  { id: 'u2', role: 'user', content: 'second question' },
  { id: 'a2', role: 'agent', agent_id: 'front', content: 'second answer' },
];

describe.each(['chat', 'build'])('the transcript as turns (%s view)', (viewMode) => {
  it('puts each prompt once, in a sticky slot at the top of its own turn', () => {
    useChatPage.mockReturnValue(page(messages, { viewMode }));
    const { container } = render(<I18nProvider><ChatMessageList /></I18nProvider>);

    const turns = [...container.querySelectorAll('[data-turn]')];
    expect(turns.map((el) => el.getAttribute('data-turn'))).toEqual(['u1', 'u2']);

    const slots = [...container.querySelectorAll('[data-turn-prompt]')];
    expect(slots.map((el) => el.getAttribute('data-turn-prompt'))).toEqual(['u1', 'u2']);
    slots.forEach((slot, i) => {
      expect(slot.parentElement).toBe(turns[i]);
      expect(slot).toBe(turns[i].firstElementChild);
      expect(slot.className).toContain('sticky');
      expect(slot.nextElementSibling.hasAttribute('data-turn-spacer')).toBe(true);
      expect(slot.querySelector('[data-prompt-bubble]')).not.toBeNull();
    });

    // The prompt text appears once: no copy over the transcript.
    expect(container.querySelectorAll('[data-prompt-bubble]').length).toBe(3);
    expect([...container.querySelectorAll('*')].filter((el) => el.textContent === 'first question' && el.children.length === 0).length).toBe(1);

    // A steer sent mid turn stays in the turn it steered, not in a slot.
    const steer = [...container.querySelectorAll('[data-prompt-bubble]')].find((el) => el.textContent.includes('and be brief'));
    expect(steer.closest('[data-turn]').getAttribute('data-turn')).toBe('u1');
    expect(steer.closest('[data-turn-prompt]')).toBeNull();

    // Replies belong to the turn of the prompt they answer.
    const second = [...container.querySelectorAll('*')].find((el) => el.textContent === 'second answer' && el.children.length === 0);
    expect(second.closest('[data-turn]').getAttribute('data-turn')).toBe('u2');

    // What comes before the first prompt is outside any turn with a slot.
    const notice = [...container.querySelectorAll('*')].find((el) => el.textContent === 'Earlier turns were summarised.' && el.children.length === 0);
    expect(notice.closest('[data-turn]')).toBeNull();
  });
});
