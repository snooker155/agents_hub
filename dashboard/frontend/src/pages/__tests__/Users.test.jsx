import {
  fireEvent, render, screen, waitFor, within,
} from '@testing-library/react';
import {
  beforeEach, describe, expect, it, vi,
} from 'vitest';

import { I18nProvider } from '../../i18n';

// The Accounts page in multi mode: where each account came from (local, SSO,
// SCIM), whether a password is any use to it, and the groups and mappings
// that grant access to many accounts at once (routes/groups.py).

const ok = (data) => Promise.resolve({ data });

const USERS = [
  { id: 'u1', username: 'root', role: 'admin', source: 'local', has_password: true, email: '' },
  { id: 'u2', username: 'alice', role: 'member', source: 'oidc', has_password: false, email: 'alice@example.com' },
  { id: 'u3', username: 'carl', role: 'member', source: 'scim', has_password: true, email: 'carl@example.com' },
];

const GROUPS = [
  { id: 'g1', name: 'devs', display_name: 'devs', source: 'oidc', member_count: 2 },
  { id: 'g2', name: 'ops', display_name: 'Operations', source: 'manual', member_count: 0 },
];

const MAPPINGS = [
  { id: 'm1', group_name: 'hub-admins', target: 'role', role: 'admin', workspace: null },
  { id: 'm2', group_name: 'devs', target: 'workspace', role: 'editor', workspace: 'w1' },
];

const api = vi.hoisted(() => ({
  getUsers: vi.fn(),
  getGroups: vi.fn(),
  getGroupMappings: vi.fn(),
  getWorkspaces: vi.fn(),
  getGroupMembers: vi.fn(),
  setGroupMembers: vi.fn(),
  createGroup: vi.fn(),
  deleteGroup: vi.fn(),
  createGroupMapping: vi.fn(),
  deleteGroupMapping: vi.fn(),
  getSpendLimits: vi.fn(),
  setDefaultSpendLimit: vi.fn(),
  updateUser: vi.fn(),
  createUser: vi.fn(),
}));

// The member menu section has its own client and its own test.
vi.mock('../../api/memberMenu', () => ({
  getMemberMenu: () => Promise.resolve({ data: { hidden: [], default: [], is_default: true } }),
  setMemberMenu: () => Promise.resolve({ data: { hidden: [], default: [], is_default: true } }),
}));

vi.mock('../../api', () => ({
  ...Object.fromEntries(Object.keys(api).map((k) => [k, (...a) => api[k](...a)])),
  deleteUser: vi.fn(),
  resetUserPassword: vi.fn(),
}));

vi.mock('../../components/auth', async (importOriginal) => ({
  ...(await importOriginal()),
  useAuth: () => ({ mode: 'multi', user: { id: 'u1' }, features: { groups: true } }),
}));

import Users from '../Users';

const show = () => render(<I18nProvider><Users /></I18nProvider>);

