export default {
  title: 'Konnektoren',
  description: 'Dienste, die dieser Hub selbst anspricht.',
  workspaceBanner: {
    title: 'Konnektoren werden für den Arbeitsbereich {{workspace}} konfiguriert.',
    rule: 'Im Standardarbeitsbereich festgelegte Konnektoren funktionieren überall, hier festgelegte Konnektoren funktionieren nur hier.',
  },
  source: {
    here: 'In diesem Arbeitsbereich festgelegt',
    default: 'Aus dem Standardarbeitsbereich',
    defineForWorkspace: 'Für diesen Arbeitsbereich festlegen',
    removeUseDefault: 'Entfernen, den Standard verwenden',
    confirmRemove: 'Die eigene Festlegung dieses Arbeitsbereichs entfernen? Danach gilt wieder die des Standardarbeitsbereichs.',
    alsoDefinedIn: 'Auch festgelegt in: {{list}}',
  },
  tabs: {
    git: 'GitLab',
    alerts: 'Alarmregeln',
    trackers: "Jira",
  },
  alerts: {
    intro: 'Lösen Sie selbst eine Benachrichtigung in der Glocke aus, wenn ein Lauf fehlschlägt oder die Ausgaben eine Schwelle überschreiten, ohne dass ein Agent oder eine Person danach fragen muss.',
    rules: 'Alarmregeln',
    noRules: 'Noch keine Alarmregeln.',
    ruleKind: 'Regel',
    ruleKinds: {
      run_failed: 'Lauf fehlgeschlagen',
      spend_daily_over: 'Tagesausgaben über',
      spend_run_over: 'Laufkosten über',
    },
    threshold: 'Schwelle, USD',
    agentFilter: 'Agentenfilter',
    agentFilterPlaceholder: 'Jeder Agent',
    enabled: 'Aktiviert',
    addRule: 'Regel hinzufügen',
    confirmRemoveRule: 'Diese Regel entfernen?',
  },
  clearSecrets: "Geheimnisse löschen",
  configured: "eingerichtet",
  notConfigured: "nicht eingerichtet",
  channels: {
    loadFailed: "Der Kanal konnte nicht geladen werden",
  },
  fields: {
    jira: {
      base_url: "Site-URL",
      email: "Konto-E-Mail",
      api_token: "API-Token",
    },
  },
  credentials: {
    title: {
      jira: "Jira Cloud",
    },
    intro: {
      jira: "Liest die Vorgänge eines Projekts als Aufgaben ein und lässt Agenten kommentieren, Status wechseln und Vorgänge anlegen. Ein Projekt wird auf seiner Seite mit einem Jira-Projekt verbunden, dort läuft auch die Synchronisation.",
    },
  },
  git: {
    username: "Benutzername",
  },
};
