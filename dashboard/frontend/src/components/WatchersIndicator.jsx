import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Eye, Pause } from 'lucide-react';
import { getWatchersSummary } from '../api/watchers';
import { useWorkspace } from './workspace';
import { useLiveRefetch } from './stream';
import { useI18n } from '../i18n';

const fmt = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
};

/**
 * The header's list of active watchers (docs/watchers.md): how many observers
 * are polling right now, and on click each one with its last check, its last
 * change and the agents that react to it. Hidden until the workspace has at
 * least one watcher, like the connections card on the Dashboard.
 */
export default function WatchersIndicator() {
  const { workspaceFilter } = useWorkspace();
  const { t } = useI18n();
  const [summary, setSummary] = useState(null);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  const load = useCallback(() => {
    getWatchersSummary(workspaceFilter)
      .then(({ data }) => setSummary(data))
      .catch(() => setSummary(null));
  }, [workspaceFilter]);

  useEffect(() => { load(); }, [load]);
  // A poll writing its state, a watcher created or paused: the backend says
  // "watchers changed" and the indicator refetches.
  useLiveRefetch(load, { type: 'watchers.changed' });

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  if (!summary || !summary.total) return null;
  const { active, errors, watchers } = summary;

  return (
    <div className="relative" ref={ref} data-testid="watchers-indicator">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={t('watchers.indicator.tooltip', { count: active })}
        className={`relative flex items-center gap-1.5 px-2.5 py-2 rounded-lg border text-xs font-medium transition-colors ${
          errors > 0
            ? 'border-amber-300 text-amber-800 bg-amber-50 hover:bg-amber-100'
            : active > 0
              ? 'border-emerald-200 text-emerald-700 bg-emerald-50 hover:bg-emerald-100'
              : 'border-gray-200 text-gray-500 hover:bg-gray-50'
        }`}
      >
        <Eye className="w-4 h-4" />
        <span data-testid="watchers-count">{active}</span>
        {active > 0 && errors === 0 && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />}
        {errors > 0 && <AlertTriangle className="w-3.5 h-3.5" />}
      </button>

      {open && (
        <div className="header-sheet absolute right-0 top-full mt-2 w-96 bg-white border border-gray-200 rounded-xl shadow-lg z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100">
            <span className="text-sm font-semibold text-gray-900">
              {t('watchers.indicator.title', { active, total: summary.total })}
            </span>
            <Link to="/watchers" onClick={() => setOpen(false)} className="text-xs text-indigo-600 hover:text-indigo-800">
              {t('watchers.indicator.manage')}
            </Link>
          </div>
          <ul className="max-h-96 overflow-y-auto divide-y divide-gray-100">
            {watchers.map((w) => (
              <li key={w.id} className="px-4 py-2.5 text-sm">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${
                    w.last_error ? 'bg-amber-500' : w.active ? 'bg-emerald-500' : 'bg-gray-300'
                  }`} />
                  <span className="font-medium text-gray-900 truncate">{w.name}</span>
                  <span className="text-[10px] uppercase tracking-wide text-gray-400">{w.kind}</span>
                  {w.paused_reason && <Pause className="w-3 h-3 text-gray-400" />}
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {t('watchers.indicator.lastCheck', { when: fmt(w.last_checked_at) })}
                  {w.last_event && <> · {w.last_event}</>}
                </div>
                {w.last_error && <div className="text-xs text-amber-700 mt-0.5 truncate">{w.last_error}</div>}
                <div className="text-xs text-gray-500 mt-0.5">
                  {w.listeners?.length
                    ? t('watchers.indicator.wakes', { agents: w.listeners.map((a) => a.name).join(', ') })
                    : t('watchers.indicator.nobodyListens')}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
