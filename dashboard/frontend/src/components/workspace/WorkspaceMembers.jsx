import React, { useCallback, useEffect, useState } from 'react';
import { Loader, ShieldCheck, UserPlus, Users, X } from 'lucide-react';
import {
  getUsers, getWorkspaceMembers, removeWorkspaceMember, setWorkspaceMember,
} from '../../api';
import { MULTI, useAuth } from '../auth';
import { useI18n } from '../../i18n';

const selectCls = 'border border-gray-200 rounded-lg px-2 py-1 text-xs '
  + 'focus:ring-2 focus:ring-indigo-500 focus:outline-none';

/**
 * Who may reach this workspace, and with what power.
 *
 * Renders nothing outside `multi` mode, and nothing for a viewer the backend
 * will not answer for: membership is owner-or-admin territory, so a member
 * without that standing gets no card rather than an error where a card should
 * be. The first failed load is what settles it, because "am I an owner here"
 * is a question only the backend can answer.
 */
export default function WorkspaceMembers({ workspace }) {
  const { t } = useI18n();
  const { mode } = useAuth();
  const [members, setMembers] = useState([]);
  const [users, setUsers] = useState([]);
  const [visible, setVisible] = useState(mode === MULTI);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState({ user_id: '', role: 'viewer' });

  const load = useCallback(async () => {
    if (mode !== MULTI || !workspace) return;
    setLoading(true);
    try {
      const { data } = await getWorkspaceMembers(workspace);
      setMembers(Array.isArray(data) ? data : []);
      setVisible(true);
      setError('');
    } catch (err) {
      // 403/404: not this viewer's card to see. Anything else is a real error
      // worth showing, since the card is already on screen by then.
      const status = err?.response?.status;
      if (status === 403 || status === 404) setVisible(false);
      else setError(err?.response?.data?.detail || t('auth.members.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [mode, workspace, t]);

  useEffect(() => { load(); }, [load]);

  // The picker needs every account, which only an admin may list. An owner who
  // is not an admin adds members by typing an id instead, so a failure here
  // leaves the card working rather than breaking it.
  useEffect(() => {
    if (!visible) return;
    getUsers().then(({ data }) => setUsers(Array.isArray(data) ? data : []))
      .catch(() => setUsers([]));
  }, [visible]);

  if (mode !== MULTI || !visible) return null;

  const add = async (event) => {
    event.preventDefault();
    try {
      await setWorkspaceMember(workspace, draft);
      setDraft({ user_id: '', role: 'viewer' });
      setError('');
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('auth.members.addFailed'));
    }
  };

  const changeRole = async (member, role) => {
    try {
      await setWorkspaceMember(workspace, { user_id: member.id, role });
      setError('');
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('auth.members.updateFailed'));
    }
  };

  const remove = async (member) => {
    try {
      await removeWorkspaceMember(workspace, member.id);
      setError('');
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('auth.members.removeFailed'));
    }
  };

  const known = new Set(members.map((m) => m.id));
  const candidates = users.filter((u) => !known.has(u.id));

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6">
      <h3 className="text-lg font-bold mb-1 flex items-center gap-2">
        <Users className="w-5 h-5 text-indigo-600" />
        {t('auth.members.title')}
      </h3>
      <p className="text-sm text-gray-500 mb-4">{t('auth.members.description')}</p>

      {error && <p className="mb-3 text-xs text-red-600">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : (
        <ul className="divide-y divide-gray-100 mb-4">
          {members.length === 0 && (
            <li className="py-2 text-sm text-gray-500">{t('auth.members.empty')}</li>
          )}
          {members.map((member) => (
            <li key={member.id} className="py-2 flex items-center gap-3">
              <span className="text-sm font-medium text-gray-800 flex-1">
                {member.username}
                {member.role === 'admin' && (
                  <ShieldCheck className="inline w-3.5 h-3.5 ml-1.5 text-indigo-500" />
                )}
                {member.source === 'group' && (
                  <span title={t('auth.sso.viaGroupHint')}
                    className="ml-2 inline-flex items-center rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700">
                    {t('auth.sso.viaGroup')}
                  </span>
                )}
              </span>
              {/* A role granted by a group mapping follows the group, so it is
                  changed through the mapping, not overridden member by member. */}
              <select value={member.workspace_role} className={selectCls}
                disabled={member.source === 'group'}
                title={member.source === 'group' ? t('auth.sso.viaGroupHint') : undefined}
                aria-label={t('auth.fields.role')}
                onChange={(e) => changeRole(member, e.target.value)}>
                <option value="viewer">{t('auth.workspaceRoles.viewer')}</option>
                <option value="editor">{t('auth.workspaceRoles.editor')}</option>
                <option value="owner">{t('auth.workspaceRoles.owner')}</option>
              </select>
              <button type="button" onClick={() => remove(member)}
                title={t('common.remove')}
                className="text-gray-400 hover:text-red-600">
                <X className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={add} className="flex flex-wrap items-center gap-2">
        {candidates.length > 0 ? (
          <select value={draft.user_id} className={selectCls}
            onChange={(e) => setDraft({ ...draft, user_id: e.target.value })}>
            <option value="">{t('auth.members.pickUser')}</option>
            {candidates.map((u) => (
              <option key={u.id} value={u.id}>{u.username}</option>
            ))}
          </select>
        ) : (
          <input value={draft.user_id} placeholder={t('auth.members.userIdPlaceholder')}
            onChange={(e) => setDraft({ ...draft, user_id: e.target.value })}
            className={selectCls} />
        )}
        <select value={draft.role} className={selectCls}
          onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
          <option value="viewer">{t('auth.workspaceRoles.viewer')}</option>
          <option value="editor">{t('auth.workspaceRoles.editor')}</option>
          <option value="owner">{t('auth.workspaceRoles.owner')}</option>
        </select>
        <button type="submit" disabled={!draft.user_id}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50">
          <UserPlus className="w-3.5 h-3.5" /> {t('auth.members.add')}
        </button>
      </form>
    </div>
  );
}
