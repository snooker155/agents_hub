// The audit trail (docs/audit.md, routes/audit.py). Visible in token and
// multi mode; off entirely in single mode, where there is nobody to audit.
export default {
  title: 'Audit trail',
  description: 'Every recorded action: a sign-in, a role change, a run launched, a tool approved, a policy or budget changed, and every write request in token and multi mode. An administrator reads everything; anyone else reads their own actions plus the actions of a workspace they own.',
  system: 'system',
  details: 'Details',
  empty: 'No audit rows match these filters.',
  loadFailed: 'Could not load the audit trail.',
  exportCsv: 'Export CSV',
  exportJsonl: 'Export JSONL',

  filters: {
    actor: 'Actor',
    actorPlaceholder: 'User id or name',
    action: 'Action',
    allActions: 'Any action',
    workspace: 'Workspace',
    allWorkspaces: 'Any workspace',
    result: 'Result',
    allResults: 'Any result',
    since: 'Since',
    until: 'Until',
    text: 'Search',
    textPlaceholder: 'Path, object id or details',
    clear: 'Clear filters',
  },

  results: {
    ok: 'OK',
    denied: 'Denied',
    error: 'Error',
  },

  columns: {
    time: 'Time',
    actor: 'Actor',
    action: 'Action',
    object: 'Object',
    workspace: 'Workspace',
    result: 'Result',
    ip: 'IP',
  },

  pagination: {
    showing: 'Showing {{from}}–{{to}} of {{total}}',
    prev: 'Previous',
    next: 'Next',
  },
};
