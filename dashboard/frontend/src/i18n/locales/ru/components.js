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
    newNotification: 'Новое уведомление',
    minutesAgo: '{{count}} мин назад',
    hoursAgo: '{{count}} ч назад',
    daysAgo: '{{count}} дн назад',
    messages: {
      taskStarted: {
        title: 'Задача начата: {{title}}',
        body: 'Агент «{{agent}}» начал задачу.',
      },
      taskCompleted: {
        title: 'Задача выполнена: {{title}}',
        body: 'Агент «{{agent}}» выполнил задачу.',
      },
      taskFailed: {
        title: 'Задача не выполнена: {{title}}',
        body: 'Агент «{{agent}}» завершился с ошибкой: {{error}}',
        bodyNoError: 'Агент «{{agent}}» завершился с ошибкой.',
      },
      routingFailed: {
        title: 'Не удалось распределить: {{title}}',
        body: 'Оркестратор не смог распределить задачу: {{error}}',
        bodyNoError: 'Оркестратор не смог распределить задачу.',
      },
      approvalNeeded: {
        title: 'Нужно одобрение: {{title}}',
        body: 'Одобрите на странице задачи, чтобы агент «{{agent}}» начал работу.',
        bodyWithReason: 'Одобрите на странице задачи, чтобы агент «{{agent}}» начал работу. Почему этот агент: {{reason}}',
      },
      containerCompleted: {
        title: 'Задача выполнена: {{title}}',
        body: 'Все подзадачи выполнены ({{count}}).',
      },
      taskNeedsInput: {
        title: 'Задаче нужен ваш ответ',
        body: '{{question}}',
        bodyNoQuestion: 'Агент ждёт вашего ответа.',
      },
      reviewCycleLimit: {
        title: 'Задача упёрлась в лимит циклов проверки',
        body: 'Достигнут лимит циклов проверки ({{limit}}): задаче нужно ваше решение, а не ещё один автоматический круг исправления и проверки.',
      },
    },
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
  cronHint: {
    next: 'Далее: {{times}}',
    checking: 'Проверка…',
  },
};
