export default {
  title: 'Коннекторы',
  description: 'Сервисы, к которым хаб обращается сам. Обратное направление к Подключениям, где ваше работает в другом месте и отчитывается сюда.',
  workspaceBanner: {
    title: 'Настройка коннекторов для рабочего пространства {{workspace}}.',
    rule: 'Коннекторы пространства по умолчанию работают везде. Коннекторы, заданные здесь, работают только здесь.',
  },
  source: {
    here: 'Задан в этом пространстве',
    default: 'Из пространства по умолчанию',
    defineForWorkspace: 'Задать для этого пространства',
    removeUseDefault: 'Удалить, использовать из пространства по умолчанию',
    confirmRemove: 'Удалить собственное определение этого пространства? Будет использовано определение по умолчанию.',
    alsoDefinedIn: 'Также задан в: {{list}}',
  },
  tabs: {
    git: 'GitHub и GitLab',
    alerts: 'Правила алертов',
    trackers: "Jira и Linear",
  },
  alerts: {
    intro: 'Поднимайте уведомление в колокольчике самостоятельно, когда прогон завершается сбоем или расходы превышают порог, без участия агента или человека.',
    rules: 'Правила алертов',
    rulesHint: 'Когда рабочее пространство должно поднять уведомление само: прогон завершился сбоем или расходы превысили порог.',
    noRules: 'Пока нет правил алертов.',
    ruleKind: 'Правило',
    ruleKinds: {
      run_failed: 'Прогон завершился сбоем',
      spend_daily_over: 'Расходы за день выше',
      spend_run_over: 'Стоимость прогона выше',
    },
    threshold: 'Порог, USD',
    agentFilter: 'Фильтр по агенту',
    agentFilterPlaceholder: 'Любой агент',
    enabled: 'Включено',
    addRule: 'Добавить правило',
    confirmRemoveRule: 'Удалить это правило?',
  },
  clearSecrets: "Очистить секреты",
  configured: "настроен",
  notConfigured: "не настроен",
  channels: {
    loadFailed: "Не удалось загрузить канал",
  },
  fields: {
    n8n: {
      base_url: "Адрес",
      api_key: "API ключ",
      webhook_header_name: "Заголовок авторизации вебхука",
      webhook_header_value: "Значение заголовка",
    },
    slack: {
      bot_token: "Bot token (xoxb)",
      app_token: "App token (xapp)",
      signing_secret: "Signing secret",
      mode: "Транспорт",
      client_id: "OAuth client ID",
      client_secret: "OAuth client secret",
      distribution: "Распространение",
      app_name: "Название приложения",
    },
    discord: {
      bot_token: "Токен бота",
      application_id: "Идентификатор приложения",
    },
    teams: {
      app_id: "Microsoft App ID",
      app_password: "Пароль приложения",
      tenant_id: "Идентификатор тенанта",
      distribution: "Распространение",
      app_name: "Название приложения",
      developer_name: "Издатель",
      website_url: "Сайт",
      privacy_url: "Адрес политики конфиденциальности",
      terms_url: "Адрес условий использования",
      app_version: "Версия приложения",
    },
    mail: {
      auth_mode: "Вход",
      imap_host: "IMAP хост",
      imap_port: "IMAP порт",
      imap_user: "IMAP пользователь",
      imap_password: "IMAP пароль",
      imap_folder: "Папка",
      imap_ssl: "IMAP через SSL",
      smtp_host: "SMTP хост",
      smtp_port: "SMTP порт",
      smtp_user: "SMTP пользователь",
      smtp_password: "SMTP пароль",
      smtp_security: "Защита SMTP",
      from_address: "Адрес отправителя",
      poll_seconds: "Опрос каждые, секунд",
      subject_prefix: "Префикс темы ответа",
    },
    jira: {
      base_url: "URL сайта",
      email: "Email аккаунта",
      api_token: "API токен",
    },
    linear: {
      api_key: "API ключ",
    },
    microsoft: {
      tenant_id: "Идентификатор тенанта",
      client_id: "Client id",
      client_secret: "Client secret",
      default_user: "Ящик по умолчанию",
      consent_tenant: "Вход конечных пользователей",
    },
    google: {
      client_id: "OAuth client id",
      client_secret: "OAuth client secret",
      service_account_json: "Ключ сервисного аккаунта (JSON)",
    },
    notion: {
      api_token: "Токен интеграции",
    },
    confluence: {
      base_url: "URL сайта",
      email: "Email аккаунта",
      api_token: "API токен",
    },
  },
  fieldHints: {
    n8n: {
      base_url: "Адрес вашего n8n, например http://localhost:5678 или https://n8n.example.com.",
      api_key: "Создаётся в n8n в Settings, n8n API. Без него потоки всё равно запускают воркфлоу по пути вебхука, но список воркфлоу и импорт из инстанса недоступны.",
      webhook_header_name: "Только если триггер Webhook использует Header Auth: имя заголовка, которого он ждёт.",
    },
    slack: {
      mode: "socket держит открытый websocket (публичный URL не нужен); events ждёт, что Slack вызовет входящий URL.",
      distribution: "Закрытое: только установки, которые вы начинаете на странице «Дистрибуция»; открытое: ставить может кто угодно, а вы подтверждаете каждую.",
      client_id: "Из раздела Basic Information приложения Slack. Нужен для кнопки «Добавить в Slack».",
    },
    teams: {
      tenant_id: "Только для однотенантных ботов.",
      distribution: "Закрытое: только добавленные вами организации; открытое: установить может любая организация, а вы подтверждаете каждую.",
      app_name: "Показывается в пакете приложения для Teams.",
    },
    mail: {
      auth_mode: "password берёт поля ниже; google входит аккаунтом, подключённым на вкладке Google (Подключить с Gmail), а пустые хосты, пользователь и адрес отправителя означают Gmail и этот аккаунт.",
      from_address: "Адрес, с которого уходят ответы; письма с него игнорируются.",
    },
    microsoft: {
      default_user: "Пользователь, чей календарь читают инструменты, если вызов не указал другого, например anna@contoso.com",
      consent_tenant: "Для портала согласий: кто может войти и выдать доступ к своему аккаунту. common, organizations, consumers или id тенанта; пусто значит только этот тенант.",
    },
    google: {
      service_account_json: "Вставьте файл ключа. Поделитесь файлами или календарями с email сервисного аккаунта. Оставьте пустым, чтобы использовать OAuth.",
    },
    confluence: {
      base_url: "Например https://acme.atlassian.net/wiki",
    },
  },
  credentials: {
    title: {
      n8n: "n8n",
      jira: "Jira Cloud",
      linear: "Linear",
      microsoft: "Microsoft Graph",
      google: "Google Workspace",
      notion: "Notion",
      confluence: "Confluence",
    },
    intro: {
      n8n: "Запускайте воркфлоу n8n из потоков через их триггер Webhook или Chat и импортируйте воркфлоу n8n как потоки хаба на странице Потоки.",
      jira: "Читает задачи проекта в задачи хаба и даёт агентам комментировать, менять статус и создавать задачи. Подключите проект к проекту Jira на его странице.",
      linear: "Читает задачи команды в задачи хаба и даёт агентам комментировать, менять состояние и создавать задачи. Подключите проект к команде Linear на его странице.",
      microsoft: "Регистрация приложения Entra с application permissions. Даёт агентам инструменты календаря Outlook; то же приложение может обслуживать бота Teams.",
      google: "Инструменты Drive, Docs, Sheets и Calendar. Сервисный аккаунт для сервера или OAuth клиент, чтобы действовать от имени человека.",
      notion: "Поиск и чтение страниц, импорт в файлы пространства, создание страниц. Поделитесь страницами с интеграцией.",
      confluence: "Поиск и чтение страниц, импорт в файлы пространства, создание страниц.",
    },
  },
  trackers: {
    intro: "Учётные данные трекеров задач. Какой проект Jira или команду Linear отражает проект хаба, задаётся на странице проекта, там же запускается синхронизация.",
  },
  git: {
    username: "Имя пользователя",
  },
  // Labels of select options, by channel and field (ChannelConnector.jsx).
  options: {
    slack: {
      distribution: { private: "Закрытое: только установки, которые вы начинаете", public: "Открытое: ставить может кто угодно, вы подтверждаете" },
    },
    teams: {
      distribution: { private: "Закрытое: только установки, которые вы начинаете", public: "Открытое: ставить может кто угодно, вы подтверждаете" },
    },
    mail: {
      auth_mode: { password: "Пароль", google: "Подключённый Google-аккаунт" },
    },
  },
};
