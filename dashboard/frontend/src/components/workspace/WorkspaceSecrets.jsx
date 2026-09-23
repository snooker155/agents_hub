import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, Loader, Plus, Trash2 } from 'lucide-react';
import {
  deleteWorkspaceSecret, getUsers, getWorkspaceSecrets, setWorkspaceSecret,
} from '../../api';
import { MULTI, useAuth } from '../auth';
import { useI18n } from '../../i18n';

const inputCls = 'border border-gray-200 rounded-lg px-2 py-1 text-xs '
  + 'focus:ring-2 focus:ring-indigo-500 focus:outline-none';

// Mirrors common/secrets.NAME_RE: a secret becomes an environment variable.
const NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

const EMPTY_DRAFT = { name: '', value: '', agent_id: '', user_id: '' };

/**
 * The workspace's secrets: names, scopes and a hint, never a value.
 *
 * Unlike the members card this renders in every mode, because one operator
 * has keys to keep out of `.env` too. In `multi` mode the backend answers only
 * the workspace's owner or an admin, so a 403 or 404 on the first load hides
 * the card rather than showing an error where a card should be.
 *
 * `agents` is the workspace's allowed agent ids, for the optional agent scope.
 * The user scope is offered only in `multi` mode, from the accounts list an
 * admin may read; anyone else can still type an id.
 */
export default function WorkspaceSecrets({ workspace, agents = [] }) {
  const { t } = useI18n();
  const { mode } = useAuth();
  const multi = mode === MULTI;
  const [rows, setRows] = useState([]);
  const [users, setUsers] = useState([]);
  const [visible, setVisible] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [noKey, setNoKey] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DRAFT);

  const load = useCallback(async () => {
    if (!workspace) return;
    setLoading(true);
    try {
      const { data } = await getWorkspaceSecrets(workspace);
      setRows(Array.isArray(data) ? data : []);
      setVisible(true);
      setError('');
    } catch (err) {
      const status = err?.response?.status;
      if (status === 403 || status === 404) setVisible(false);
      else setError(err?.response?.data?.detail || t('secrets.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [workspace, t]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!multi || !visible) return;
    getUsers().then(({ data }) => setUsers(Array.isArray(data) ? data : []))
      .catch(() => setUsers([]));
  }, [multi, visible]);

  if (!visible) return null;

  const userName = (id) => users.find((u) => u.id === id)?.username || id;

  const scopeLabel = (row) => {
    if (row.agent_id && row.user_id) {
      return t('secrets.scope.agentUser', { agent: row.agent_id, user: userName(row.user_id) });
    }
    if (row.agent_id) return t('secrets.scope.agent', { agent: row.agent_id });
    if (row.user_id) return t('secrets.scope.user', { user: userName(row.user_id) });
    return t('secrets.scope.workspace');
  };

  const save = async (event) => {
    event.preventDefault();
    const body = { value: draft.value };
    if (draft.agent_id) body.agent_id = draft.agent_id;
    if (draft.user_id) body.user_id = draft.user_id;
    try {
      await setWorkspaceSecret(workspace, draft.name, body);
      setDraft(EMPTY_DRAFT);
      setError('');
      setNoKey(false);
      await load();
    } catch (err) {
      const detail = err?.response?.data?.detail || '';
      // The backend's refusal when AGENTS_HUB_SECRET_KEY is empty names the
      // variable; show the full explanation instead of a bare error line.
      if (err?.response?.status === 400 && detail.includes('AGENTS_HUB_SECRET_KEY')) {
        setNoKey(true);
        setError('');
      } else {
        setError(detail || t('secrets.saveFailed'));
      }
    }
  };

  const remove = async (row) => {
    const params = {};
    if (row.agent_id) params.agent_id = row.agent_id;
    if (row.user_id) params.user_id = row.user_id;
    try {
      await deleteWorkspaceSecret(workspace, row.name, params);
      setError('');
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('secrets.deleteFailed'));
    }
  };

  const nameOk = NAME_RE.test(draft.name);

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6">
      <h3 className="text-lg font-bold mb-1 flex items-center gap-2">
        <KeyRound className="w-5 h-5 text-indigo-600" />
        {t('secrets.title')}
      </h3>
      <p className="text-sm text-gray-500 mb-4">{t('secrets.description')}</p>

      {noKey && (
        <p role="alert" className="mb-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          {t('secrets.noKey')}
        </p>
      )}
      {error && <p className="mb-3 text-xs text-red-600">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-500 mb-4">{t('secrets.empty')}</p>
      ) : (
        <div className="overflow-x-auto mb-4">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
                <th className="py-1.5 pr-3 font-medium">{t('secrets.columns.name')}</th>
                <th className="py-1.5 pr-3 font-medium">{t('secrets.columns.scope')}</th>
                <th className="py-1.5 pr-3 font-medium">{t('secrets.columns.hint')}</th>
                <th className="py-1.5 pr-3 font-medium">{t('secrets.columns.updated')}</th>
                <th className="py-1.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row) => (
                <tr key={`${row.name}|${row.agent_id}|${row.user_id}`}>
                  <td className="py-1.5 pr-3 font-mono text-xs text-gray-800">{row.name}</td>
                  <td className="py-1.5 pr-3 text-xs text-gray-600">{scopeLabel(row)}</td>
                  <td className="py-1.5 pr-3 font-mono text-xs text-gray-500">
                    {row.hint && row.hint !== '****' ? `••••${row.hint}` : '••••'}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-gray-500">
                    {row.updated_at ? String(row.updated_at).slice(0, 16).replace('T', ' ') : ''}
                  </td>
                  <td className="py-1.5 text-right">
                    <button type="button" onClick={() => remove(row)}
                      title={t('secrets.delete')} aria-label={`${t('secrets.delete')} ${row.name}`}
                      className="text-gray-400 hover:text-red-600">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form onSubmit={save} className="flex flex-wrap items-center gap-2">
        <input value={draft.name} placeholder={t('secrets.fields.name')}
          aria-label={t('secrets.columns.name')} title={t('secrets.nameHint')}
          onChange={(e) => setDraft({ ...draft, name: e.target.value.toUpperCase() })}
          className={`${inputCls} font-mono`} />
        <input type="password" autoComplete="new-password" value={draft.value}
          placeholder={t('secrets.fields.value')} aria-label={t('secrets.fields.value')}
          onChange={(e) => setDraft({ ...draft, value: e.target.value })}
          className={inputCls} />
        <select value={draft.agent_id} className={inputCls} aria-label={t('secrets.fields.anyAgent')}
          onChange={(e) => setDraft({ ...draft, agent_id: e.target.value })}>
          <option value="">{t('secrets.fields.anyAgent')}</option>
          {agents.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
        {multi && (users.length > 0 ? (
          <select value={draft.user_id} className={inputCls} aria-label={t('secrets.fields.anyUser')}
            onChange={(e) => setDraft({ ...draft, user_id: e.target.value })}>
            <option value="">{t('secrets.fields.anyUser')}</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
          </select>
        ) : (
          <input value={draft.user_id} placeholder={t('secrets.fields.anyUser')}
            aria-label={t('secrets.fields.anyUser')}
            onChange={(e) => setDraft({ ...draft, user_id: e.target.value })}
            className={inputCls} />
        ))}
        <button type="submit" disabled={!nameOk || !draft.value}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50">
          <Plus className="w-3.5 h-3.5" /> {t('secrets.add')}
        </button>
      </form>
    </div>
  );
}
