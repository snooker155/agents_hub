import React, { useCallback, useEffect, useState } from 'react';
import {
  KeyRound, Link2, Loader, Plus, RefreshCw, ShieldCheck, Trash2, UserPlus, Users as UsersIcon,
  UsersRound,
} from 'lucide-react';
import {
  createGroup, createGroupMapping, createUser, deleteGroup, deleteGroupMapping, deleteUser,
  getGroupMappings, getGroupMembers, getGroups, getUsers, getWorkspaces, resetUserPassword,
  setGroupMembers, updateUser,
} from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useAuth } from '../components/auth';
import { useI18n } from '../i18n';

const inputCls = 'border border-gray-200 rounded-lg px-3 py-2 text-sm '
  + 'focus:ring-2 focus:ring-indigo-500 focus:outline-none';

/**
 * Accounts: who can log in, and what they may do globally.
 *
 * Only reachable in `multi` mode and only for administrators; App.jsx keeps
 * the route off the table otherwise, so this page never has to render an
 * "access denied" of its own.
 *
 * Per-workspace access is deliberately not here: it belongs to the workspace,
 * and lives on the workspace's own page (`WorkspaceMembers`). This page
 * answers the two questions that are global, who exists and who is an
 * administrator, and nothing else. Below the accounts sit the groups and the
 * rules that turn a group into access (`GroupsSection`), because granting
 * through a group is the same global question asked of many people at once.
 */
