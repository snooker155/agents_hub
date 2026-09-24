import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, Check, Copy, Github, KeyRound, Laptop, Link2, Loader, Lock, LogOut, Palette, Plus,
  RefreshCw, Trash2, User as UserIcon,
} from 'lucide-react';
import {
  changeMyPassword, createMyApiKey, disconnectMyGitHub, getMyApiKeys, getMyGitHub,
  getMySessions, getWorkspaces, connectGitHub, revokeMyApiKey, revokeMySession,
  revokeOtherSessions,
} from '../api';
import { getMyPreferences, putMyPreferences } from '../api/palette';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { SectionCard, inputCls } from '../components/settingsUi';
import { useAuth } from '../components/auth';
import { useTheme, resolvePalette } from '../components/theme';
import { PRESET_ORDER, PRESETS, SHADES, checkPalette, matchPreset, rampFromColor } from '../lib/palette';
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
        <PaletteSection t={t} />
        <SessionsSection t={t} />
        {user?.has_password && features?.local_passwords && (
          <PasswordSection t={t} onChanged={onPasswordChanged} />
        )}
        <ApiKeysSection t={t} isAdmin={user?.role === 'admin'} />
        <GitHubSection t={t} />
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

// ── palette ──────────────────────────────────────────────────────────────────

/**
 * A personal color palette (docs/settings.md "Palette"): 2 to 4 base colors —
 * brand is always set, neutral/ok/danger are opt-in — saved through
 * GET/PUT /api/auth/preferences (dashboard/backend/routes/account.py). The
 * resolution order (this, then the workspace default, then the built-in
 * palette) lives in components/theme.js's resolvePalette, called again here
 * right after a save or a reset so the change takes effect at once.
 */
