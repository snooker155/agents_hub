export default {
  added: 'hinzugefügt',
  // Die Beschriftung eines eingeklappten Gedankens im Chat-Feed.
  thought: 'Gedanke',
  // „und 4 weitere Änderungen“ — das Ende einer zu langen Änderungsliste.
  moreChanges: 'und {{count}} weitere Änderungen',
  // Was der Agent mit dem bearbeiteten Objekt gemacht hat, wie es im Chat steht.
  change: {
    added: 'hinzugefügt',
    removed: 'entfernt',
    updated: 'geändert',
    set: 'gesetzt',
  },
  // Woran. Singular: jede Zeile benennt genau eine Sache.
  kinds: {
    locations: 'Ort',
    items: 'Gegenstand',
    entities: 'Objekt',
    globals: 'Weltwert',
    stats: 'Wert',
    roles: 'Rolle',
    actions: 'Aktion',
    objectives: 'Ziel',
    rules: 'Regeln',
    base_actions: 'eingebaute Aktionen',
    end_when: 'Ende',
    name: 'Name',
    description: 'Beschreibung',
    starting_location: 'Startort',
    time_of_day: 'Tageszeit',
    hours_per_tick: 'Stunden pro Tick',
    environment: 'Umgebung',
    activation: 'Aktivierung',
    env_params: 'Parameter',
    limits: 'Limit',
  },
  // Wohin ein remember- oder forget-Schritt geschrieben hat: „Im persönlichen Speicher gesichert · dev".
  memory: {
    remember: { personal: 'Im persönlichen Speicher gesichert · {{workspace}}', pool: 'Im Speicher „{{pool}}" gesichert · {{workspace}}' },
    forget: { personal: 'Aus dem persönlichen Speicher entfernt · {{workspace}}', pool: 'Aus dem Speicher „{{pool}}" entfernt · {{workspace}}' },
  },
};
