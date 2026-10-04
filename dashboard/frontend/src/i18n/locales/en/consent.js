// The consent portal on the agent page (AgentConsentCard, docs/consent.md):
// which providers the agent acts on as a widget or channel end user, and the
// personal grants those end users gave it.
export default {
  title: 'Account access',
  intro: 'In a widget or chat channel conversation, the agent can ask the person to grant their own Google or Microsoft account through a public consent page. With a provider switched on, its tools for that provider act only as the person in the conversation there, never as the hub\'s own connection.',
  actAs: 'Act as the end user on {{provider}}',
  notReady: 'No {{provider}} app is set up on the Connectors page yet, so the agent cannot ask for it.',
  redirectUri: 'Redirect URI to register with Google and Microsoft:',
  save: 'Save access',
  saved: 'Account access saved.',
  saveFailed: 'Could not save the account access.',
  loadFailed: 'Could not load the account access.',
  grantsTitle: 'Granted by end users in this workspace',
  noGrants: 'Nobody has granted access yet.',
  unknownAccount: 'Unknown account',
  revoke: 'Revoke',
  revoking: 'Revoking…',
  revokeConfirm: 'Revoke the access {{account}} gave? The agent loses it at once.',
  revokeFailed: 'Could not revoke the access.',
  via: {
    widget: 'Widget visitor',
    channel: 'Chat channel',
    other: 'End user',
  },
  access: {
    calendar: 'See and change calendar events',
    calendar_read: 'See calendar events',
    drive_read: 'See and download Drive files',
    drive: 'See, change, create and delete Drive files',
    docs: 'See, change and create Google Docs',
    sheets: 'See, change and create Google Sheets',
    mail_read: 'Read email',
    mail_send: 'Send email as the person',
    files_read: 'See and download OneDrive files',
    files: 'See, change, create and delete OneDrive files',
  },
};
