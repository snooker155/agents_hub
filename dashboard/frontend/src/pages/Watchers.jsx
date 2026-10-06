import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Eye, Loader, Pause, Pencil, Play, Plus, RadioTower, Trash2, X } from 'lucide-react';
import {
  createWatcher, deleteWatcher, getWatcherKinds, getWatchers, pauseWatcher, probeWatcher, resumeWatcher,
  updateWatcher,
} from '../api/watchers';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';
import MailPresetPicker from '../components/connectors/MailPresetPicker';
import { presetForAddress } from '../components/connectors/mailPresets';
import GmailSignInNote from '../components/connectors/GmailSignInNote';

const inputCls = 'w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none';
const smallBtn = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed';

const fmt = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

function StateBadge({ w, t }) {
  if (w.last_error) {
    return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-800 border border-amber-200"><AlertTriangle className="w-3 h-3" />{t('watchers.state.error')}</span>;
  }
  if (!w.enabled) return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">{t('watchers.state.disabled')}</span>;
  if (w.paused_reason) return <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">{t('watchers.state.paused', { reason: w.paused_reason })}</span>;
  return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />{t('watchers.state.active')}</span>;
}

/** The create and edit form: a kind, its config fields (from /api/watchers/kinds), the interval. */
/** Whether a field's ``{other: value}`` condition (optional_when, hidden_when) holds. */
const when = (cond, values) => Object.entries(cond || {}).some(([k, v]) => (values[k] ?? false) === v);

