import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EntityChat from '../EntityChat';
import ChatSessionList from '../ChatSessionList';

vi.mock('../../api', () => ({
  streamEntityChat: vi.fn(),
  getEntityChatSessions: vi.fn(),
  activateEntityChatSession: vi.fn(),
  deleteEntityChatSession: vi.fn(),
}));

import {
  activateEntityChatSession, deleteEntityChatSession, getEntityChatSessions,
} from '../../api';

const CHAT_REF = { kind: 'scenario', id: 's1' };

const SESSIONS = [
  { id: 's1', epoch: 1, active: true, title: 'start over', messages: 1, updated_at: null },
  { id: 's0', epoch: 0, active: false, title: 'cast a bard', messages: 4, updated_at: null },
];

const chatProps = (overrides = {}) => ({
  path: '/playground/scenarios/s1/chat',
  loadChat: vi.fn().mockResolvedValue({
    data: {
      messages: [{ role: 'user', content: 'start over' }],
      trace: [],
      chat_ref: CHAT_REF,
    },
  }),
  clearChat: vi.fn().mockResolvedValue({ data: { cleared: true } }),
  stopChat: vi.fn().mockResolvedValue({ data: {} }),
  title: 'Scenario',
  ...overrides,
});

/**
 * History is not a menu over the conversation: it takes the column the chat
 * lives in, and picking a thread turns the column back into a chat showing that
 * thread. These pin that exchange, because both halves of it are easy to get
 * wrong: a list that opens beside a live composer, or a pick that changes the
 * list without changing the conversation.
 */
describe('the session history in the chat column', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getEntityChatSessions.mockResolvedValue({
      data: { sessions: SESSIONS, has_history: true },
    });
  });

  it('stays out of the way until the chat has a history', async () => {
    getEntityChatSessions.mockResolvedValue({
      data: { sessions: [SESSIONS[0]], has_history: false },
    });
    render(<EntityChat {...chatProps()} />);

    await waitFor(() => expect(getEntityChatSessions).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /history/i })).toBeNull();
  });

  it('replaces the conversation, composer included', async () => {
    const user = userEvent.setup();
    render(<EntityChat {...chatProps()} />);

    const composer = await screen.findByPlaceholderText('entityChat.placeholder');
    expect(composer).toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: /history/i }));

    expect(screen.queryByPlaceholderText('entityChat.placeholder')).toBeNull();
    expect(screen.getByText('cast a bard')).toBeInTheDocument();
    expect(screen.getByText('entityChat.backToChat')).toBeInTheDocument();
  });

  it('opens the picked thread in the column', async () => {
    const user = userEvent.setup();
    activateEntityChatSession.mockResolvedValue({
      data: {
        messages: [{ role: 'user', content: 'cast a bard' }],
        trace: [{ k: 'user', text: 'cast a bard' }],
        session_epoch: 0,
        sessions: [{ ...SESSIONS[1], active: true }, { ...SESSIONS[0], active: false }],
        has_history: true,
      },
    });
    render(<EntityChat {...chatProps()} />);

    await user.click(await screen.findByRole('button', { name: /history/i }));
    await user.click(screen.getByText('cast a bard'));

    expect(activateEntityChatSession).toHaveBeenCalledWith(CHAT_REF, 's0');
    // Back to a chat, showing the thread that was picked.
    await waitFor(() => expect(
      screen.getByPlaceholderText('entityChat.placeholder'),
    ).toBeInTheDocument());
    expect(screen.getByText('cast a bard')).toBeInTheDocument();
  });

  it('keeps History reachable from inside an old thread', async () => {
    const user = userEvent.setup();
    // Opening the old thread drops the empty new chat it displaced, so one
    // thread is left. The way back to the list must survive that.
    activateEntityChatSession.mockResolvedValue({
      data: {
        messages: [{ role: 'user', content: 'cast a bard' }],
        trace: [],
        session_epoch: 0,
        sessions: [{ ...SESSIONS[1], active: true }],
        has_history: true,
      },
    });
    render(<EntityChat {...chatProps()} />);

    await user.click(await screen.findByRole('button', { name: /history/i }));
    await user.click(screen.getByText('cast a bard'));

    const toggle = await screen.findByRole('button', { name: /history/i });
    await user.click(toggle);
    expect(screen.getByText('entityChat.backToChat')).toBeInTheDocument();
  });

  it('treats the live thread as the way back', async () => {
    const user = userEvent.setup();
    render(<EntityChat {...chatProps()} />);

    await user.click(await screen.findByRole('button', { name: /history/i }));
    await user.click(screen.getByText('start over'));

    expect(activateEntityChatSession).not.toHaveBeenCalled();
    await waitFor(() => expect(
      screen.getByPlaceholderText('entityChat.placeholder'),
    ).toBeInTheDocument());
  });

  it('comes back unchanged from the back link', async () => {
    const user = userEvent.setup();
    render(<EntityChat {...chatProps()} />);

    await user.click(await screen.findByRole('button', { name: /history/i }));
    await user.click(screen.getByText('entityChat.backToChat'));

    expect(activateEntityChatSession).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText('entityChat.placeholder')).toBeInTheDocument();
  });

  it('drops an archived thread without opening it', async () => {
    const user = userEvent.setup();
    deleteEntityChatSession.mockResolvedValue({
      data: { sessions: [SESSIONS[0]], has_history: true },
    });
    render(<EntityChat {...chatProps()} />);

    await user.click(await screen.findByRole('button', { name: /history/i }));
    await user.click(screen.getByTitle('entityChat.deleteSession'));

    expect(deleteEntityChatSession).toHaveBeenCalledWith(CHAT_REF, 's0');
    expect(activateEntityChatSession).not.toHaveBeenCalled();
  });
});

describe('ChatSessionList rows', () => {
  it('marks the live thread and sizes each one', () => {
    render(
      <ChatSessionList
        sessions={SESSIONS} onPick={vi.fn()} onDelete={vi.fn()} onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('entityChat.currentChat ·')).toBeInTheDocument();
    // The live thread cannot be deleted, so only the archived one offers it.
    expect(screen.getAllByTitle('entityChat.deleteSession')).toHaveLength(1);
  });

  it('names a thread nobody wrote in', () => {
    render(
      <ChatSessionList
        sessions={[{ id: 's2', active: true, title: '', messages: 0 }]}
        onPick={vi.fn()} onDelete={vi.fn()} onClose={vi.fn()}
      />,
    );
    expect(screen.getByText('entityChat.untitledChat')).toBeInTheDocument();
  });
});
