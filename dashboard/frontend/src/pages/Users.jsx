import React, { useCallback, useEffect, useState } from 'react';
import {
  KeyRound, Loader, Plus, RefreshCw, ShieldCheck, Trash2, UserPlus, Users as UsersIcon,
} from 'lucide-react';
import {
  createUser, deleteUser, getUsers, resetUserPassword, updateUser,
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
 * administrator, and nothing else.
 */
export default function Users() {
  const { t } = useI18n();
  const { user: me } = useAuth();
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
                    <button type="button" onClick={() => resetPassword(user)}
                      className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 mr-3">
                      <KeyRound className="w-3.5 h-3.5" /> {t('auth.users.resetPassword')}
                    </button>
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
    </PageContainer>
  );
}
