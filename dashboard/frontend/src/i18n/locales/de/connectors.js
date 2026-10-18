export default {
  title: 'Konnektoren',
  description: 'Dienste, die dieser Hub selbst anspricht. Die Gegenrichtung zu Verbindungen, wo etwas von Ihnen anderswo läuft und hierher meldet.',
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
    git: 'GitHub und GitLab',
    alerts: 'Alarmregeln',
    trackers: "Jira und Linear",
  },
  alerts: {
    intro: 'Lösen Sie selbst eine Benachrichtigung in der Glocke aus, wenn ein Lauf fehlschlägt oder die Ausgaben eine Schwelle überschreiten, ohne dass ein Agent oder eine Person danach fragen muss.',
    rules: 'Alarmregeln',
    rulesHint: 'Wann der Arbeitsbereich selbst eine Benachrichtigung auslösen soll: ein fehlgeschlagener Lauf oder Ausgaben über einer Schwelle.',
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
    n8n: {
      base_url: "Basis-URL",
      api_key: "API-Schlüssel",
      webhook_header_name: "Webhook-Auth-Header",
      webhook_header_value: "Webhook-Auth-Wert",
    },
    slack: {
      bot_token: "Bot-Token (xoxb)",
      app_token: "App-Token (xapp)",
      signing_secret: "Signing Secret",
      mode: "Transport",
      client_id: "OAuth-Client-ID",
      client_secret: "OAuth-Client-Secret",
      distribution: "Verteilung",
      app_name: "App-Name",
    },
    discord: {
      bot_token: "Bot-Token",
      application_id: "Anwendungs-Id",
    },
    teams: {
      app_id: "Microsoft App ID",
      app_password: "App-Passwort",
      tenant_id: "Mandanten-Id",
      distribution: "Verteilung",
      app_name: "App-Name",
      developer_name: "Herausgeber",
      website_url: "Website",
      privacy_url: "URL der Datenschutzerklärung",
      terms_url: "URL der Nutzungsbedingungen",
      app_version: "App-Version",
    },
    mail: {
      auth_mode: "Anmeldung",
      imap_host: "IMAP-Host",
      imap_port: "IMAP-Port",
      imap_user: "IMAP-Benutzer",
      imap_password: "IMAP-Passwort",
      imap_folder: "Ordner",
      imap_ssl: "IMAP über SSL",
      smtp_host: "SMTP-Host",
      smtp_port: "SMTP-Port",
      smtp_user: "SMTP-Benutzer",
      smtp_password: "SMTP-Passwort",
      smtp_security: "SMTP-Sicherheit",
      from_address: "Absenderadresse",
      poll_seconds: "Abfrage alle, Sekunden",
      subject_prefix: "Betreffpräfix der Antwort",
    },
    jira: {
      base_url: "Site-URL",
      email: "Konto-E-Mail",
      api_token: "API-Token",
    },
    linear: {
      api_key: "API-Schlüssel",
    },
    microsoft: {
      tenant_id: "Mandanten-Id",
      client_id: "Client-Id",
      client_secret: "Client Secret",
      default_user: "Standardpostfach",
      consent_tenant: "Anmeldung für Endnutzer",
    },
    google: {
      client_id: "OAuth-Client-Id",
      client_secret: "OAuth-Client-Secret",
      service_account_json: "Dienstkonto-Schlüssel (JSON)",
    },
    notion: {
      api_token: "Integrations-Token",
    },
    confluence: {
      base_url: "Site-URL",
      email: "Konto-E-Mail",
      api_token: "API-Token",
    },
  },
  fieldHints: {
    n8n: {
      base_url: "Die Adresse Ihres n8n, z. B. http://localhost:5678 oder https://n8n.example.com.",
      api_key: "In n8n unter Settings, n8n API erstellt. Ohne ihn starten Flows Workflows weiterhin über den Webhook-Pfad, aber Workflow-Liste und Import aus der Instanz sind aus.",
      webhook_header_name: "Nur wenn der Webhook-Trigger Header Auth nutzt: der erwartete Header-Name.",
    },
    slack: {
      mode: "socket hält einen Websocket offen (keine öffentliche URL nötig); events erwartet, dass Slack die eingehende URL aufruft.",
      distribution: "Privat: nur Installationen, die Sie auf der Seite Verteilung starten; öffentlich: jeder kann installieren, und Sie bestätigen jede einzelne.",
      client_id: "Aus Basic Information der Slack-App. Nötig für „Zu Slack hinzufügen“.",
    },
    teams: {
      tenant_id: "Nur für Bots mit einem Mandanten.",
      distribution: "Privat: nur Organisationen, die Sie hinzufügen; öffentlich: jede Organisation kann installieren, und Sie bestätigen jede einzelne.",
      app_name: "Wird im App-Paket für Teams angezeigt.",
    },
    mail: {
      auth_mode: "password nutzt die Felder unten; google meldet sich mit dem auf dem Google-Tab verbundenen Konto an (Mit Gmail verbinden), leere Hosts, Benutzer und Absenderadresse bedeuten Gmail und dieses Konto.",
      from_address: "Die Adresse, von der Antworten gesendet werden; Nachrichten von ihr werden ignoriert.",
    },
    microsoft: {
      default_user: "Der Benutzer, dessen Kalender die Werkzeuge lesen, wenn ein Aufruf keinen nennt, z. B. anna@contoso.com",
      consent_tenant: "Für das Zustimmungsportal: wer sich anmelden darf, um das eigene Konto freizugeben. common, organizations, consumers oder eine Tenant-ID; leer heißt nur dieser Tenant.",
    },
    google: {
      service_account_json: "Die Schlüsseldatei einfügen. Dateien oder Kalender mit der E-Mail des Dienstkontos teilen. Leer lassen, um stattdessen OAuth zu nutzen.",
    },
    confluence: {
      base_url: "Zum Beispiel https://acme.atlassian.net/wiki",
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
      n8n: "n8n-Workflows aus Flows über ihren Webhook- oder Chat-Trigger starten und n8n-Workflows auf der Flows-Seite als Hub-Flows importieren.",
      jira: "Liest die Vorgänge eines Projekts als Aufgaben ein und lässt Agenten kommentieren, Status wechseln und Vorgänge anlegen. Ein Projekt wird auf seiner Seite mit einem Jira-Projekt verbunden.",
      linear: "Liest die Issues eines Teams als Aufgaben ein und lässt Agenten kommentieren, den Status ändern und Issues anlegen. Ein Projekt wird auf seiner Seite mit einem Linear-Team verbunden.",
      microsoft: "Eine Entra-App-Registrierung mit Anwendungsberechtigungen. Gibt Agenten die Outlook-Kalenderwerkzeuge; dieselbe App kann den Teams-Bot tragen.",
      google: "Werkzeuge für Drive, Docs, Sheets und Kalender. Ein Dienstkonto für einen Server oder ein OAuth-Client, um als Person zu handeln.",
      notion: "Seiten suchen und lesen, in Arbeitsbereichsdateien importieren, Seiten anlegen. Die Seiten mit der Integration teilen.",
      confluence: "Seiten suchen und lesen, in Arbeitsbereichsdateien importieren, Seiten anlegen.",
    },
  },
  trackers: {
    intro: "Zugangsdaten der Issue-Tracker. Welches Jira-Projekt oder Linear-Team ein Hub-Projekt spiegelt, wird auf der Projektseite festgelegt, wo auch die Synchronisation läuft.",
  },
  git: {
    username: "Benutzername",
  },
  // Labels of select options, by channel and field (ChannelConnector.jsx).
  options: {
    slack: {
      distribution: { private: "Privat: nur Installationen, die Sie starten", public: "Öffentlich: jeder kann installieren, Sie bestätigen" },
    },
    teams: {
      distribution: { private: "Privat: nur Installationen, die Sie starten", public: "Öffentlich: jeder kann installieren, Sie bestätigen" },
    },
    mail: {
      auth_mode: { password: "Passwort", google: "Verbundenes Google-Konto" },
    },
  },
};
