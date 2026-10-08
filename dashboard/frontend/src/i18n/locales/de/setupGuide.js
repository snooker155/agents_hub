export default {
  pill: 'Einrichtung {{done}}/{{total}}',
  progress: '{{done}} von {{total}}',
  groups: {
    setup: 'Hub einrichten',
    start: 'Jetzt loslegen',
  },
  loading: 'Anleitung wird geladen…',
  doIt: 'Mit dem Assistenten erledigen',
  skip: 'Überspringen',
  unskip: 'Übersprungenen Schritt zurückholen',
  takeTour: 'Tour starten',
  restart: 'Anleitung neu starten',
  finish: 'Abschließen',
  steps: {
    model: {
      title: 'Ein Modell verbinden',
      why: 'Jeder Agent, auch Sie, denkt mit einem Modell, ohne das läuft nichts.',
    },
    default_model: {
      title: 'Standardmodell wählen',
      why: 'Jeder Arbeitsbereich ohne eigenes Modell nutzt dieses, es bestimmt Geschwindigkeit und Kosten.',
    },
    voice: {
      title: 'Dem Assistenten eine Stimme geben',
      why: 'Mit einem Sprach- und einem Transkriptionsmodell hört und spricht der Assistent in jedem Browser.',
    },
    web_search: {
      title: 'Websuche einschalten',
      why: 'Agenten können dann im Web suchen, nicht nur Seiten öffnen, die man ihnen gibt.',
    },
    demo: {
      title: 'Den Demo-Arbeitsbereich ansehen',
      why: 'Vier Agenten mit Chats, Ansichten und einem Pulse zeigen, was der Hub kann, noch bevor Sie etwas gebaut haben.',
    },
    people: {
      title: 'Ihr Team einladen',
      why: 'Jede Person bekommt ihren eigenen Zugang, einen persönlichen Arbeitsbereich und Assistenten.',
    },
    health: {
      title: 'Den Zustand des Hubs prüfen',
      why: 'Der Doctor prüft Datenbank, Modellverbindung, Runtime und den Rest, jeweils mit Lösung.',
    },
    first_chat: {
      title: 'Im Chat mit einem Agenten sprechen',
      why: 'Im Chat arbeiten Sie mit jedem Agenten, mit Dateien, Referenzen und Freigaben.',
    },
    channel: {
      title: 'Den Hub über Telegram, Slack oder E-Mail erreichen',
      why: 'Dann erreichen Sie Ihre Agenten über den Messenger auf dem Handy oder per E-Mail.',
    },
    accounts: {
      title: 'Ihre Konten verbinden',
      why: 'Agenten können dann Ihre Google-, Microsoft-, Jira-, Linear- oder Notion-Daten lesen und schreiben.',
    },
    first_agent: {
      title: 'Einen eigenen Agenten erstellen',
      why: 'Ein Agent mit eigenen Anweisungen, Tools und Modell erledigt eine Art von Aufgabe gut.',
    },
    first_task: {
      title: 'Einem Agenten eine Aufgabe geben',
      why: 'Eine Aufgabe läuft selbständig, Ergebnis, Kosten und Verlauf bleiben auf der Tasks-Seite.',
    },
    automation: {
      title: 'Etwas von selbst laufen lassen',
      why: 'Ein Watcher weckt einen Agenten bei neuer Post oder einer geänderten Seite; ein Pulse lässt einen Agenten nach Zeitplan nachsehen.',
    },
    tour: {
      title: 'Die Willkommenstour machen',
      why: 'Zwei Minuten über die wichtigsten Seiten, damit Sie wissen, wo alles liegt.',
    },
  },
  firstModel: {
    provider: 'Anbieter',
    apiKey: 'API-Schlüssel',
    baseUrl: 'Basis-URL (optional)',
    connect: 'Verbinden',
    connecting: 'Verbinde…',
    adminOnly: 'Bitten Sie Ihren Administrator, ein Modell zu verbinden.',
    runtimeHint: 'Die eigene Runtime des Hubs, ein auf diesen Rechner geladenes Modell, wird auf der Seite Models, Tab Local, eingestellt.',
    runtimeLink: 'Tab Local öffnen',
    providers: {
      openai: 'OpenAI',
      anthropic: 'Anthropic',
      google: 'Google Gemini',
      ollama: 'Ollama auf diesem Rechner',
      lmstudio: 'LM Studio auf diesem Rechner',
    },
    errors: {
      forbidden: 'Bitten Sie Ihren Administrator, ein Modell zu verbinden.',
      rejected: 'Der Schlüssel wurde nicht akzeptiert. Prüfen Sie ihn und versuchen Sie es erneut.',
      missing: 'Geben Sie zuerst einen API-Schlüssel ein.',
      no_models: 'Dieser Anbieter hat noch kein nutzbares Modell.',
      unknown_provider: 'Dieser Anbieter ist nicht bekannt.',
      failed: 'Verbindung fehlgeschlagen. Versuchen Sie es erneut.',
    },
  },
};
