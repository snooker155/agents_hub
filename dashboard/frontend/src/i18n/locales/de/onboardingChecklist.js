export default {
  setupProgress: 'Einrichtungsfortschritt: {{done}} / {{total}}',
  reCheck: 'Erneut prüfen',
  backend: {
    title: 'Backend läuft',
    done: 'Die API ist erreichbar.',
    todo: 'Starten Sie das FastAPI-Backend und prüfen Sie erneut. Siehe Abschnitt „Installation“ unten.',
    unreachable: 'Die API ist nicht erreichbar. Starten Sie das FastAPI-Backend (siehe Abschnitt „Installation" unten) und drücken Sie „Erneut prüfen".',
  },
  provider: {
    title: 'Einen LLM-Anbieter verbinden',
    done: 'Mindestens ein Anbieter ist konfiguriert.',
    doneWithLabel: 'Mindestens ein Anbieter ist konfiguriert (Standard: {{provider}}).',
    warn: 'Ein Anbieter ist konfiguriert, aber der Verbindungstest ist fehlgeschlagen — prüfen Sie API-Schlüssel und Basis-URL in den Einstellungen.',
    todo: 'Fügen Sie in den Einstellungen einen API-Schlüssel (OpenAI / Anthropic / Google) hinzu oder verweisen Sie auf ein lokales Modell (Ollama / LM Studio).',
    openSettings: 'Einstellungen öffnen',
    testConnection: 'Verbindung testen',
  },
  workspace: {
    title: 'Einen Workspace anlegen',
    done: 'Sie haben {{count}} Workspaces. Workspaces isolieren Aufgaben, Dateien und Speicher.',
    todo: 'Ein Workspace ist die Grenze für Ihre Aufgaben, Projekte, Dateien und Speicher-Pools.',
    manage: 'Workspaces verwalten',
  },
  agent: {
    title: 'Einen Agenten hinzufügen (optional)',
    done: '{{count}} Agenten verfügbar.',
    todo: 'Eingebaute Agenten funktionieren sofort. Im Marktplatz können Sie spezialisierte Rollen in Ihren Workspace klonen.',
    openMarketplace: 'Marktplatz öffnen',
  },
  chat: {
    title: 'Ersten Chat starten',
    desc: 'Bitten Sie einen Agenten um etwas — z. B. „Fasse die Dateien in diesem Workspace zusammen“ oder „Erstelle eine Aufgabe für ein README“.',
    openChat: 'Chat öffnen',
  },
};
