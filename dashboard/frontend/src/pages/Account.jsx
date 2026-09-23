import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, Check, Copy, KeyRound, Laptop, Loader, Lock, LogOut, Plus, RefreshCw,
  Trash2, User as UserIcon,
} from 'lucide-react';
import {
  changeMyPassword, createMyApiKey, getMyApiKeys, getMySessions, getWorkspaces,
  revokeMyApiKey, revokeMySession, revokeOtherSessions,
} from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { SectionCard, inputCls } from '../components/settingsUi';
import { useAuth } from '../components/auth';
import { useFormatters, useI18n } from '../i18n';

const btnPrimary = 'flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white '
  + 'px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';
const btnGhost = 'flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 '
  + 'rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50';
const btnDanger = 'inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-700 disabled:opacity-50';

/**
 * The signed-in person's own account: sessions and personal API keys
 * (routes/account.py, docs/api-keys.md). App.jsx keeps this route off the
 * table outside `multi` mode, so nothing here has to branch on the mode.
 *
 * Deliberately not the accounts page (`Users.jsx`, an administrator's view of
 * everybody): every call here is "my own", never named by an id an admin
 * picked, which is what keeps the backend routes 404-safe for anyone who is
 * authenticated but is not a person (a service credential, a shared token).
 */
export default function Account() {
  const { t } = useI18n();
  const { user, features, logout } = useAuth();
  const navigate = useNavigate();

  const onPasswordChanged = useCallback(async () => {
    // set_password drops every session, this one included: the browser has
    // to be told to go log in again rather than let the next click surface a
    // bare, unexplained 401.
    await logout();
    navigate('/login');
  }, [logout, navigate]);

  return (
    <PageContainer width="narrow">
      <PageHeader icon={UserIcon} title={t('account.title')} description={t('account.description')} />
      <div className="space-y-6">
        <ProfileSection user={user} t={t} />
        <SessionsSection t={t} />
        {user?.has_password && features?.local_passwords && (
          <PasswordSection t={t} onChanged={onPasswordChanged} />
        )}
        <ApiKeysSection t={t} isAdmin={user?.role === 'admin'} />
      </div>
    </PageContainer>
  );
}

// ── profile ──────────────────────────────────────────────────────────────────

function ProfileSection({ user, t }) {
  if (!user) return null;
  const groups = Array.isArray(user.groups) ? user.groups : [];
  const workspaces = Array.isArray(user.workspaces) ? user.workspaces : [];
  return (
    <SectionCard title={t('account.profile.title')}>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <Field label={t('account.profile.username')} value={user.username} />
        <Field label={t('account.profile.displayName')} value={user.display_name} />
        <Field label={t('account.profile.email')} value={user.email || '—'} />
        <Field label={t('account.profile.source')}
          value={t(`account.profile.sources.${user.source || 'local'}`, { defaultValue: user.source })} />
        <Field label={t('account.profile.role')} value={t(`auth.roles.${user.role}`, { defaultValue: user.role })} />
        <Field label={t('account.profile.groups')}
          value={groups.length ? groups.join(', ') : t('account.profile.noGroups')} />
        <div className="sm:col-span-2">
          <dt className="text-xs font-medium text-gray-500">{t('account.profile.workspaces')}</dt>
          <dd className="mt-0.5 text-gray-800">
            {workspaces.length
              ? workspaces.map((w) => (
                <span key={w} className="inline-block bg-gray-100 rounded px-2 py-0.5 text-xs mr-1.5 mb-1">{w}</span>
              ))
              : t('account.profile.noWorkspaces')}
          </dd>
        </div>
      </dl>
    </SectionCard>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-gray-800">{value || '—'}</dd>
    </div>
  );
}

// ── sessions ─────────────────────────────────────────────────────────────────