function PaletteSection({ t }) {
  const { theme, resolvedMode } = useTheme();
  const [saved, setSaved] = useState(null); // what the backend has, or null
  const [draft, setDraft] = useState(PRESETS.navy);
  const [enabled, setEnabled] = useState({ neutral: false, ok: false, danger: false });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getMyPreferences();
      const p = data?.palette && Object.keys(data.palette).length ? data.palette : null;
      setSaved(p);
      setDraft({ ...PRESETS.navy, ...(p || {}) });
      setEnabled({ neutral: Boolean(p?.neutral), ok: Boolean(p?.ok), danger: Boolean(p?.danger) });
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.palette.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const applyPreset = (name) => {
    setDraft(PRESETS[name]);
    setEnabled({ neutral: true, ok: true, danger: true });
  };

  const activePalette = () => {
    const payload = { brand: draft.brand };
    if (enabled.neutral) payload.neutral = draft.neutral;
    if (enabled.ok) payload.ok = draft.ok;
    if (enabled.danger) payload.danger = draft.danger;
    return payload;
  };

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const payload = activePalette();
      await putMyPreferences({ palette: payload });
      setSaved(payload);
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
      await resolvePalette(theme);
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.palette.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError('');
    try {
      await putMyPreferences({ palette: {} });
      setSaved(null);
      setDraft(PRESETS.navy);
      setEnabled({ neutral: false, ok: false, danger: false });
      await resolvePalette(theme);
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.palette.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const preset = matchPreset(activePalette());
  const ramp = rampFromColor(draft.brand, { mode: resolvedMode });
  const warnings = checkPalette(activePalette(), resolvedMode);

  return (
    <SectionCard title={t('account.palette.title')}>
      <p className="text-sm text-gray-600">{t('account.palette.description')}</p>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}
      {loading ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {PRESET_ORDER.map((name) => (
              <button key={name} type="button" onClick={() => applyPreset(name)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium border ${
                  preset === name ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                }`}>
                {t(`account.palette.presets.${name}`)}
              </button>
            ))}
            <span className={`px-3 py-1.5 rounded-lg text-xs font-medium ${
              preset ? 'text-gray-400' : 'border border-indigo-500 bg-indigo-50 text-indigo-700'
            }`}>
              {t('account.palette.presets.custom')}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <ColorField label={t('account.palette.brand')} value={draft.brand} required
              onChange={(v) => setDraft((d) => ({ ...d, brand: v }))} />
            <ColorField label={t('account.palette.neutral')} value={draft.neutral}
              enabled={enabled.neutral} onToggle={(v) => setEnabled((e) => ({ ...e, neutral: v }))}
              onChange={(v) => setDraft((d) => ({ ...d, neutral: v }))} />
            <ColorField label={t('account.palette.ok')} value={draft.ok}
              enabled={enabled.ok} onToggle={(v) => setEnabled((e) => ({ ...e, ok: v }))}
              onChange={(v) => setDraft((d) => ({ ...d, ok: v }))} />
            <ColorField label={t('account.palette.danger')} value={draft.danger}
              enabled={enabled.danger} onToggle={(v) => setEnabled((e) => ({ ...e, danger: v }))}
              onChange={(v) => setDraft((d) => ({ ...d, danger: v }))} />
          </div>

          <div>
            <span className="block text-xs font-medium text-gray-500 mb-1">{t('account.palette.preview')}</span>
            <div className="flex items-center gap-1">
              {SHADES.map((s) => (
                <div key={s} className="w-6 h-6 rounded" style={{ backgroundColor: ramp[s] }} title={String(s)} />
              ))}
            </div>
          </div>

          {warnings.length > 0 && (
            <div className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 space-y-1">
              {warnings.map((w) => (
                <div key={w.label}>
                  {t('account.palette.contrastWarning', { pair: w.label, ratio: w.ratio.toFixed(2) })}
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button type="button" onClick={save} disabled={busy} className={btnPrimary}>
              {busy ? <Loader className="w-4 h-4 animate-spin" /> : justSaved ? <Check className="w-4 h-4" /> : <Palette className="w-4 h-4" />}
              {t('account.palette.save')}
            </button>
            <button type="button" onClick={reset} disabled={busy || !saved} className={btnGhost}>
              <RefreshCw className="w-3.5 h-3.5" /> {t('account.palette.reset')}
            </button>
          </div>
        </>
      )}
    </SectionCard>
  );
}

function ColorField({ label, value, onChange, required, enabled, onToggle }) {
  const active = required || enabled;
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-gray-500">{label}</span>
        {!required && (
          <label className="flex items-center gap-1.5 text-xs text-gray-500">
            <input type="checkbox" checked={Boolean(enabled)} onChange={(e) => onToggle(e.target.checked)} />
          </label>
        )}
      </div>
      <div className="flex items-center gap-2">
        <input type="color" value={value || '#000000'} disabled={!active}
          onChange={(e) => onChange(e.target.value)}
          className="w-10 h-9 rounded border border-gray-200 disabled:opacity-40" />
        <input type="text" value={value || ''} disabled={!active}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls + ' font-mono text-xs disabled:opacity-40'} />
      </div>
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

// ── connected accounts ───────────────────────────────────────────────────────

/** Read and clear the `#github=connected|error&reason=…` the backend's
 *  callback (routes/github_app.py) lands the browser on. */
function takeGitHubOutcome() {
  if (typeof window === 'undefined') return null;
  const hash = (window.location.hash || '').replace(/^#/, '');
  if (!hash.includes('github=')) return null;
  const params = new URLSearchParams(hash);
  try {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  } catch { /* a test DOM without history: leaving the hash is harmless */ }
  return { outcome: params.get('github'), reason: params.get('reason') || '' };
}

function GitHubSection({ t }) {
  const { formatDate } = useFormatters();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await getMyGitHub();
      setStatus(data || null);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.github.loadFailed'));
    }
  }, [t]);

  useEffect(() => {
    const back = takeGitHubOutcome();
    if (back?.outcome === 'connected') setNotice({ ok: true, text: t('account.github.connectedToast') });
    else if (back) setNotice({ ok: false, text: t('account.github.errorToast', { reason: back.reason || back.outcome }) });
    load();
  }, [load, t]);

  const disconnect = async () => {
    if (!window.confirm(t('account.github.disconnectConfirm'))) return;
    setBusy(true);
    try {
      await disconnectMyGitHub();
      setNotice(null);
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('account.github.disconnectFailed'));
    } finally {
      setBusy(false);
    }
  };

  const connected = Boolean(status?.connected);
  const canConnect = Boolean(status?.configured && status?.key_configured);

  return (
    <SectionCard title={t('account.github.title')}>
      <p className="text-sm text-gray-600">{t('account.github.description')}</p>
      {notice && (
        <p className={`text-sm rounded-lg px-3 py-2 border ${notice.ok
          ? 'text-green-700 bg-green-50 border-green-100'
          : 'text-red-600 bg-red-50 border-red-100'}`}>
          {notice.text}
        </p>
      )}
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}
      {status === null && !error ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : status && (
        <div className="flex flex-wrap items-center justify-between gap-3 border border-gray-100 rounded-lg px-3 py-2.5">
          <div className="flex items-start gap-2 text-sm">
            <Github className="w-4 h-4 mt-0.5 text-gray-700" />
            <div>
              <div className="font-medium text-gray-800">{t('account.github.name')}</div>
              {connected ? (
                <div className="text-gray-600">
                  {t('account.github.connectedAs', { login: status.login || '?' })}
                  {status.access_expires_at && (
                    <span className="text-gray-400">
                      {', '}{t('account.github.until', { date: formatDate(status.access_expires_at) })}
                    </span>
                  )}
                  {status.refresh_expires_at && (
                    <div className="text-xs text-gray-400">
                      {t('account.github.renewUntil', { date: formatDate(status.refresh_expires_at) })}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-gray-500">
                  {!status.configured ? t('account.github.notConfigured')
                    : !status.key_configured ? t('account.github.noKey')
                      : t('account.github.notConnected')}
                </div>
              )}
            </div>
          </div>
          {connected ? (
            <button type="button" onClick={disconnect} disabled={busy} className={btnGhost}>
              {busy ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              {t('account.github.disconnect')}
            </button>
          ) : canConnect && (
            <button type="button" onClick={() => connectGitHub()} className={btnPrimary}>
              <Link2 className="w-3.5 h-3.5" /> {t('account.github.connect')}
            </button>
          )}
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
  // The key's own limits (docs/api-keys.md "Rate limits"): blank follows the
  // hub-wide setting and is left out of the request.
  const [perMinute, setPerMinute] = useState('');
  const [perDay, setPerDay] = useState('');
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
      if (perMinute !== '') payload.rate_limit_per_minute = Number(perMinute);
      if (perDay !== '') payload.tokens_per_day = Number(perDay);
      const { data } = await createMyApiKey(payload);
      setJustCreated(data);
      setCopied(false);
      setName('');
      setAllWorkspaces(true);
      setSelected([]);
      setPerMinute('');
      setPerDay('');
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
        <div>
          <div className="flex flex-wrap gap-3">
            <div>
              <label htmlFor="acct-key-rpm" className="block text-xs font-medium text-gray-500 mb-1">
                {t('account.apiKeys.ratePerMinute')}
              </label>
              <input id="acct-key-rpm" type="number" min="0" step="1" value={perMinute}
                onChange={(e) => setPerMinute(e.target.value)}
                placeholder={t('account.apiKeys.limitPlaceholder')} className={inputCls + ' w-40'} />
            </div>
            <div>
              <label htmlFor="acct-key-tpd" className="block text-xs font-medium text-gray-500 mb-1">
                {t('account.apiKeys.tokensPerDay')}
              </label>
              <input id="acct-key-tpd" type="number" min="0" step="1" value={perDay}
                onChange={(e) => setPerDay(e.target.value)}
                placeholder={t('account.apiKeys.limitPlaceholder')} className={inputCls + ' w-40'} />
            </div>
          </div>
          <p className="text-xs text-gray-500 mt-1">{t('account.apiKeys.limitsHint')}</p>
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