export default function Users() {
  const { t } = useI18n();
  const { user: me, features } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState({ username: '', password: '', role: 'member' });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getUsers();
      setUsers(Array.isArray(data) ? data : []);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('auth.users.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const fail = (err, fallbackKey) => setError(
    err?.response?.data?.detail || t(fallbackKey),
  );

  const add = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await createUser(draft);
      setDraft({ username: '', password: '', role: 'member' });
      setError('');
      await load();
    } catch (err) {
      fail(err, 'auth.users.createFailed');
    } finally {
      setBusy(false);
    }
  };

  const changeRole = async (user, role) => {
    try {
      await updateUser(user.id, { role });
      setError('');
      await load();
    } catch (err) {
      fail(err, 'auth.users.updateFailed');
    }
  };

  const remove = async (user) => {
    if (!window.confirm(t('auth.users.confirmDelete', { name: user.username }))) return;
    try {
      await deleteUser(user.id);
      setError('');
      await load();
    } catch (err) {
      fail(err, 'auth.users.deleteFailed');
    }
  };

  const resetPassword = async (user) => {
    const password = window.prompt(t('auth.users.newPasswordFor', { name: user.username }));
    if (!password) return;
    try {
      await resetUserPassword(user.id, password);
      setError('');
    } catch (err) {
      fail(err, 'auth.users.passwordFailed');
    }
  };

  return (
    <PageContainer>
      <PageHeader
        icon={UsersIcon}
        title={t('auth.users.title')}
        description={t('auth.users.description')}
        actions={(
          <button type="button" onClick={load}
            className="flex items-center gap-1.5 border border-gray-200 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700">
            <RefreshCw className="w-3.5 h-3.5" /> {t('common.refresh')}
          </button>
        )}
      />

      {error && (
        <p className="mb-4 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      <form onSubmit={add} className="mb-6 bg-white border border-gray-200 rounded-xl p-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="new-username" className="block text-xs font-medium text-gray-500 mb-1">
            {t('auth.fields.username')}
          </label>
          <input id="new-username" value={draft.username} autoComplete="off"
            onChange={(e) => setDraft({ ...draft, username: e.target.value })} className={inputCls} />
        </div>
        <div>
          <label htmlFor="new-password" className="block text-xs font-medium text-gray-500 mb-1">
            {t('auth.fields.password')}
          </label>
          <input id="new-password" type="password" value={draft.password} autoComplete="new-password"
            onChange={(e) => setDraft({ ...draft, password: e.target.value })} className={inputCls} />
        </div>
        <div>
          <label htmlFor="new-role" className="block text-xs font-medium text-gray-500 mb-1">
            {t('auth.fields.role')}
          </label>
          <select id="new-role" value={draft.role}
            onChange={(e) => setDraft({ ...draft, role: e.target.value })} className={inputCls}>
            <option value="member">{t('auth.roles.member')}</option>
            <option value="admin">{t('auth.roles.admin')}</option>
          </select>
        </div>
        <button type="submit" disabled={busy || !draft.username || !draft.password}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
          {busy ? <Loader className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
          {t('auth.users.add')}
        </button>
      </form>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {loading ? (
          <p className="p-6 text-sm text-gray-500 flex items-center gap-2">
            <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
          </p>
        ) : users.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">{t('auth.users.empty')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-400">
              <tr>
                <th className="text-left px-4 py-2 font-semibold">{t('auth.fields.username')}</th>
                <th className="text-left px-4 py-2 font-semibold">{t('auth.sso.email')}</th>
                <th className="text-left px-4 py-2 font-semibold">{t('auth.sso.sourceLabel')}</th>
                <th className="text-left px-4 py-2 font-semibold">{t('auth.fields.role')}</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className="border-t border-gray-100">
                  <td className="px-4 py-2">
                    <span className="font-medium text-gray-800">{user.username}</span>
                    {user.id === me?.id && (
                      <span className="ml-2 text-xs text-indigo-600">{t('auth.users.you')}</span>
                    )}
                    {user.display_name && user.display_name !== user.username && (
                      <span className="block text-xs text-gray-500">{user.display_name}</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">{user.email || ''}</td>
                  <td className="px-4 py-2">
                    <SourceBadge source={user.source} t={t} />
                    {!user.has_password && (
                      <span className="ml-2 text-[11px] text-gray-400">{t('auth.sso.noPassword')}</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <select value={user.role} onChange={(e) => changeRole(user, e.target.value)}
                      className="border border-gray-200 rounded-lg px-2 py-1 text-xs">
                      <option value="member">{t('auth.roles.member')}</option>
                      <option value="admin">{t('auth.roles.admin')}</option>
                    </select>
                    {user.role === 'admin' && (
                      <ShieldCheck className="inline w-3.5 h-3.5 ml-1.5 text-indigo-500" />
                    )}
                  </td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    {canUsePassword(user) && (
                      <button type="button" onClick={() => resetPassword(user)}
                        className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 mr-3">
                        <KeyRound className="w-3.5 h-3.5" /> {t('auth.users.resetPassword')}
                      </button>
                    )}
                    <button type="button" onClick={() => remove(user)}
                      className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-700">
                      <Trash2 className="w-3.5 h-3.5" /> {t('common.delete')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="mt-3 text-xs text-gray-500 flex items-center gap-1.5">
        <Plus className="w-3 h-3" /> {t('auth.users.membershipHint')}
      </p>

      {features?.groups !== false && <GroupsAndMappings users={users} />}
    </PageContainer>
  );
}

// A local account signs in with a password. One from SSO or SCIM does only
// when it already has one (a linked local account, or one an administrator
// set as the emergency door); offering to "reset" a password it never had
// would quietly give an SSO account a second way in.
const canUsePassword = (user) => (user.source || 'local') === 'local' || Boolean(user.has_password);

const SOURCE_STYLES = {
  local: 'bg-gray-100 text-gray-700',
  oidc: 'bg-indigo-50 text-indigo-700',
  scim: 'bg-emerald-50 text-emerald-700',
};

function SourceBadge({ source, t }) {
  const key = SOURCE_STYLES[source] ? source : 'local';
  return (
    <span data-testid="account-source"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${SOURCE_STYLES[key]}`}>
      {t(`auth.sso.source.${key}`)}
    </span>
  );
}

const cardCls = 'bg-white border border-gray-200 rounded-xl overflow-hidden';
const sectionTitleCls = 'text-base font-semibold text-gray-900 flex items-center gap-2';

/**
 * Groups, and the rules that turn a group into access (routes/groups.py).
 *
 * One component for both sections because they share their data: the
 * mapping form offers the known group names, and a change to either can
 * change what the other shows (deleting a group drops what it granted).
 */
function GroupsAndMappings({ users }) {
  const { t } = useI18n();
  const [groups, setGroups] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [g, m] = await Promise.all([getGroups(), getGroupMappings()]);
      setGroups(Array.isArray(g.data) ? g.data : []);
      setMappings(Array.isArray(m.data) ? m.data : []);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('groups.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    getWorkspaces()
      .then(({ data }) => setWorkspaces((Array.isArray(data) ? data : [])
        .map((w) => w?.name || w).filter((w) => typeof w === 'string' && w)))
      .catch(() => setWorkspaces([]));
  }, []);

  const fail = (err, key) => setError(err?.response?.data?.detail || t(key));

  return (
    <>
      {error && (
        <p className="mt-8 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
          {error}
        </p>
      )}
      <GroupsSection groups={groups} users={users} loading={loading} reload={load} fail={fail}
        clearError={() => setError('')} />
      <MappingsSection groups={groups} mappings={mappings} workspaces={workspaces}
        loading={loading} reload={load} fail={fail} clearError={() => setError('')} />
    </>
  );
}

function GroupsSection({ groups, users, loading, reload, fail, clearError }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState({ name: '', display_name: '' });
  const [editing, setEditing] = useState(null); // { id, selected: Set }
  const [busy, setBusy] = useState(false);

  const add = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await createGroup(draft);
      setDraft({ name: '', display_name: '' });
      clearError();
      await reload();
    } catch (err) {
      fail(err, 'groups.createFailed');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (group) => {
    if (!window.confirm(t('groups.confirmDelete', { name: group.display_name || group.name }))) return;
    try {
      await deleteGroup(group.id);
      clearError();
      await reload();
    } catch (err) {
      fail(err, 'groups.deleteFailed');
    }
  };

  const startEdit = async (group) => {
    try {
      const { data } = await getGroupMembers(group.id);
      setEditing({ id: group.id, selected: new Set((data || []).map((m) => m.id)) });
    } catch (err) {
      fail(err, 'groups.loadFailed');
    }
  };

  const toggle = (userId) => setEditing((prev) => {
    const selected = new Set(prev.selected);
    if (selected.has(userId)) selected.delete(userId);
    else selected.add(userId);
    return { ...prev, selected };
  });

  const saveMembers = async () => {
    setBusy(true);
    try {
      await setGroupMembers(editing.id, [...editing.selected]);
      setEditing(null);
      clearError();
      await reload();
    } catch (err) {
      fail(err, 'groups.membersFailed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-8" aria-labelledby="groups-title">
      <h2 id="groups-title" className={sectionTitleCls}>
        <UsersRound className="w-4 h-4 text-indigo-600" /> {t('groups.title')}
      </h2>
      <p className="text-xs text-gray-500 mt-1 mb-3">{t('groups.description')}</p>

      <form onSubmit={add} className="mb-3 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="new-group-name" className="block text-xs font-medium text-gray-500 mb-1">
            {t('groups.name')}
          </label>
          <input id="new-group-name" value={draft.name} autoComplete="off"
            onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={inputCls} />
        </div>
        <div>
          <label htmlFor="new-group-display" className="block text-xs font-medium text-gray-500 mb-1">
            {t('groups.displayName')}
          </label>
          <input id="new-group-display" value={draft.display_name} autoComplete="off"
            onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} className={inputCls} />
        </div>
        <button type="submit" disabled={busy || !draft.name.trim()}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
          <Plus className="w-4 h-4" /> {t('groups.add')}
        </button>
      </form>

      <div className={cardCls}>
        {loading ? (
          <p className="p-4 text-sm text-gray-500 flex items-center gap-2">
            <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
          </p>
        ) : groups.length === 0 ? (
          <p className="p-4 text-sm text-gray-500">{t('groups.empty')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-400">
              <tr>
                <th className="text-left px-4 py-2 font-semibold">{t('groups.name')}</th>
                <th className="text-left px-4 py-2 font-semibold">{t('groups.source')}</th>
                <th className="text-left px-4 py-2 font-semibold">{t('groups.members')}</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <React.Fragment key={group.id}>
                  <tr className="border-t border-gray-100">
                    <td className="px-4 py-2">
                      <span className="font-medium text-gray-800">{group.display_name || group.name}</span>
                      {group.display_name && group.display_name !== group.name && (
                        <span className="ml-2 text-xs text-gray-400 font-mono">{group.name}</span>
                      )}
                    </td>
                    <td className="px-4 py-2"><SourceBadge source={group.source === 'manual' ? 'local' : group.source} t={t} /></td>
                    <td className="px-4 py-2 text-gray-600" data-testid={`group-count-${group.name}`}>
                      {group.member_count ?? 0}
                    </td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      <button type="button" onClick={() => startEdit(group)}
                        className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 mr-3">
                        <UsersIcon className="w-3.5 h-3.5" /> {t('groups.editMembers')}
                      </button>
                      <button type="button" onClick={() => remove(group)}
                        aria-label={`${t('common.delete')} ${group.name}`}
                        className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-700">
                        <Trash2 className="w-3.5 h-3.5" /> {t('common.delete')}
                      </button>
                    </td>
                  </tr>
                  {editing?.id === group.id && (
                    <tr className="bg-gray-50/60">
                      <td colSpan={4} className="px-4 py-3">
                        {group.source !== 'manual' && (
                          <p className="mb-2 text-xs text-amber-700">{t('groups.providerManaged')}</p>
                        )}
                        <fieldset className="flex flex-wrap gap-x-4 gap-y-1.5" aria-label={t('groups.members')}>
                          {users.map((user) => (
                            <label key={user.id} className="inline-flex items-center gap-1.5 text-sm text-gray-700">
                              <input type="checkbox" checked={editing.selected.has(user.id)}
                                onChange={() => toggle(user.id)} />
                              {user.username}
                            </label>
                          ))}
                        </fieldset>
                        <div className="mt-3 flex gap-2">
                          <button type="button" onClick={saveMembers} disabled={busy}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50">
                            {t('groups.saveMembers')}
                          </button>
                          <button type="button" onClick={() => setEditing(null)}
                            className="border border-gray-200 hover:bg-white px-3 py-1.5 rounded-lg text-xs font-medium text-gray-700">
                            {t('groups.cancel')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

const EMPTY_MAPPING = { group_name: '', target: 'role', role: 'admin', workspace: '' };

function MappingsSection({ groups, mappings, workspaces, loading, reload, fail, clearError }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(EMPTY_MAPPING);
  const [busy, setBusy] = useState(false);

  const setTarget = (target) => setDraft({
    ...draft, target, role: target === 'role' ? 'admin' : 'editor',
  });

  const add = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      const payload = { group_name: draft.group_name.trim(), target: draft.target, role: draft.role };
      if (draft.target === 'workspace') payload.workspace = draft.workspace;
      await createGroupMapping(payload);
      setDraft(EMPTY_MAPPING);
      clearError();
      await reload();
    } catch (err) {
      fail(err, 'groups.mappings.createFailed');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (mapping) => {
    try {
      await deleteGroupMapping(mapping.id);
      clearError();
      await reload();
    } catch (err) {
      fail(err, 'groups.mappings.deleteFailed');
    }
  };

  const grantText = (m) => (m.target === 'role'
    ? t('groups.mappings.grantRole', { role: t(`auth.roles.${m.role}`) })
    : t('groups.mappings.grantWorkspace', {
      role: t(`auth.workspaceRoles.${m.role}`), workspace: m.workspace,
    }));

  const ready = draft.group_name.trim() && (draft.target === 'role' || draft.workspace);

  return (
    <section className="mt-8" aria-labelledby="mappings-title">
      <h2 id="mappings-title" className={sectionTitleCls}>
        <Link2 className="w-4 h-4 text-indigo-600" /> {t('groups.mappings.title')}
      </h2>
      <p className="text-xs text-gray-500 mt-1 mb-3">{t('groups.mappings.description')}</p>

      <div className={`${cardCls} mb-3`}>
        {loading ? (
          <p className="p-4 text-sm text-gray-500 flex items-center gap-2">
            <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
          </p>
        ) : mappings.length === 0 ? (
          <p className="p-4 text-sm text-gray-500">{t('groups.mappings.empty')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-400">
              <tr>
                <th className="text-left px-4 py-2 font-semibold">{t('groups.mappings.group')}</th>
                <th className="text-left px-4 py-2 font-semibold">{t('groups.mappings.grants')}</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {mappings.map((m) => (
                <tr key={m.id} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-mono text-xs text-gray-800">{m.group_name}</td>
                  <td className="px-4 py-2 text-gray-700">{grantText(m)}</td>
                  <td className="px-4 py-2 text-right">
                    <button type="button" onClick={() => remove(m)}
                      aria-label={`${t('common.delete')} ${m.group_name}`}
                      className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-700">
                      <Trash2 className="w-3.5 h-3.5" /> {t('common.delete')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <form onSubmit={add} className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="mapping-group" className="block text-xs font-medium text-gray-500 mb-1">
            {t('groups.mappings.group')}
          </label>
          <input id="mapping-group" list="mapping-group-names" value={draft.group_name} autoComplete="off"
            onChange={(e) => setDraft({ ...draft, group_name: e.target.value })} className={inputCls} />
          <datalist id="mapping-group-names">
            {groups.map((g) => <option key={g.id} value={g.name} />)}
          </datalist>
        </div>
        <div>
          <label htmlFor="mapping-target" className="block text-xs font-medium text-gray-500 mb-1">
            {t('groups.mappings.target')}
          </label>
          <select id="mapping-target" value={draft.target} onChange={(e) => setTarget(e.target.value)}
            className={inputCls}>
            <option value="role">{t('groups.mappings.targetRole')}</option>
            <option value="workspace">{t('groups.mappings.targetWorkspace')}</option>
          </select>
        </div>
        <div>
          <label htmlFor="mapping-role" className="block text-xs font-medium text-gray-500 mb-1">
            {t('groups.mappings.role')}
          </label>
          <select id="mapping-role" value={draft.role}
            onChange={(e) => setDraft({ ...draft, role: e.target.value })} className={inputCls}>
            {draft.target === 'role' ? (
              <>
                <option value="admin">{t('auth.roles.admin')}</option>
                <option value="member">{t('auth.roles.member')}</option>
              </>
            ) : (
              <>
                <option value="viewer">{t('auth.workspaceRoles.viewer')}</option>
                <option value="editor">{t('auth.workspaceRoles.editor')}</option>
                <option value="owner">{t('auth.workspaceRoles.owner')}</option>
              </>
            )}
          </select>
        </div>
        {draft.target === 'workspace' && (
          <div>
            <label htmlFor="mapping-workspace" className="block text-xs font-medium text-gray-500 mb-1">
              {t('groups.mappings.workspace')}
            </label>
            <select id="mapping-workspace" value={draft.workspace}
              onChange={(e) => setDraft({ ...draft, workspace: e.target.value })} className={inputCls}>
              <option value="">{t('groups.mappings.pickWorkspace')}</option>
              {workspaces.map((w) => <option key={w} value={w}>{w}</option>)}
            </select>
          </div>
        )}
        <button type="submit" disabled={busy || !ready}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
          <Plus className="w-4 h-4" /> {t('groups.mappings.add')}
        </button>
      </form>
    </section>
  );
}