function SessionsSection({ t }) {
  const { formatDate } = useFormatters();
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [revokingOthers, setRevokingOthers] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getMySessions();
      setSessions(Array.isArray(data) ? data : []);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.sessions.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const revoke = async (session) => {
    setBusyId(session.id);
    try {
      await revokeMySession(session.id);
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.sessions.revokeFailed'));
    } finally {
      setBusyId('');
    }
  };

  const revokeOthers = async () => {
    if (!window.confirm(t('account.sessions.revokeOthersConfirm'))) return;
    setRevokingOthers(true);
    try {
      await revokeOtherSessions();
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.sessions.revokeFailed'));
    } finally {
      setRevokingOthers(false);
    }
  };

  return (
    <SectionCard
      title={t('account.sessions.title')}
      actions={(
        <button type="button" onClick={revokeOthers} disabled={revokingOthers || sessions.length < 2}
          className={btnGhost}>
          {revokingOthers ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
          {t('account.sessions.revokeOthers')}
        </button>
      )}
    >
      <p className="text-sm text-gray-600">{t('account.sessions.description')}</p>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}
      {loading ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : sessions.length === 0 ? (
        <p className="text-sm text-gray-500">{t('account.sessions.empty')}</p>
      ) : (
        <div className="overflow-x-auto -mx-2">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wider text-gray-400">
              <tr>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.sessions.kind')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.sessions.ip')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.sessions.userAgent')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.sessions.created')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.sessions.lastSeen')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.sessions.expires')}</th>
                <th className="px-2 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.id} className="border-t border-gray-100">
                  <td className="px-2 py-2">
                    <span className="inline-flex items-center gap-1">
                      <Laptop className="w-3.5 h-3.5 text-gray-400" />
                      {t(`account.sessions.kinds.${session.kind}`, { defaultValue: session.kind })}
                    </span>
                    {session.current && (
                      <span className="ml-1.5 text-xs text-indigo-600">{t('account.sessions.current')}</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-gray-600">{session.ip || '—'}</td>
                  <td className="px-2 py-2 text-gray-600 max-w-[16rem] truncate" title={session.user_agent || ''}>
                    {session.user_agent || '—'}
                  </td>
                  <td className="px-2 py-2 text-gray-600 whitespace-nowrap">{formatDate(session.created_at)}</td>
                  <td className="px-2 py-2 text-gray-600 whitespace-nowrap">{formatDate(session.last_seen_at)}</td>
                  <td className="px-2 py-2 text-gray-600 whitespace-nowrap">{formatDate(session.expires_at)}</td>
                  <td className="px-2 py-2 text-right">
                    <button type="button" onClick={() => revoke(session)}
                      disabled={busyId === session.id} className={btnDanger}>
                      <Trash2 className="w-3.5 h-3.5" /> {t('account.sessions.revoke')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  );
}

// ── password ─────────────────────────────────────────────────────────────────

function PasswordSection({ t, onChanged }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    if (next !== repeat) {
      setError(t('auth.login.passwordsDiffer'));
      return;
    }
    setBusy(true);
    try {
      await changeMyPassword(current, next);
      await onChanged();
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.password.failed'));
      setBusy(false);
    }
  };

  return (
    <SectionCard title={t('account.password.title')}>
      <p className="text-sm text-gray-600">{t('account.password.description')}</p>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}
      <form onSubmit={submit} className="space-y-3 max-w-sm">
        <div>
          <label htmlFor="acct-current-password" className="block text-xs font-medium text-gray-500 mb-1">
            {t('account.password.current')}
          </label>
          <input id="acct-current-password" type="password" autoComplete="current-password"
            value={current} onChange={(e) => setCurrent(e.target.value)} className={inputCls} />
        </div>
        <div>
          <label htmlFor="acct-new-password" className="block text-xs font-medium text-gray-500 mb-1">
            {t('account.password.new')}
          </label>
          <input id="acct-new-password" type="password" autoComplete="new-password"
            value={next} onChange={(e) => setNext(e.target.value)} className={inputCls} />
        </div>
        <div>
          <label htmlFor="acct-repeat-password" className="block text-xs font-medium text-gray-500 mb-1">
            {t('account.password.repeat')}
          </label>
          <input id="acct-repeat-password" type="password" autoComplete="new-password"
            value={repeat} onChange={(e) => setRepeat(e.target.value)} className={inputCls} />
        </div>
        <button type="submit" disabled={busy || !current || !next || !repeat} className={btnPrimary}>
          {busy ? <Loader className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
          {t('account.password.submit')}
        </button>
      </form>
    </SectionCard>
  );
}

// ── API keys ─────────────────────────────────────────────────────────────────

const EXPIRY_CHOICES = [
  { value: '30', key: 'd30' },
  { value: '90', key: 'd90' },
  { value: '365', key: 'd365' },
  { value: '', key: 'never' },
];

