// Workspace secrets (common/secrets.py, docs/secrets.md): encrypted values an
// agent receives by name. Values are write-only; only a hint is ever shown.
export default {
  title: 'Secrets',
  description: 'Encrypted values handed to an agent run as environment variables, only under the names that agent declares. A value can be replaced or deleted, never read back.',
  loadFailed: 'Could not load the secrets.',
  saveFailed: 'Could not save the secret.',
  deleteFailed: 'Could not delete the secret.',
  empty: 'No secrets in this workspace yet.',
  noKey: 'Storing a secret needs an encryption key. Set AGENTS_HUB_SECRET_KEY in .env (generate one with ah secrets keygen) and restart the backend.',
  columns: {
    name: 'Name',
    scope: 'Scope',
    hint: 'Value',
    updated: 'Updated',
  },
  scope: {
    workspace: 'Workspace-wide',
    agent: 'Agent {{agent}}',
    user: 'User {{user}}',
    agentUser: 'Agent {{agent}}, user {{user}}',
  },
  fields: {
    name: 'Name, e.g. GITHUB_TOKEN',
    value: 'Value',
    anyAgent: 'Any agent',
    anyUser: 'Any user',
  },
  add: 'Save secret',
  delete: 'Delete secret',
  nameHint: 'Upper case letters, digits and underscores, starting with a letter.',
};
