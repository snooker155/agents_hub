// Identität, Authentifizierung und Autorisierung (AUTH_MODE, siehe docs/identity.md).
// Im Einzelbetrieb ist davon nichts sichtbar.
export default {
  logout: 'Abmelden',

  fields: {
    username: 'Benutzername',
    password: 'Passwort',
    confirmPassword: 'Passwort wiederholen',
    role: 'Rolle',
  },

  roles: {
    admin: 'Administrator',
    member: 'Mitglied',
  },

  workspaceRoles: {
    viewer: 'Leser',
    editor: 'Bearbeiter',
    owner: 'Eigentümer',
  },

  login: {
    title: 'Anmelden',
    hint: 'Dieser Hub arbeitet mit benannten Konten.',
    submit: 'Anmelden',
    failed: 'Anmeldung nicht möglich. Bitte erneut versuchen.',
    passwordsDiffer: 'Die beiden Passwörter stimmen nicht überein.',
    bootstrapTitle: 'Ersten Administrator anlegen',
    bootstrapHint: 'Hier hat sich noch niemand angemeldet. Dieses Konto verwaltet alle weiteren.',
    createAdmin: 'Administrator anlegen',
  },

  users: {
    title: 'Konten',
    description: 'Wer sich anmelden darf und wer diesen Hub verwaltet. Der Zugriff auf einen einzelnen Arbeitsbereich wird dort vergeben, im Reiter Agenten.',
    add: 'Konto anlegen',
    empty: 'Noch keine Konten.',
    you: 'Sie',
    resetPassword: 'Passwort zurücksetzen',
    newPasswordFor: 'Neues Passwort für {{name}}',
    confirmDelete: 'Das Konto „{{name}}“ löschen? Die Mitgliedschaften in Arbeitsbereichen verschwinden mit.',
    membershipHint: 'Der Zugriff auf einen Arbeitsbereich wird im Arbeitsbereich selbst vergeben, im Reiter Agenten.',
    loadFailed: 'Die Konten konnten nicht geladen werden.',
    createFailed: 'Das Konto konnte nicht angelegt werden.',
    updateFailed: 'Das Konto konnte nicht geändert werden.',
    deleteFailed: 'Das Konto konnte nicht gelöscht werden.',
    passwordFailed: 'Das Passwort konnte nicht gesetzt werden.',
  },

  members: {
    title: 'Mitglieder',
    description: 'Leser lesen, Bearbeiter ändern die Inhalte des Arbeitsbereichs, Eigentümer ändern zusätzlich seine Konfiguration und seine Mitglieder.',
    add: 'Mitglied hinzufügen',
    empty: 'Außer Administratoren erreicht diesen Arbeitsbereich noch niemand.',
    pickUser: 'Konto auswählen',
    userIdPlaceholder: 'Konto-ID',
    loadFailed: 'Die Mitglieder konnten nicht geladen werden.',
    addFailed: 'Das Mitglied konnte nicht hinzugefügt werden.',
    updateFailed: 'Die Rolle konnte nicht geändert werden.',
    removeFailed: 'Das Mitglied konnte nicht entfernt werden.',
  },
};
