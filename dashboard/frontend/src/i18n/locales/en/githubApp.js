// The GitHub App card on the Git connector page (routes/github_app.py,
// docs/github-app.md).
export default {
  title: 'GitHub App',
  description: 'With a GitHub App the hub issues short-lived tokens itself. Bind an installation to a workspace, and every run there whose agent declares GITHUB_TOKEN pushes and opens pull requests as the app.',
  notConfigured: 'Not configured on this hub.',
  envHint: 'Create the app on GitHub, then set these variables in the backend environment and restart:',
  install: 'Install',
  sync: 'Sync',
  noInstallations: 'No installations yet. Install the app on an account or organisation, then sync.',
  account: 'Account',
  type: 'Type',
  repositories: 'Repositories',
  workspace: 'Workspace',
  unbound: 'Not bound',
  unbind: 'Unbind',
  bindTo: 'Workspace for {{account}}',
  types: {
    Organization: 'Organisation',
    User: 'User',
  },
  targets: {
    all: 'All',
    selected: 'Selected',
  },
  loadFailed: 'Could not load the GitHub App.',
  syncFailed: 'Could not sync the installations.',
  bindFailed: 'Could not change the binding.',
};
