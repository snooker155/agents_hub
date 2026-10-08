// Подключение, которое агент предлагает из чата (common/tool_approvals.py,
// components/chat/ConnectionProposalCard.jsx): карточка одобрения для вызова
// `propose_connection` и панель «Предложения агентов» на странице Connectors.
export default {
  title: 'Агент предлагает подключение',
  kind: {
    connector: 'Коннектор',
    channel: 'Чат-канал',
    mcp_server: 'MCP-сервер',
    database: 'База данных',
    watcher: 'Вотчер',
    secret: 'Секрет',
    provider: 'Ключ провайдера (на весь хаб)',
  },
  warnings: 'Предупреждения',
  replaces: 'Подключение заменит уже настроенное соединение для {{title}}.',
  secretsHint: 'Секреты уходят прямо в хаб, агент их не видит.',
  secretKeepHint: 'Сохранено, оставьте поле пустым, чтобы оставить текущее значение.',
  listPlaceholder: 'По одному элементу на строку',
  connect: 'Подключить',
  openLink: 'Открыть',
  panelTitle: 'Предложения агентов',
};
