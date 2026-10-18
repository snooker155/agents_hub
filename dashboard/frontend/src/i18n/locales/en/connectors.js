export default {
  title: 'Connectors',
  description: 'Services this hub reaches out to. The other direction from Connections, where something of yours runs elsewhere and reports in.',
  workspaceBanner: {
    title: 'Configuring connectors for workspace {{workspace}}.',
    rule: "Connectors defined in the default workspace work everywhere; connectors defined here work only here.",
  },
  source: {
    here: 'Defined in this workspace',
    default: 'From the default workspace',
    defineForWorkspace: 'Define for this workspace',
    removeUseDefault: "Remove, use the default's",
    confirmRemove: "Remove this workspace's own definition? It will use the default's instead.",
    alsoDefinedIn: 'Also defined in: {{list}}',
  },
  tabs: {
    git: 'GitHub & GitLab',
    alerts: 'Alert rules',
    trackers: "Jira & Linear",
  },
  alerts: {
    intro: 'Raise a notification in the bell on your own when a run fails or spend crosses a threshold, no agent or person needed to ask for it.',
    rules: 'Alert rules',
    rulesHint: 'When the workspace should raise a notification on its own: a run failing, or spend past a threshold.',
    noRules: 'No alert rules yet.',
    ruleKind: 'Rule',
    ruleKinds: {
      run_failed: 'Run failed',
      spend_daily_over: 'Daily spend over',
      spend_run_over: 'Run cost over',
    },
    threshold: 'Threshold, USD',
    agentFilter: 'Agent filter',
    agentFilterPlaceholder: 'Any agent',
    enabled: 'Enabled',
    addRule: 'Add rule',
    confirmRemoveRule: 'Remove this rule?',
  },
  clearSecrets: "Clear secrets",
  configured: "configured",
  notConfigured: "not configured",
  channels: {
    loadFailed: "Failed to load the channel",
  },
  fields: {
    n8n: {
      base_url: "Base URL",
      api_key: "API key",
      webhook_header_name: "Webhook auth header",
      webhook_header_value: "Webhook auth value",
    },
    slack: {
      bot_token: "Bot token (xoxb)",
      app_token: "App token (xapp)",
      signing_secret: "Signing secret",
      mode: "Transport",
      client_id: "OAuth client ID",
      client_secret: "OAuth client secret",
      distribution: "Distribution",
      app_name: "App name",
    },
    discord: {
      bot_token: "Bot token",
      application_id: "Application id",
    },
    teams: {
      app_id: "Microsoft App ID",
      app_password: "App password",
      tenant_id: "Tenant id",
      distribution: "Distribution",
      app_name: "App name",
      developer_name: "Publisher",
      website_url: "Website",
      privacy_url: "Privacy policy URL",
      terms_url: "Terms of use URL",
      app_version: "App version",
    },
    mail: {
      auth_mode: "Sign in with",
      imap_host: "IMAP host",
      imap_port: "IMAP port",
      imap_user: "IMAP user",
      imap_password: "IMAP password",
      imap_folder: "Folder",
      imap_ssl: "IMAP over SSL",
      smtp_host: "SMTP host",
      smtp_port: "SMTP port",
      smtp_user: "SMTP user",
      smtp_password: "SMTP password",
      smtp_security: "SMTP security",
      from_address: "From address",
      poll_seconds: "Poll every, seconds",
      subject_prefix: "Reply subject prefix",
    },
    jira: {
      base_url: "Site URL",
      email: "Account email",
      api_token: "API token",
    },
    linear: {
      api_key: "API key",
    },
    microsoft: {
      tenant_id: "Tenant id",
      client_id: "Client id",
      client_secret: "Client secret",
      default_user: "Default mailbox",
      consent_tenant: "End user sign-in authority",
    },
    google: {
      client_id: "OAuth client id",
      client_secret: "OAuth client secret",
      service_account_json: "Service account key (JSON)",
    },
    notion: {
      api_token: "Integration token",
    },
    confluence: {
      base_url: "Site URL",
      email: "Account email",
      api_token: "API token",
    },
  },
  fieldHints: {
    n8n: {
      base_url: "The address of your n8n, e.g. http://localhost:5678 or https://n8n.example.com.",
      api_key: "Created in n8n under Settings, n8n API. Without it flows can still run workflows by their webhook path, but the workflow list and import from the instance are off.",
      webhook_header_name: "Only when the Webhook trigger uses Header Auth: the header name it expects.",
    },
    slack: {
      mode: "socket keeps a websocket open (no public URL needed); events expects Slack to call the inbound URL.",
      distribution: "Private keeps installs to the ones you start on the Distribution page; public lets anyone install, and you approve each one.",
      client_id: "From Basic Information of the Slack app. Needed for Add to Slack.",
    },
    teams: {
      tenant_id: "Only for single tenant bots.",
      distribution: "Private keeps installs to the ones you add; public lets any organisation install, and you approve each one.",
      app_name: "Shown in the Teams app package.",
    },
    mail: {
      auth_mode: "password uses the fields below; google signs in with the account connected on the Google tab (Connect with Gmail), and empty hosts, user and from address mean Gmail's and that account's.",
      from_address: "The address replies are sent from; messages from it are ignored.",
    },
    microsoft: {
      default_user: "The user whose calendar the tools read when a call names none, e.g. anna@contoso.com",
      consent_tenant: "For the consent portal: who may sign in to grant their own account. common, organizations, consumers or a tenant id; empty means this tenant only.",
    },
    google: {
      service_account_json: "Paste the key file. Share the files or calendars with the service account's email. Leave empty to use OAuth instead.",
    },
    confluence: {
      base_url: "For example https://acme.atlassian.net/wiki",
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
      n8n: "Run n8n workflows from flows through their Webhook or Chat trigger, and import n8n workflows as hub flows on the Flows page.",
      jira: "Read a project's issues into tasks and let agents comment, transition and create issues. Connect a project to a Jira project on its page.",
      linear: "Read a team's issues into tasks and let agents comment, change state and create issues. Connect a project to a Linear team on its page.",
      microsoft: "An Entra app registration with application permissions. Gives agents the Outlook calendar tools; the same app can back the Teams bot.",
      google: "Drive, Docs, Sheets and Calendar tools. Use a service account for a server, or an OAuth client to act as a person.",
      notion: "Search and read pages, import them into workspace files, create pages. Share the pages with the integration.",
      confluence: "Search and read pages, import them into workspace files, create pages.",
    },
  },
  trackers: {
    intro: "Credentials for the issue trackers. Which Jira project or Linear team a hub project mirrors is set on the project's page, where the sync runs.",
  },
  git: {
    username: "Username",
  },
  // Labels of select options, by channel and field (ChannelConnector.jsx).
  options: {
    slack: {
      distribution: { private: "Private: only installs you start", public: "Public: anyone can install, you approve" },
    },
    teams: {
      distribution: { private: "Private: only installs you start", public: "Public: anyone can install, you approve" },
    },
    mail: {
      auth_mode: { password: "Password", google: "Connected Google account" },
    },
  },
};