function WatcherForm({ watcher, kinds, interval, google, workspace, onClose, onSaved, t }) {
  const [name, setName] = useState(watcher?.name || '');
  const [kind, setKind] = useState(watcher?.kind || kinds[0]?.kind || 'imap');
  const [config, setConfig] = useState(watcher?.config || {});
  const [seconds, setSeconds] = useState(watcher?.interval_seconds || interval.default);
  const [autoPause, setAutoPause] = useState(watcher?.auto_pause_after ?? 5);
  const [enabled, setEnabled] = useState(watcher ? !!watcher.enabled : true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const spec = kinds.find((k) => k.kind === kind);
  const fields = spec?.fields || [];
  // The provider presets (connectors/mail/presets.py) ride along with the
  // imap kind. The picked one is read back from the host field, so an
  // existing Gmail watcher opens with "Gmail" selected and a hand-typed host
  // shows as custom; a typed address with a known domain fills the host too.
  const presets = spec?.presets || [];
  const presetId = presets.find((p) => p.watcher?.host === config.host)?.id || '';
  const useGoogle = !!config.use_google;
  const applyPreset = (p) => { if (p) setConfig((c) => ({ ...c, ...p.watcher })); };
  const setField = (f, v) => setConfig((c) => {
    const next = { ...c, [f]: v };
    if (f === 'username' && !c.host) {
      const guess = presetForAddress(presets, v);
      if (guess) Object.assign(next, guess.watcher);
    }
    return next;
  });

  const save = async () => {
    setSaving(true);
    setError('');
    const payload = { name, kind, config, interval_seconds: Number(seconds), auto_pause_after: Number(autoPause), enabled };
    try {
      const { data } = watcher
        ? await updateWatcher(watcher.id, payload)
        : await createWatcher({ ...payload, workspace });
      onSaved(data);
    } catch (e) {
      setError(e?.response?.data?.detail || t('watchers.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 bg-black/30 flex items-start justify-center pt-16 px-4" data-testid="watcher-form">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-bold text-gray-900">{watcher ? t('watchers.form.editTitle') : t('watchers.form.createTitle')}</h3>
          <button type="button" onClick={onClose} aria-label={t('watchers.form.cancel')} className="p-1 rounded text-gray-500 hover:bg-gray-100"><X className="w-4 h-4" /></button>
        </div>
        <label className="block text-sm text-gray-700">
          <span className="font-medium">{t('watchers.form.name')}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} aria-label={t('watchers.form.name')} />
        </label>
        <label className="block text-sm text-gray-700">
          <span className="font-medium">{t('watchers.form.kind')}</span>
          <select value={kind} onChange={(e) => { setKind(e.target.value); setConfig({}); }} className={inputCls} aria-label={t('watchers.form.kind')}>
            {kinds.map((k) => <option key={k.kind} value={k.kind}>{t(`watchers.form.kinds.${k.kind}`)}</option>)}
          </select>
          <span className="text-xs text-gray-500">{t(`watchers.form.kinds.${kind}Hint`)}</span>
        </label>
        <MailPresetPicker presets={presets} value={presetId} onPick={applyPreset} t={t} inputCls={inputCls}
          googleActive={useGoogle}
          onUseGoogle={fields.some((f) => f.name === 'use_google') ? () => setField('use_google', true) : undefined} />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {fields.filter((f) => !when(f.hidden_when, config)).map((f) => (
            <label key={f.name} className={`block text-sm text-gray-700 ${f.name === 'url' || f.name === 'host' || f.name === 'use_google' ? 'sm:col-span-2' : ''}`}>
              <span className="font-medium">{t(`watchers.form.fields.${f.name}`)}{f.required && !when(f.optional_when, config) && <span className="text-red-500"> *</span>}</span>
              {f.type === 'bool' ? (
                <input type="checkbox" checked={config[f.name] ?? f.default ?? false} onChange={(e) => setField(f.name, e.target.checked)}
                  className="ml-2 h-4 w-4 rounded border-gray-300 text-indigo-600" aria-label={t(`watchers.form.fields.${f.name}`)} />
              ) : (
                <input type={f.type === 'int' ? 'number' : 'text'} value={config[f.name] ?? f.default ?? ''}
                  onChange={(e) => setField(f.name, e.target.value)} className={inputCls}
                  aria-label={t(`watchers.form.fields.${f.name}`)} />
              )}
              {f.type === 'secret' && <span className="text-xs text-gray-500">{t('watchers.form.secretHint')}</span>}
              {f.name === 'use_google' && (
                <span className="block">
                  <span className="block text-xs text-gray-500">{t('watchers.form.useGoogleHint')}</span>
                  {useGoogle && <GmailSignInNote status={google} t={t} />}
                </span>
              )}
            </label>
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="block text-sm text-gray-700">
            <span className="font-medium">{t('watchers.form.interval')}</span>
            <input type="number" min={interval.min} max={interval.max} value={seconds} onChange={(e) => setSeconds(e.target.value)}
              className={inputCls} aria-label={t('watchers.form.interval')} />
            <span className="text-xs text-gray-500">{t('watchers.form.intervalHint', { min: interval.min, max: interval.max })}</span>
          </label>
          <label className="block text-sm text-gray-700">
            <span className="font-medium">{t('watchers.form.autoPause')}</span>
            <input type="number" min="0" value={autoPause} onChange={(e) => setAutoPause(e.target.value)}
              className={inputCls} aria-label={t('watchers.form.autoPause')} />
            <span className="text-xs text-gray-500">{t('watchers.form.autoPauseHint')}</span>
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
          {t('watchers.form.enabled')}
        </label>
        {error && <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={smallBtn}>{t('watchers.form.cancel')}</button>
          <button type="button" onClick={save} disabled={saving || !name.trim()}
            className="px-4 py-1.5 rounded-lg text-sm font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40">
            {saving ? <Loader className="w-4 h-4 animate-spin" /> : t('watchers.form.save')}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The Watchers page (docs/watchers.md): the workspace's observers, their
 * state and the agents each one wakes, with create, edit, pause, resume, a
 * dry-run test and a poll ahead of schedule.
 */
export default function Watchers() {
  const { t } = useI18n();
  const { workspaceFilter, selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || workspaceFilter || '';
  // The kinds' own fields (whether Google is ready for a watch) read like
  // any other connector: this workspace's own if it defines one, else the
  // default's (connectors/channels/store.py) — the current workspace, not
  // the "show every workspace" list filter above.
  const currentWorkspace = selectedWorkspace || 'default';
  const [rows, setRows] = useState([]);
  const [kinds, setKinds] = useState([]);
  const [interval, setInterval_] = useState({ min: 15, max: 21600, default: 120 });
  const [google, setGoogle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null); // null | 'new' | watcher
  const [busy, setBusy] = useState('');
  const [probeResult, setProbeResult] = useState(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const [{ data }, { data: meta }] = await Promise.all([
        getWatchers(workspaceFilter), getWatcherKinds(currentWorkspace),
      ]);
      setRows(Array.isArray(data) ? data : []);
      setKinds(meta?.kinds || []);
      if (meta?.interval) setInterval_(meta.interval);
      setGoogle(meta?.google || null);
    } catch (e) {
      setError(e?.response?.data?.detail || t('watchers.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter, currentWorkspace, t]);

  useEffect(() => { load(); }, [load]);
  useLiveRefetch(load, { type: 'watchers.changed' });

  const act = async (id, fn) => {
    setBusy(id);
    setError('');
    try {
      await fn(id);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || t('watchers.actionFailed'));
    } finally {
      setBusy('');
    }
  };

  const probe = async (w, dryRun) => {
    setBusy(w.id);
    setProbeResult(null);
    try {
      const { data } = await probeWatcher(w.id, dryRun);
      setProbeResult({ id: w.id, ...data });
      if (!dryRun) await load();
    } catch (e) {
      setProbeResult({ id: w.id, ok: false, error: e?.response?.data?.detail || t('watchers.actionFailed') });
    } finally {
      setBusy('');
    }
  };

  const remove = async (w) => {
    if (!window.confirm(t('watchers.confirmDelete', { name: w.name }))) return;
    await act(w.id, deleteWatcher);
  };

  if (loading) return <PageLoader size="lg" />;

  return (
    <PageContainer>
      <PageHeader
        icon={Eye}
        title={t('watchers.title')}
        description={t('watchers.description')}
        actions={(
          <button type="button" onClick={() => setEditing('new')} disabled={!workspace}
            title={!workspace ? t('watchers.pickWorkspace') : undefined}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40">
            <Plus className="w-4 h-4" />{t('watchers.add')}
          </button>
        )}
      />

      {error && <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mb-4">{error}</p>}

      {rows.length === 0 ? (
        <div className="bg-white rounded-lg shadow-md p-10 text-center text-sm text-gray-500">
          <RadioTower className="w-8 h-8 mx-auto mb-3 text-gray-300" />
          {t('watchers.empty')}
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow-md overflow-hidden">
          <div className="hidden md:grid grid-cols-12 gap-3 px-4 py-2 text-[11px] uppercase tracking-wide text-gray-400 border-b border-gray-100">
            <div className="col-span-4">{t('watchers.columns.name')}</div>
            <div className="col-span-2">{t('watchers.columns.state')}</div>
            <div className="col-span-2">{t('watchers.columns.lastCheck')}</div>
            <div className="col-span-2">{t('watchers.columns.agents')}</div>
            <div className="col-span-2" />
          </div>
          <ul className="divide-y divide-gray-100" data-testid="watcher-list">
            {rows.map((w) => (
              <li key={w.id} className="px-4 py-3">
                <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-start text-sm">
                  <div className="md:col-span-4 min-w-0">
                    <div className="font-semibold text-gray-900 truncate">{w.name}</div>
                    <div className="text-xs text-gray-500">
                      <span className="uppercase tracking-wide">{w.kind}</span> · {t('watchers.every', { seconds: w.interval_seconds })} · {t('watchers.fired', { count: w.fired || 0 })}
                    </div>
                    <div className="text-xs text-gray-400 truncate">{w.kind === 'imap'
                      ? `${w.config?.use_google ? t('watchers.viaGoogle') : `${w.config?.username || ''}@${w.config?.host || ''}`}/${w.config?.folder || 'INBOX'}`
                      : w.config?.url}</div>
                  </div>
                  <div className="md:col-span-2"><StateBadge w={w} t={t} />
                    {w.last_error && <div className="text-xs text-amber-700 mt-1 break-words">{w.last_error}</div>}
                  </div>
                  <div className="md:col-span-2 text-xs text-gray-600">
                    <div>{fmt(w.last_checked_at)}</div>
                    {w.last_event && <div className="text-gray-500 mt-0.5 break-words">{w.last_event}</div>}
                  </div>
                  <div className="md:col-span-2 text-xs text-gray-600">
                    {w.listeners?.length
                      ? w.listeners.map((a) => <Link key={a.agent_id} to={`/agents/${a.agent_id}`} className="block text-indigo-600 hover:underline truncate">{a.name}</Link>)
                      : <span className="text-gray-400 italic">{t('watchers.nobody')}</span>}
                  </div>
                  <div className="md:col-span-2 flex flex-wrap gap-1.5 md:justify-end">
                    {w.paused_reason || !w.enabled ? (
                      <button type="button" className={smallBtn} disabled={busy === w.id} onClick={() => act(w.id, resumeWatcher)}><Play className="w-3 h-3" />{t('watchers.actions.resume')}</button>
                    ) : (
                      <button type="button" className={smallBtn} disabled={busy === w.id} onClick={() => act(w.id, pauseWatcher)}><Pause className="w-3 h-3" />{t('watchers.actions.pause')}</button>
                    )}
                    <button type="button" className={smallBtn} disabled={busy === w.id} onClick={() => probe(w, true)}>{t('watchers.actions.probe')}</button>
                    <button type="button" className={smallBtn} disabled={busy === w.id} onClick={() => probe(w, false)}>{t('watchers.actions.poll')}</button>
                    <button type="button" className={smallBtn} onClick={() => setEditing(w)} aria-label={t('watchers.actions.edit')}><Pencil className="w-3 h-3" /></button>
                    <button type="button" className={smallBtn} onClick={() => remove(w)} aria-label={t('watchers.actions.delete')}><Trash2 className="w-3 h-3" /></button>
                  </div>
                </div>
                {probeResult?.id === w.id && (
                  <div className={`mt-2 text-xs rounded-lg px-3 py-2 border ${probeResult.ok ? 'bg-gray-50 border-gray-100 text-gray-700' : 'bg-amber-50 border-amber-100 text-amber-800'}`} data-testid="probe-result">
                    {probeResult.ok ? (
                      <>
                        <div>{t('watchers.probe.ok', { summary: probeResult.summary })}</div>
                        {Array.isArray(probeResult.events) && probeResult.events.length > 0 && (
                          <div>{t('watchers.probe.events', { count: probeResult.events.length })}</div>
                        )}
                        {probeResult.would_wake?.length > 0 && <div>{t('watchers.probe.wouldWake', { agents: probeResult.would_wake.join(', ') })}</div>}
                      </>
                    ) : t('watchers.probe.failed', { error: probeResult.error })}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {editing && (
        <WatcherForm
          watcher={editing === 'new' ? null : editing}
          kinds={kinds}
          interval={interval}
          google={google}
          workspace={editing === 'new' ? workspace : editing.workspace}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
          t={t}
        />
      )}
    </PageContainer>
  );
}
