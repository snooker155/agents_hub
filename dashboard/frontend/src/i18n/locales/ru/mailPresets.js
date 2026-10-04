// Список почтовых провайдеров, общий для формы IMAP-наблюдателя и почтового
// канала (components/connectors/MailPresetPicker.jsx, connectors/mail/presets.py).
export default {
  label: 'Почтовый сервис',
  custom: 'Другой сервер (хосты вписать вручную)',
  help: 'Как создать',
  auth: {
    app_password: '{{provider}} не принимает обычный пароль аккаунта по IMAP. Создайте пароль приложения в настройках безопасности аккаунта (нужна включённая двухэтапная проверка) и укажите его как пароль.',
    password: '{{provider}} принимает обычный пароль аккаунта. Проверьте, что доступ по IMAP включён в настройках ящика.',
    oauth: '{{provider}} отключил вход по паролю для IMAP и SMTP, поэтому пароль, скорее всего, будет отклонён. Хосты заполнены для тенантов, где это ещё разрешено; иначе подключите аккаунт через коннектор Microsoft.',
  },
  useGoogle: 'Или войдите подключённым Google-аккаунтом.',
  // GmailSignInNote.jsx: whether the Google connector can sign in to Gmail.
  google: {
    unknown: 'Не удалось проверить подключение Google; карточка статуса покажет, работает ли вход.',
    ready: 'Вход в Gmail как {{email}}.',
    noGmail: 'Google подключён как {{email}}, но без доступа к Gmail.',
    notConnected: 'Google не подключён.',
    open: 'Откройте вкладку Google и нажмите «Подключить с Gmail»',
  },
};
