// Вызов инструмента, который ждёт решения человека внутри хода чата
// (common/tool_approvals.py, components/chat/ToolApprovalCard.jsx).
export default {
  title: 'Нужно одобрение',
  status: {
    pending: 'Ждёт вашего ответа',
    approved: 'Одобрено',
    denied: 'Отклонено',
    expired: 'Никто не ответил вовремя',
    cancelled: 'Запуск остановлен',
  },
  by: {
    hook: 'Хук рабочего пространства',
    policy: 'Политика инструментов',
    auto: 'Классификатор политики',
    guardrail: 'Ограничитель',
    auto_mode: 'Авторежим',
  },
  noteLabel: 'Заметка для агента',
  notePlaceholder: 'Заметка для агента (необязательно)',
  approve: 'Одобрить',
  deny: 'Отклонить',
  waitsUntil: 'Ждёт до {{time}}, потом вызов будет отклонён.',
  waiting: 'Ждёт вашего одобрения для {{tool}}',
  inRun: '{{agent}} ждёт одобрения, работа запуска на паузе.',
  openRun: 'Открыть запуск',
  errors: {
    forbidden: 'Ответить может только владелец запуска или администратор.',
    gone: 'Этот вызов уже не ждёт ответа.',
    failed: 'Не удалось отправить ответ. Попробуйте ещё раз.',
  },
  // The connection proposal card (chat/ConnectionProposalCard.jsx) still says who answered and the note.
  decidedBy: 'Ответ: {{name}}',
  noteShown: 'Заметка: {{note}}',
};
