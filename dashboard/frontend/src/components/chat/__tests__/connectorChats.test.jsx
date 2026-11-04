import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// The connectors' chats: every connector's bindings in one shape, and listed
// by connector at the foot of the chat list, a click opening one connector.

const getTelegramBindings = vi.fn();
const getChannelBindings = vi.fn();
const sendTelegramMessage = vi.fn();
const sendChannelMessage = vi.fn();
vi.mock('../../../api', () => ({
  getTelegramBindings: (...a) => getTelegramBindings(...a),
  getChannelBindings: (...a) => getChannelBindings(...a),
  sendTelegramMessage: (...a) => sendTelegramMessage(...a),
  sendChannelMessage: (...a) => sendChannelMessage(...a),
  searchChats: vi.fn(),
}));

import { groupByConnector, loadConnectorBindings, sendAsConnector } from '../connectorChats';
import ChatSidebar from '../ChatSidebar';
import { ChatPageContext } from '../context';

describe('loadConnectorBindings', () => {
  it('reads Telegram and every channel into one shape, skipping a connector that fails', async () => {
    getTelegramBindings.mockResolvedValue({ data: [{ chat_id: 42, conversation_id: 'c-tg', title: 'Family' }] });
    getChannelBindings.mockImplementation((name) => (name === 'slack'
      ? Promise.resolve({ data: [{ chat_key: 'C01', conversation_id: 'c-sl', title: '#general' }] })
      : Promise.reject(new Error('not set up'))));
    const rows = await loadConnectorBindings();
    expect(rows).toEqual([
      expect.objectContaining({ connector: 'telegram', chat_key: '42', conversation_id: 'c-tg' }),
      expect.objectContaining({ connector: 'slack', chat_key: 'C01', conversation_id: 'c-sl' }),
    ]);
  });

  it('answers as the bot through the connector the chat belongs to', () => {
    sendAsConnector({ connector: 'telegram', chat_id: 42, chat_key: '42' }, 'hi');
    expect(sendTelegramMessage).toHaveBeenCalledWith(42, 'hi');
    sendAsConnector({ connector: 'slack', chat_key: 'C01', workspace: 'ws' }, 'hi');
    expect(sendChannelMessage).toHaveBeenCalledWith('slack', 'C01', 'hi', 'ws');
  });

  it('groups by connector in a fixed order and leaves out empty ones', () => {
    const groups = groupByConnector([{ connector: 'slack' }, { connector: 'telegram' }, { connector: 'slack' }]);
    expect(groups.map((g) => [g.id, g.chats.length])).toEqual([['telegram', 1], ['slack', 2]]);
  });
});

describe('ChatSidebar connectors', () => {
  const BINDINGS = [
    { connector: 'telegram', chat_key: '42', conversation_id: 'c-tg', title: 'Family', agent_id: 'a', workspace: 'ws' },
    { connector: 'slack', chat_key: 'C01', conversation_id: 'c-sl', title: '#general', agent_id: 'a', workspace: 'ws' },
    { connector: 'slack', chat_key: 'C02', conversation_id: 'c-sl2', title: '#random', agent_id: 'a', workspace: 'ws' },
  ];
  const draw = (extra = {}) => {
    const page = {
      currentConvId: null, deleteConversation: vi.fn(), flows: [], listOpen: false, navigate: vi.fn(),
      newConversation: vi.fn(), projects: [], setListOpen: vi.fn(), selectableAgents: [],
      selectedWorkspace: 'ws', setConversations: vi.fn(), setSelectedAgent: vi.fn(),
      setSelectedFlow: vi.fn(), setSelectedProject: vi.fn(), setSelectedTeam: vi.fn(),
      setTargetMode: vi.fn(), syncError: false, t: (k) => k, teams: [],
      visibleConversations: [], visibleConnectorBindings: BINDINGS, ...extra,
    };
    render(<ChatPageContext.Provider value={page}><ChatSidebar /></ChatPageContext.Provider>);
    return page;
  };

  it('is an accordion: a connector opens in place of the service chats, which keep their row', () => {
    const page = draw({ visibleConversations: [{ id: 'x', title: 'Plan the release' }] });
    // The service's chats open first, without a row of their own; the connectors down to their rows.
    expect(screen.queryByTestId('chats-section')).toBeNull();
    expect(screen.getByText('Plan the release')).toBeInTheDocument();
    expect(screen.getByTestId('connector-telegram')).toHaveTextContent('1');
    expect(screen.getByTestId('connector-slack')).toHaveTextContent('2');
    expect(screen.queryByTestId('connector-slack-chats')).toBeNull();

    fireEvent.click(screen.getByTestId('connector-slack'));
    expect(screen.getByTestId('connector-slack-chats')).toHaveTextContent('#general');
    expect(screen.queryByText('Plan the release')).toBeNull();
    expect(screen.getByTestId('chats-section')).toHaveAttribute('aria-expanded', 'false');

    // One section at a time.
    fireEvent.click(screen.getByTestId('connector-telegram'));
    expect(screen.queryByTestId('connector-slack-chats')).toBeNull();
    fireEvent.click(screen.getByText('Family'));
    expect(page.navigate).toHaveBeenCalledWith('/chat/c-tg');

    // The open section's row, or the chats' own, brings the service's chats back.
    fireEvent.click(screen.getByTestId('connector-telegram'));
    expect(screen.getByText('Plan the release')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('connector-slack'));
    fireEvent.click(screen.getByTestId('chats-section'));
    expect(screen.getByText('Plan the release')).toBeInTheDocument();
    expect(screen.queryByTestId('chats-section')).toBeNull();
  });

  it('has no section rows without connector chats', () => {
    draw({ visibleConnectorBindings: [], visibleConversations: [{ id: 'x', title: 'Plan the release' }] });
    expect(screen.queryByTestId('chats-section')).toBeNull();
    expect(screen.getByText('Plan the release')).toBeInTheDocument();
  });

  it('starts with the open chat\'s connector open', () => {
    draw({ currentConvId: 'c-sl2' });
    expect(screen.getByTestId('connector-slack-chats')).toHaveTextContent('#random');
    expect(screen.getByTestId('connector-slack')).toHaveAttribute('aria-expanded', 'true');
  });
});
