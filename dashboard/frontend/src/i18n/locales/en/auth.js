// Identity, authentication and authorization (AUTH_MODE, see docs/identity.md).
// Everything here is invisible in single-operator mode.
export default {
  logout: 'Sign out',

  fields: {
    username: 'User name',
    password: 'Password',
    confirmPassword: 'Repeat password',
    role: 'Role',
  },

  roles: {
    admin: 'Administrator',
    member: 'Member',
  },

  workspaceRoles: {
    viewer: 'Viewer',
    editor: 'Editor',
    owner: 'Owner',
  },

  login: {
    title: 'Sign in',
    hint: 'This hub runs with named accounts.',
    submit: 'Sign in',
    failed: 'Could not sign in. Try again.',
    passwordsDiffer: 'The two passwords do not match.',
    bootstrapTitle: 'Create the first administrator',
    bootstrapHint: 'Nobody has signed in here yet. This account will manage the others.',
    createAdmin: 'Create administrator',
  },

  users: {
    title: 'Accounts',
    description: 'Who can sign in, and who administers this hub. Access to an individual workspace is granted on that workspace, under its Agents tab.',
    add: 'Add account',
    empty: 'No accounts yet.',
    you: 'you',
    resetPassword: 'Reset password',
    newPasswordFor: 'New password for {{name}}',
    confirmDelete: 'Delete the account "{{name}}"? Their workspace memberships go with it.',
    membershipHint: 'Workspace access is managed on the workspace itself, under its Agents tab.',
    loadFailed: 'Could not load the accounts.',
    createFailed: 'Could not create the account.',
    updateFailed: 'Could not change the account.',
    deleteFailed: 'Could not delete the account.',
    passwordFailed: 'Could not set the password.',
  },

  members: {
    title: 'Members',
    description: 'Viewers read, editors change what is in the workspace, owners also change how it is configured and who belongs to it.',
    add: 'Add member',
    empty: 'Nobody but administrators can reach this workspace yet.',
    pickUser: 'Pick an account',
    userIdPlaceholder: 'Account id',
    loadFailed: 'Could not load the members.',
    addFailed: 'Could not add the member.',
    updateFailed: 'Could not change the role.',
    removeFailed: 'Could not remove the member.',
  },
  // Single sign-on (docs/sso.md) and where an account came from.
  sso: {
    signInWith: 'Sign in with {{provider}}',
    or: 'or with a password',
    passwordsOff: 'Password sign-in is reserved for administrators.',
    completing: 'Finishing sign-in…',
    backToLogin: 'Back to sign-in',
    failedTitle: 'Single sign-on did not work',
    errors: {
      bad_state: 'The sign-in took too long or was started in another browser. Please try again.',
      provider_error: 'The identity provider refused the sign-in.',
      provider_unreachable: 'The identity provider could not be reached.',
      exchange_failed: 'The identity provider did not confirm the sign-in.',
      verification_failed: 'The identity provider\'s answer could not be verified.',
      disabled: 'This account is disabled. Ask an administrator.',
      account_failed: 'No account could be created for this identity.',
      unknown: 'Single sign-on failed. Please try again.',
    },
    source: {
      local: 'local',
      oidc: 'SSO',
      scim: 'SCIM',
    },
    sourceLabel: 'Source',
    email: 'Email',
    noPassword: 'no password',
    viaGroup: 'via group',
    viaGroupHint: 'Granted by a group mapping. The role follows the group; change the mapping on the Accounts page.',
  },
};
