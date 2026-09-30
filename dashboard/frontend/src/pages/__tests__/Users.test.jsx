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
}));

vi.mock('../../api', () => ({
  ...Object.fromEntries(Object.keys(api).map((k) => [k, (...a) => api[k](...a)])),
  createUser: vi.fn(),
  deleteUser: vi.fn(),
  updateUser: vi.fn(),
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
});