function ApiKeysSection({ t, isAdmin }) {
  const { formatDate } = useFormatters();
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [workspaceOptions, setWorkspaceOptions] = useState([]);
  const [name, setName] = useState('');
  const [allWorkspaces, setAllWorkspaces] = useState(true);
  const [selected, setSelected] = useState([]);
  const [expiry, setExpiry] = useState('30');
  const [creating, setCreating] = useState(false);
  const [revokingId, setRevokingId] = useState('');
  const [justCreated, setJustCreated] = useState(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getMyApiKeys();
      setKeys(Array.isArray(data) ? data : []);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.apiKeys.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getWorkspaces();
        setWorkspaceOptions(Array.isArray(data) ? data : []);
      } catch {
        // Not fatal: the create form just offers no workspaces to narrow to,
        // and an unscoped key (the default) still works.
        setWorkspaceOptions([]);
      }
    })();
  }, []);

  const toggleWorkspace = (wsName) => {
    setSelected((prev) => (
      prev.includes(wsName) ? prev.filter((w) => w !== wsName) : [...prev, wsName]
    ));
  };

  const create = async (event) => {
    event.preventDefault();
    setCreating(true);
    setError('');
    try {
      const payload = {
        name,
        workspaces: allWorkspaces ? null : selected,
        expires_in_days: expiry ? Number(expiry) : null,
      };
      const { data } = await createMyApiKey(payload);
      setJustCreated(data);
      setCopied(false);
      setName('');
      setAllWorkspaces(true);
      setSelected([]);
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.apiKeys.createFailed'));
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (key) => {
    if (!window.confirm(t('account.apiKeys.revokeConfirm', { name: key.name || key.hint }))) return;
    setRevokingId(key.id);
    try {
      await revokeMyApiKey(key.id);
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.apiKeys.revokeFailed'));
    } finally {
      setRevokingId('');
    }
  };

  const copyKey = async () => {
    if (!justCreated?.key) return;
    try {
      await navigator.clipboard.writeText(justCreated.key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be refused (no permission, no secure context):
      // the key is still selectable text in the box, so nothing is lost.
    }
  };

  return (
    <SectionCard title={t('account.apiKeys.title')}>
      <p className="text-sm text-gray-600">{t('account.apiKeys.description')}</p>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}

      {justCreated && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 space-y-2">
          <p className="text-sm font-medium text-amber-900 flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" /> {t('account.apiKeys.newKeyTitle')}
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-white border border-amber-200 rounded px-2 py-1.5 text-xs break-all select-all">
              {justCreated.key}
            </code>
            <button type="button" onClick={copyKey} className={btnGhost}>
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? t('account.apiKeys.copied') : t('account.apiKeys.copy')}
            </button>
          </div>
          <p className="text-xs text-amber-800">{t('account.apiKeys.newKeyWarning')}</p>
        </div>
      )}

      <form onSubmit={create} className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
        <div>
          <label htmlFor="acct-key-name" className="block text-xs font-medium text-gray-500 mb-1">
            {t('account.apiKeys.name')}
          </label>
          <input id="acct-key-name" value={name} onChange={(e) => setName(e.target.value)}
            placeholder={t('account.apiKeys.namePlaceholder')} className={inputCls} />
        </div>
        <div>
          <span className="block text-xs font-medium text-gray-500 mb-1">{t('account.apiKeys.workspaces')}</span>
          <label className="flex items-center gap-2 text-sm text-gray-700 mb-1.5">
            <input type="checkbox" checked={allWorkspaces}
              onChange={(e) => setAllWorkspaces(e.target.checked)} />
            {t('account.apiKeys.allWorkspaces')}
          </label>
          {!allWorkspaces && (
            <div className="flex flex-wrap gap-2 pl-6">
              {workspaceOptions.map((ws) => (
                <label key={ws.name} className="flex items-center gap-1.5 text-xs text-gray-600 bg-white border border-gray-200 rounded px-2 py-1">
                  <input type="checkbox" checked={selected.includes(ws.name)}
                    onChange={() => toggleWorkspace(ws.name)} />
                  {ws.name}
                </label>
              ))}
            </div>
          )}
        </div>
        <div>
          <label htmlFor="acct-key-expiry" className="block text-xs font-medium text-gray-500 mb-1">
            {t('account.apiKeys.expires')}
          </label>
          <select id="acct-key-expiry" value={expiry} onChange={(e) => setExpiry(e.target.value)}
            className={inputCls + ' w-auto'}>
            {EXPIRY_CHOICES.map((choice) => (
              <option key={choice.key} value={choice.value}>
                {t(`account.apiKeys.expiresOptions.${choice.key}`)}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" disabled={creating} className={btnPrimary}>
          {creating ? <Loader className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          {t('account.apiKeys.add')}
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : keys.length === 0 ? (
        <p className="text-sm text-gray-500">{t('account.apiKeys.empty')}</p>
      ) : (
        <div className="overflow-x-auto -mx-2">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wider text-gray-400">
              <tr>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.apiKeys.name')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.apiKeys.hint')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.apiKeys.workspaces')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.apiKeys.created')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.apiKeys.lastUsed')}</th>
                <th className="text-left px-2 py-1.5 font-semibold">{t('account.apiKeys.expires')}</th>
                <th className="px-2 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id} className="border-t border-gray-100">
                  <td className="px-2 py-2 font-medium text-gray-800">{key.name || '—'}</td>
                  <td className="px-2 py-2 text-gray-500 font-mono text-xs">
                    <KeyRound className="inline w-3 h-3 mr-1 text-gray-400" />…{key.hint}
                  </td>
                  <td className="px-2 py-2 text-gray-600">
                    {Array.isArray(key.workspaces) && key.workspaces.length
                      ? key.workspaces.join(', ')
                      : t('account.apiKeys.scopeFull')}
                  </td>
                  <td className="px-2 py-2 text-gray-600 whitespace-nowrap">{formatDate(key.created_at)}</td>
                  <td className="px-2 py-2 text-gray-600 whitespace-nowrap">
                    {key.last_used_at ? formatDate(key.last_used_at) : t('account.apiKeys.never')}
                  </td>
                  <td className="px-2 py-2 text-gray-600 whitespace-nowrap">
                    {key.expires_at ? formatDate(key.expires_at) : t('account.apiKeys.never')}
                  </td>
                  <td className="px-2 py-2 text-right">
                    <button type="button" onClick={() => revoke(key)} disabled={revokingId === key.id}
                      className={btnDanger}>
                      <Trash2 className="w-3.5 h-3.5" /> {t('account.apiKeys.revoke')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {isAdmin && (
        <p className="text-xs text-gray-500">{t('account.apiKeys.adminHint')}</p>
      )}
    </SectionCard>
  );
}
