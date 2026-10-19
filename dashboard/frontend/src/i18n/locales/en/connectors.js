export default {
  title: 'Connectors',
  description: 'Services this hub reaches out to.',
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
    git: 'GitLab',
    alerts: 'Alert rules',
    trackers: "Jira",
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
    jira: {
      base_url: "Site URL",
      email: "Account email",
      api_token: "API token",
    },
  },
  credentials: {
    title: {
      jira: "Jira Cloud",
    },
    intro: {
      jira: "Read a project's issues into tasks and let agents comment, transition and create issues. Connect a project to a Jira project on its page.",
    },
  },
  trackers: {
    intro: "Credentials for Jira. Which Jira project a hub project mirrors is set on the project's page, where the sync runs.",
  },
  git: {
    username: "Username",
  },
};
