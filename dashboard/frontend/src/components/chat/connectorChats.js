/**
 * The chats the connectors keep (Telegram and the channels: Slack, Discord,
 * Teams, mail), as the Chat page lists them at the foot of its conversation
 * list. Every connector's binding comes back in one shape, so the page opens,
 * reads and answers any of them the same way:
 *
 *   { connector, chat_key, conversation_id, workspace, title, agent_id, agent_name, ... }
 *
 * `chat_key` is the connector's own id of the chat (Telegram's chat_id). A
 * connector's thread is a conversation the server keeps (connectors/channels/
 * turns.py, connectors/telegram/telegram_runner.py), never one of the stored
 * chats, so the page rebuilds its messages from the runs (useChatTelegram.js).
 */
import { Hash, Mail, MessageCircle, Send, Users } from 'lucide-react';
import { getChannelBindings, getTelegramBindings, sendChannelMessage, sendTelegramMessage } from '../../api';

// In the order the list shows them, with the Connectors page's icons
// (pages/Connectors.jsx); a label is `connectors.tabs.<id>`.
export const CHAT_CONNECTORS = [
  { id: 'telegram', icon: Send },
  { id: 'slack', icon: Hash },
  { id: 'discord', icon: MessageCircle },
  { id: 'teams', icon: Users },
  { id: 'mail', icon: Mail },
];

export const CONNECTOR_ORIGINS = new Set(CHAT_CONNECTORS.map((c) => c.id));

export const connectorMeta = (id) => CHAT_CONNECTORS.find((c) => c.id === id) || { id, icon: MessageCircle };

const asList = (data) => (Array.isArray(data) ? data : []);

/** Every connector's bindings in one list; a connector that fails to answer
 * (not set up, not installed on this hub) adds none. */
export async function loadConnectorBindings() {
  const answers = await Promise.all(CHAT_CONNECTORS.map(async ({ id }) => {
    try {
      const { data } = id === 'telegram' ? await getTelegramBindings() : await getChannelBindings(id);
      return asList(data).map((b) => ({
        ...b,
        connector: id,
        chat_key: String(id === 'telegram' ? b.chat_id : b.chat_key),
      }));
    } catch {
      return [];
    }
  }));
  return answers.flat();
}

/** Send `text` into the connector's chat as its bot. */
export function sendAsConnector(binding, text) {
  if (binding.connector === 'telegram') return sendTelegramMessage(binding.chat_id ?? binding.chat_key, text);
  return sendChannelMessage(binding.connector, binding.chat_key, text, binding.workspace || undefined);
}

/** The bindings grouped by connector, in CHAT_CONNECTORS order, empty ones left out. */
export function groupByConnector(bindings) {
  return CHAT_CONNECTORS
    .map((c) => ({ ...c, chats: bindings.filter((b) => b.connector === c.id) }))
    .filter((g) => g.chats.length > 0);
}
