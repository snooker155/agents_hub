// Das Terminal-Panel: eine Shell im Container eines Laufs oder einer
// Service-Replik (components/terminal/, docs/terminal.md).
export default {
  title: 'Terminal',
  open: 'Terminal',
  openRunHint: 'Eine Shell im Container dieses Laufs öffnen',
  openReplicaHint: 'Eine Shell im Container dieser Replik öffnen',
  status: {
    idle: 'Startet',
    connecting: 'Verbinde',
    reconnecting: 'Verbinde erneut',
    open: 'Verbunden',
    ended: 'Sitzung beendet',
    taken: 'Anderswo geöffnet',
    error: 'Nicht verfügbar',
  },
  hide: 'Ausblenden',
  hideHint: 'Panel ausblenden. Die Shell läuft noch {{seconds}} s weiter, erneutes Öffnen des Terminals kehrt zu ihr zurück.',
  end: 'Sitzung beenden',
  endHint: 'Die Shell jetzt schließen',
  newSession: 'Neue Sitzung',
  notices: {
    resumed: 'Sitzung fortgesetzt, letzte Ausgabe erneut angezeigt',
    expired: 'die vorige Sitzung ist beendet, eine neue wird geöffnet',
    takenOver: 'diese Sitzung wurde in einem anderen Fenster geöffnet',
  },
  reasons: {
    exited: 'die Shell wurde beendet (Code {{code}})',
    closed: 'die Sitzung wurde geschlossen',
    grace: 'niemand hat sich rechtzeitig neu verbunden, die Sitzung ist beendet',
    idle: 'die Sitzung war zu lange untätig und ist beendet',
    shutdown: 'der Hub wurde neu gestartet, die Sitzung ist beendet',
    expired: 'die Verbindung ließ sich nicht rechtzeitig wiederherstellen, die Sitzung ist beendet',
  },
};
