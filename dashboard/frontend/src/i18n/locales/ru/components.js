export default {
  notifications: {
    title: 'Уведомления',
    tooltip: 'Уведомления',
    markReadFailed: 'Не удалось отметить прочитанным',
    markAllRead: 'Прочитать все',
    empty: 'Нет уведомлений',
    viewTask: 'открыть задачу →',
    viewAllInPlan: 'Все уведомления в Плане →',
    justNow: 'только что',
    minutesAgo: '{{count}} мин назад',
    hoursAgo: '{{count}} ч назад',
    daysAgo: '{{count}} дн назад',
  },
  inlineEdit: {
    clickToEdit: 'Нажмите, чтобы изменить',
    clickToAdd: 'Нажмите, чтобы добавить…',
  },
  runOrigin: {
    external: 'Внешний',
    imported: 'импорт',
    partial: 'неполный',
    externalHint: 'Сообщён подключением {{connection}}; хаб его не запускал, поэтому своего лога и живого потока у него нет.',
    importedHint: 'Запись собрана из OpenTelemetry-спанов уже после конца запуска, поэтому живого просмотра у него нет.',
    partialHint: 'Корневой спан так и не пришёл, поэтому вход, ответ и итог могут отсутствовать.',
  },
};