describe('Users page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getUsers.mockImplementation(() => ok(USERS));
    api.getGroups.mockImplementation(() => ok(GROUPS));
    api.getGroupMappings.mockImplementation(() => ok(MAPPINGS));
    api.getWorkspaces.mockImplementation(() => ok([{ name: 'default' }, { name: 'w1' }]));
    api.getGroupMembers.mockImplementation(() => ok([{ id: 'u2', username: 'alice' }]));
    api.setGroupMembers.mockImplementation(() => ok([]));
    api.createGroupMapping.mockImplementation(() => ok({}));
    api.createGroup.mockImplementation(() => ok({}));
  });

  it('shows each person\'s month spend against their limit and saves the default', async () => {
    api.getUsers.mockImplementation(() => ok([
      { ...USERS[0], spend_limit_usd: null },
      { ...USERS[1], spend_limit_usd: 20 },
      { ...USERS[2], spend_limit_usd: 0 },
    ]));
    api.getSpendLimits.mockImplementation(() => ok({
      period: 'monthly',
      default_limit_usd: 10,
      users: {
        u1: { limit_usd: 10, source: 'default', spend: 2, exceeded: false },
        u2: { limit_usd: 20, source: 'user', spend: 25, exceeded: true },
        u3: { limit_usd: 0, source: 'user', spend: 1.5, exceeded: false },
      },
    }));
    api.setDefaultSpendLimit.mockImplementation(() => ok({ default_limit_usd: 12 }));
    show();
    await screen.findByTestId('limit-alice');
    expect(screen.getByTestId('limit-root').textContent).toContain('$2.0000 of $10.00');
    expect(screen.getByTestId('limit-root').textContent).toContain('default');
    expect(screen.getByTestId('limit-alice').textContent).toBe('$25.0000 of $20.00');
    expect(screen.getByTestId('limit-carl').textContent).toBe('$1.5000, no limit');

    fireEvent.change(screen.getByLabelText(/Default limit per person/), { target: { value: '12' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0]);
    await waitFor(() => expect(api.setDefaultSpendLimit).toHaveBeenCalledWith(12));
  });

  it('sets a person\'s own limits in place of the defaults, empty following them', async () => {
    api.getUsers.mockImplementation(() => ok([
      { ...USERS[0], spend_limit_usd: null, rate_limit_per_minute: null, tokens_per_day: null },
      { ...USERS[1], spend_limit_usd: 20, rate_limit_per_minute: 120, tokens_per_day: 0 },
    ]));
    api.getSpendLimits.mockImplementation(() => ok({
      period: 'monthly',
      default_limit_usd: 10,
      rate_limit_defaults: { rate_limit_per_minute: 60, tokens_per_day: 50000 },
      users: {
        u1: { limit_usd: 10, source: 'default', spend: 2, exceeded: false },
        u2: { limit_usd: 20, source: 'user', spend: 3, exceeded: false },
      },
    }));
    api.updateUser.mockImplementation(() => ok({}));
    show();
    await screen.findByTestId('limit-alice');
    expect(screen.getByTestId('limit-alice').textContent).toContain('120/min · no token limit');
    expect(screen.getByTestId('limit-root').textContent).not.toContain('/min');

    fireEvent.click(screen.getByTestId('limits-root'));
    const modal = await screen.findByTestId('limits-modal');
    expect(within(modal).getByLabelText('Requests per minute').placeholder).toBe('Default: 60');
    expect(within(modal).getByLabelText('Budget, USD per month').placeholder).toBe('Default: $10.00');
    // The browser stops a fraction at the field's step; the form checks again.
    fireEvent.change(within(modal).getByLabelText('Requests per minute'), { target: { value: '1.5' } });
    fireEvent.submit(modal);
    expect(await within(modal).findByRole('alert')).toBeTruthy();
    expect(api.updateUser).not.toHaveBeenCalled();

    fireEvent.change(within(modal).getByLabelText('Requests per minute'), { target: { value: '300' } });
    fireEvent.change(within(modal).getByLabelText('Tokens per day on /v1'), { target: { value: '0' } });
    fireEvent.click(within(modal).getByTestId('limits-save'));
    await waitFor(() => expect(api.updateUser).toHaveBeenCalledWith('u1', {
      spend_limit_usd: null, rate_limit_per_minute: 300, tokens_per_day: 0,
    }));
    await waitFor(() => expect(screen.queryByTestId('limits-modal')).toBeNull());
  });

  it('opens the limits from the row\'s Limits button and takes a budget with a new account', async () => {
    api.getSpendLimits.mockImplementation(() => ok({
      period: 'monthly', default_limit_usd: 0, rate_limit_defaults: {}, users: {},
    }));
    api.createUser.mockImplementation(() => ok({}));
    show();
    fireEvent.click(await screen.findByTestId('set-limits-alice'));
    expect(within(await screen.findByTestId('limits-modal')).getByText('alice')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.change(screen.getByLabelText('User name'), { target: { value: 'dana' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret-pass' } });
    const budget = screen.getByLabelText('Budget, USD per month');
    expect(budget.placeholder).toBe('Default: no limit');
    fireEvent.change(budget, { target: { value: '15' } });
    fireEvent.submit(budget.closest('form'));
    await waitFor(() => expect(api.createUser).toHaveBeenCalledWith(
      expect.objectContaining({ username: 'dana', spend_limit_usd: 15 }),
    ));
  });

  it('shows where each account came from and offers a password reset only where it helps', async () => {
    show();
    await screen.findByText('alice');
    const rowOf = (name) => screen.getByText(name).closest('tr');
    expect(within(rowOf('root')).getByTestId('account-source').textContent).toBe('local');
    expect(within(rowOf('alice')).getByTestId('account-source').textContent).toBe('SSO');
    expect(within(rowOf('carl')).getByTestId('account-source').textContent).toBe('SCIM');
    expect(within(rowOf('alice')).getByText('alice@example.com')).toBeTruthy();
    expect(within(rowOf('alice')).getByText('no password')).toBeTruthy();
    expect(within(rowOf('root')).queryByText('Reset password')).toBeTruthy();
    expect(within(rowOf('alice')).queryByText('Reset password')).toBeNull();
    // A provisioned account that has a password may still reset it.
    expect(within(rowOf('carl')).queryByText('Reset password')).toBeTruthy();
  });

  it('lists groups with member counts and mappings in words', async () => {
    show();
    expect((await screen.findByTestId('group-count-devs')).textContent).toBe('2');
    expect(screen.getByText('Operations')).toBeTruthy();
    expect(screen.getByText('global role Administrator')).toBeTruthy();
    expect(screen.getByText('Editor in workspace w1')).toBeTruthy();
  });

  it('edits a group\'s members with the account checklist', async () => {
    show();
    await screen.findByTestId('group-count-devs');
    const row = screen.getByTestId('group-count-devs').closest('tr');
    fireEvent.click(within(row).getByText('Edit members'));
    const members = await screen.findByRole('group', { name: 'Members' });
    const alice = within(members).getByLabelText('alice');
    await waitFor(() => expect(alice.checked).toBe(true));
    fireEvent.click(within(members).getByLabelText('carl'));
    expect(screen.getByText(/comes from the identity provider/)).toBeTruthy();
    fireEvent.click(screen.getByText('Save members'));
    await waitFor(() => expect(api.setGroupMembers).toHaveBeenCalled());
    const [id, userIds] = api.setGroupMembers.mock.calls[0];
    expect(id).toBe('g1');
    expect([...userIds].sort()).toEqual(['u2', 'u3']);
  });

  it('adds a workspace mapping from the form', async () => {
    show();
    await screen.findByTestId('group-count-devs');
    fireEvent.change(screen.getByLabelText('Group', { selector: 'input' }), { target: { value: 'devs' } });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'workspace' } });
    fireEvent.change(await screen.findByLabelText('Workspace'), { target: { value: 'default' } });
    fireEvent.change(screen.getByLabelText('Role', { selector: '#mapping-role' }), { target: { value: 'viewer' } });
    fireEvent.click(screen.getByRole('button', { name: /Add mapping/ }));
    await waitFor(() => expect(api.createGroupMapping).toHaveBeenCalledWith({
      group_name: 'devs', target: 'workspace', role: 'viewer', workspace: 'default',
    }));
  });

  it('creates a group', async () => {
    show();
    await screen.findByTestId('group-count-devs');
    fireEvent.change(screen.getByLabelText('Name', { selector: 'input' }), { target: { value: 'qa' } });
    fireEvent.click(screen.getByRole('button', { name: /Add group/ }));
    await waitFor(() => expect(api.createGroup).toHaveBeenCalledWith({ name: 'qa', display_name: '' }));
  });

  it('makes an account with a first and last name and English unless another language is picked', async () => {
    api.createUser.mockImplementation(() => ok({}));
    show();
    await screen.findByTestId('group-count-devs');
    fireEvent.change(screen.getByLabelText('User name'), { target: { value: 'anna' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret-pass-1' } });
    fireEvent.change(screen.getByLabelText('First name'), { target: { value: 'Anna' } });
    fireEvent.change(screen.getByLabelText('Last name'), { target: { value: 'Petrova' } });
    expect(screen.getByLabelText('Language').value).toBe('en');
    fireEvent.click(screen.getByRole('button', { name: /Add account/ }));
    await waitFor(() => expect(api.createUser).toHaveBeenCalledWith(expect.objectContaining({
      username: 'anna', first_name: 'Anna', last_name: 'Petrova', language: 'en',
    })));
  });

  it('edits an account\'s names and language in a window', async () => {
    api.updateUser.mockImplementation(() => ok({}));
    show();
    fireEvent.click(await screen.findByTestId('edit-alice'));
    const modal = screen.getByTestId('edit-user-modal');
    fireEvent.change(within(modal).getByLabelText('First name'), { target: { value: 'Alice' } });
    fireEvent.change(within(modal).getByLabelText('Last name'), { target: { value: 'Smith' } });
    fireEvent.change(within(modal).getByLabelText('Language'), { target: { value: 'de' } });
    fireEvent.click(within(modal).getByTestId('edit-user-save'));
    await waitFor(() => expect(api.updateUser).toHaveBeenCalledWith('u2', {
      first_name: 'Alice', last_name: 'Smith', email: 'alice@example.com', language: 'de',
    }));
    await waitFor(() => expect(screen.queryByTestId('edit-user-modal')).toBeNull());
  });
});
