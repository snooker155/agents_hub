export default {
  optionLabel: 'Aus einer Vorlage',
  optionDesc: 'Wählen Sie einen fertigen Agenten: SRE Agent, KI Teamkollege, Support Triage, Recherche und weitere. Vor dem Anlegen lässt er sich umbenennen und anpassen.',
  stepTemplate: 'Vorlage',
  pickHint: 'Jede Vorlage bringt einen abgestimmten Prompt, sichere Werkzeuge und eine Standard-Bewertung mit. Alles lässt sich vor dem Anlegen ändern.',
  loading: 'Vorlagen werden geladen…',
  loadFailed: 'Vorlagen konnten nicht geladen werden',
  needs: 'Benötigt',
  worksWith: 'Arbeitet mit',
  connected: 'verbunden',
  notConnected: 'nicht verbunden',
  tryAsking: 'Zum Beispiel',
  adjust: 'Prompt und Werkzeuge anpassen',
  prompt: 'Prompt',
  tools: 'Werkzeuge',
  template: 'Vorlage',
  rubric: 'Standard-Ergebnis',
  created: '{{name}} aus der Vorlage angelegt',
  createFailed: 'Agent konnte nicht angelegt werden',
  categories: {
    Engineering: 'Entwicklung',
    Productivity: 'Team',
    Data: 'Daten',
    Finance: 'Finanzen',
    Legal: 'Recht',
    Support: 'Support',
    Research: 'Recherche',
    HR: 'Personal',
    Marketing: 'Marketing',
    Sales: 'Vertrieb',
  },
  items: {
    sre_agent: {
      name: 'SRE Agent',
      description: 'Untersucht einen Vorfall anhand von Logs, Manifesten und Runbooks, benennt die Ursache mit Belegen und schlägt die Korrektur als Pull Request zur Freigabe vor.',
    },
    ai_teammate: {
      name: 'KI Teamkollege',
      description: 'Arbeitet im Team-Chat mit, antwortet aus den eigenen Dokumenten und Notizen mit Quellen, übernimmt kleine Aufgaben und merkt sich Entscheidungen.',
    },
    github_issue_agent: {
      name: 'GitHub Issue Agent',
      description: 'Nimmt ein aus GitHub oder GitLab synchronisiertes Issue, findet die Ursache im Code, schreibt eine gezielte Korrektur mit Test und öffnet einen Pull Request.',
    },
    data_agent: {
      name: 'Daten-Agent',
      description: 'Beantwortet Geschäftsfragen aus den verbundenen Datenbanken und Tabellen, zeigt die Abfrage hinter jeder Zahl und erstellt Diagramme.',
    },
    invoice_agent: {
      name: 'Rechnungs-Agent',
      description: 'Liest Lieferant, Daten, Positionen, Steuer und Summen aus Rechnungen, prüft Rechnung, Dubletten und Bestellungen und bereitet Datensätze zur Freigabe vor.',
    },
    contract_review_agent: {
      name: 'Vertragsprüfung',
      description: 'Prüft einen Vertrag gegen das Playbook des Teams: Kernbedingungen, riskante oder fehlende Klauseln nach Schwere, Änderungsvorschläge und ein kurzes Memo.',
    },
    support_triage_agent: {
      name: 'Support-Triage',
      description: 'Ordnet Tickets nach Bereich, Dringlichkeit und Stimmung ein, findet die Antwort in der Wissensdatenbank, entwirft eine Antwort zur Freigabe und legt Fehler als Aufgaben an.',
    },
    research_agent: {
      name: 'Recherche-Agent',
      description: 'Recherchiert eine Frage im Web sowie in Ihren Dateien und Ihrem Gedächtnis, vergleicht die Quellen und schreibt einen Bericht mit Belegen, Sicherheitsgrad und offenen Fragen.',
    },
    meeting_notes_agent: {
      name: 'Protokoll-Agent',
      description: 'Macht aus einem Transkript oder groben Notizen eine Zusammenfassung, Entscheidungen, Aufgaben mit Verantwortlichen und Terminen sowie offene Fragen und legt die Aufgaben an.',
    },
    recruiting_screener_agent: {
      name: 'Bewerbungs-Screening',
      description: 'Prüft Lebensläufe gegen Muss- und Kann-Kriterien einer Stelle mit Belegen, markiert Lücken, ignoriert geschützte Merkmale und schreibt eine Shortlist mit Interviewfragen.',
    },
    content_writer_agent: {
      name: 'Content-Autor',
      description: 'Schreibt aus einem Briefing Blogbeiträge, Newsletter und Social Posts im Ton Ihrer Marke: erst die Gliederung, Fakten gegen Ihre Quellen geprüft, Überschriftenvarianten.',
    },
    lead_research_agent: {
      name: 'Lead-Recherche',
      description: 'Recherchiert ein Unternehmen oder eine Person aus öffentlichen Quellen und Ihrem CRM und schreibt ein Account-Briefing und einen persönlichen ersten Entwurf. Sendet nie.',
    },
  },
};
