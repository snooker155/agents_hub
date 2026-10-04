// A connection an agent proposes from the chat (common/tool_approvals.py,
// components/chat/ConnectionProposalCard.jsx): the approval card for a
// `propose_connection` call, and the "Proposed by agents" panel on Connectors.
export default {
  title: 'Agent proposes a connection',
  kind: {
    connector: 'Connector',
    channel: 'Chat channel',
    mcp_server: 'MCP server',
    database: 'Database',
    watcher: 'Watcher',
    secret: 'Secret',
  },
  warnings: 'Warnings',
  replaces: 'Connect overwrites the connection already configured for {{title}}.',
  secretsHint: 'Secrets go straight to the hub, the agent never sees them.',
  secretKeepHint: 'Stored, leave empty to keep.',
  listPlaceholder: 'One item per line',
  connect: 'Connect',
  openLink: 'Open',
  panelTitle: 'Proposed by agents',
};
