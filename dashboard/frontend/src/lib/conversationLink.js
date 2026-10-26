/**
 * Where a run's conversation opens, from its id (the run page's
 * "Conversation ID").
 *
 * An entity build chat is `<kind>chat:<entity id>[:<epoch>]`
 * (chat/entity_chat.py) and lives on its entity's page; a flow chat is
 * `flow-chat-<flow id>[-<nonce>]` (components/flow/FlowChat.jsx) and lives on
 * the flow's page; any other id is a Chat conversation. A kind with no page
 * here gets no link.
 */
const enc = encodeURIComponent;

const ENTITY_CHATS = {
  view: (id) => `/views/${enc(id)}`,
  loop: (id) => `/loops/${enc(id)}`,
  team: (id) => `/teams/${enc(id)}`,
  agentdef: (id) => `/agents/${enc(id)}`,
  projects: () => '/projects',
  memory: () => '/memory',
};

export function conversationLink(conversationId, run = null) {
  const id = String(conversationId || '').trim();
  if (!id) return null;
  const entity = id.match(/^([a-z]+)chat:([^:]+)/);
  if (entity) {
    const page = ENTITY_CHATS[entity[1]];
    return page ? page(entity[2]) : null;
  }
  if (id.startsWith('flow-chat-')) {
    return run?.flow_id ? `/flows/${enc(run.flow_id)}` : null;
  }
  return `/chat/${enc(id)}`;
}
