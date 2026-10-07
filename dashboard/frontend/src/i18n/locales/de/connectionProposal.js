// Eine Verbindung, die ein Agent im Chat vorschlägt (common/tool_approvals.py,
// components/chat/ConnectionProposalCard.jsx): die Freigabekarte für einen
// `propose_connection`-Aufruf und die Kachel „Von Agenten vorgeschlagen" auf
// der Connectors-Seite.
export default {
  title: 'Agent schlägt eine Verbindung vor',
  kind: {
    connector: 'Connector',
    channel: 'Chat-Kanal',
    mcp_server: 'MCP-Server',
    database: 'Datenbank',
    watcher: 'Watcher',
    secret: 'Geheimnis',
    provider: 'Anbieter-Schlüssel (für den ganzen Hub)',
  },
  warnings: 'Warnungen',
  replaces: 'Verbinden ersetzt die bereits eingerichtete Verbindung für {{title}}.',
  secretsHint: 'Secrets gehen direkt an den Hub, der Agent sieht sie nie.',
  secretKeepHint: 'Gespeichert, leer lassen, um es zu behalten.',
  listPlaceholder: 'Ein Eintrag pro Zeile',
  connect: 'Verbinden',
  openLink: 'Öffnen',
  panelTitle: 'Von Agenten vorgeschlagen',
};
