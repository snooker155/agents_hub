import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Lock, RefreshCw } from 'lucide-react';
import { getHubSync, relativeTime, runHubSync } from '../api/hubSync';
import { MULTI, isAdmin, useAuth } from './auth';
import { useI18n } from '../i18n';

const POLL_MS = 30000;

/**
 * The header pill for two-way sync (hubsync/): shows that this hub's data
 * leaves for the linked hub and in what form, sealed or readable there.
 * Renders nothing until sync is on.
 */
export default function SyncIndicator() {
  const { t, language } = useI18n();
  const auth = useAuth();
  const canRun = auth?.mode !== MULTI || isAdmin(auth);
  const [status, setStatus] = useState(null);
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const ref = useRef(null);

  const load = useCallback(() => {
    getHubSync().then(({ data }) => setStatus(data)).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    window.addEventListener('focus', load);
    window.addEventListener('hubsync:changed', load);
    return () => {
      clearInterval(timer);
      window.removeEventListener('focus', load);
      window.removeEventListener('hubsync:changed', load);
    };
  }, [load]);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const syncNow = async () => {
    setRunning(true);
    try { await runHubSync(); } catch { /* the error shows in the status */ }
    setRunning(false);
    load();
  };

  if (!status || !status.enabled) return null;
  const lr = status.last_result || {};
  const locked = status.encrypted && !status.key_unlocked;
  // A non-administrator gets has_error with last_error blanked (the text can
  // name another workspace), so the warning must not rest on the text alone.
  const failed = !!status.last_error || !!status.has_error;
  const warn = failed || (status.conflicts || 0) > 0;
  const tone = locked ? 'red' : warn ? 'amber' : 'emerald';
  const toneCls = {
    red: 'border-red-300 text-red-700 bg-red-50 hover:bg-red-100',
    amber: 'border-amber-300 text-amber-800 bg-amber-50 hover:bg-amber-100',
    emerald: 'border-emerald-200 text-emerald-700 bg-emerald-50 hover:bg-emerald-100',
  }[tone];
  const Icon = status.encrypted && !running ? Lock : RefreshCw;
  const label = locked ? t('hubSync.indicator.locked')
    : status.encrypted ? t('hubSync.indicator.encrypted') : t('hubSync.indicator.plain');

  return (
    <div className="relative" ref={ref} data-testid="sync-indicator" data-tone={tone}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={t('hubSync.indicator.tooltip')}
        className={`flex items-center gap-1.5 px-2.5 py-2 rounded-lg border text-xs font-medium transition-colors ${toneCls}`}
      >
        <Icon className={`w-4 h-4 ${running ? 'animate-spin' : ''}`} />
        <span className="hidden sm:inline" data-testid="sync-label">{label}</span>
        {(warn || locked) && <AlertTriangle className="w-3.5 h-3.5" />}
      </button>

      {open && (
        <div className="header-sheet absolute right-0 top-full mt-2 w-80 max-w-[calc(100vw-2rem)] bg-white border border-gray-200 rounded-xl shadow-lg z-50 p-4 space-y-3 text-sm">
          <div className="font-semibold text-gray-900">{t('hubSync.indicator.tooltip')}</div>
          <div className="text-xs text-gray-600 break-all">
            {t('hubSync.indicator.remote')}: <span className="font-medium text-gray-800">{status.remote_name || status.remote}</span>
            {status.remote_name && status.remote ? <span className="block text-gray-400">{status.remote}</span> : null}
          </div>
          <p className={`text-xs rounded-lg px-2.5 py-2 border ${status.encrypted ? 'text-emerald-800 bg-emerald-50 border-emerald-200' : 'text-amber-800 bg-amber-50 border-amber-200'}`}>
            {status.encrypted ? t('hubSync.indicator.encryptedNote') : t('hubSync.indicator.plainNote')}
          </p>
          {locked && <p className="text-xs text-red-600">{t('hubSync.indicator.lockedNote')}</p>}
          <div className="text-xs text-gray-600">
            {t('hubSync.indicator.workspaces')}:{' '}
            <span className="text-gray-800">{(status.mappings || []).map((m) => (m.space && m.space !== m.workspace ? `${m.workspace} → ${m.space}` : m.workspace)).join(', ') || '—'}</span>
          </div>
          <div className="text-xs text-gray-600">
            {t('hubSync.status.lastPass')}: {status.last_pass_at ? relativeTime(status.last_pass_at, language) : t('hubSync.status.never')}
            {status.last_pass_at && <span className="block text-gray-400">{t('hubSync.status.counts', { pulled: lr.pulled ?? 0, applied: lr.applied ?? 0, pushed: lr.pushed ?? 0 })}</span>}
          </div>
          {status.last_error && <p className="text-xs text-amber-700 break-words">{t('hubSync.status.lastError')}: {status.last_error}</p>}
          {!status.last_error && status.has_error && <p className="text-xs text-amber-700" data-testid="sync-error-hidden">{t('hubSync.status.errorHidden')}</p>}
          {(status.conflicts || 0) > 0 && (
            <p className="text-xs text-amber-700">
              {t('hubSync.conflicts.count', { count: status.conflicts })}{' · '}
              <Link to="/settings/hubLink" onClick={() => setOpen(false)} className="text-indigo-600 hover:underline">{t('hubSync.indicator.conflictsLink')}</Link>
            </p>
          )}
          <div className="flex items-center justify-between gap-2 pt-1">
            <Link to="/settings/hubLink" onClick={() => setOpen(false)} className="text-xs text-indigo-600 hover:text-indigo-800">{t('hubSync.indicator.settings')}</Link>
            {canRun && (
              <button type="button" onClick={syncNow} disabled={running} data-testid="sync-indicator-run"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                <RefreshCw className={`w-3.5 h-3.5 ${running ? 'animate-spin' : ''}`} />{running ? t('hubSync.run.running') : t('hubSync.run.now')}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
