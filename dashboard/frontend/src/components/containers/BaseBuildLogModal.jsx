import { useEffect, useRef, useState } from 'react';
import { Check, Copy, X } from 'lucide-react';
import { getBaseBuildStatus } from '../../api/containers';
import { useI18n, useFormatters } from '../../i18n';
import PageLoader from '../PageLoader';

const POLL_MS = 2000;

const STATE_LOOK = {
  running: 'bg-indigo-900 text-indigo-200',
  done: 'bg-emerald-900 text-emerald-200',
  failed: 'bg-red-900 text-red-200',
  idle: 'bg-gray-800 text-gray-300',
};

/**
 * The log of the last base image pull or build on the Containers page
 * (GET /api/containers/build-base: the backend keeps its last 400 lines in
 * the state directory, dashboard/backend/routes/containers.py). Read when
 * opened, and every two seconds while the build runs, so a build started
 * here, in another tab or by another replica is followed live.
 */
export default function BaseBuildLogModal({ onClose }) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const [build, setBuild] = useState(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    let stopped = false;
    let timer = null;
    const poll = () => getBaseBuildStatus()
      .then(({ data }) => {
        if (stopped) return;
        setBuild(data || { state: 'idle' });
        setError('');
        if (data?.state === 'running') timer = setTimeout(poll, POLL_MS);
      })
      .catch((e) => { if (!stopped) setError(e?.response?.data?.detail || e.message); });
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // The newest lines are the ones to see, while it runs as after.
  const log = build?.log || '';
  useEffect(() => { bottomRef.current?.scrollIntoView?.(); }, [log]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(log);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch { /* clipboard unavailable */ }
  };

  const state = build?.state || 'idle';
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}
      role="dialog" aria-modal="true" aria-label={t('containers.baseBuildLog.title')} data-testid="base-build-log-modal">
      <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-3 min-w-0 flex-wrap">
            <span className="text-gray-200 text-sm font-semibold">{t('containers.baseBuildLog.title')}</span>
            {build && (
              <span className={`px-2 py-0.5 rounded text-[11px] font-medium ${STATE_LOOK[state] || STATE_LOOK.idle}`}
                data-testid="base-build-state">
                {t(`containers.baseBuildLog.state.${state}`, { defaultValue: state })}
              </span>
            )}
            {build?.started_at && (
              <span className="text-[11px] text-gray-400">
                {t('containers.baseBuildLog.started', { at: formatDate(build.started_at) })}
                {build.finished_at && ` · ${t('containers.baseBuildLog.finished', { at: formatDate(build.finished_at) })}`}
                {build.no_cache && ` · ${t('containers.baseBuildLog.noCache')}`}
              </span>
            )}
            {build?.source && <code className="text-[11px] text-gray-400 truncate">{build.source}</code>}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button type="button" onClick={copy} disabled={!log}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 disabled:opacity-40"
              title={t('containers.baseBuildLog.copy')}>
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {t('containers.baseBuildLog.copy')}
            </button>
            <button type="button" onClick={onClose} className="text-gray-500 hover:text-gray-300" title={t('containers.baseBuildLog.close')}
              aria-label={t('containers.baseBuildLog.close')}>
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
        {(build?.error || error) && (
          <p className="px-5 pt-3 text-xs text-red-300" data-testid="base-build-error">{build?.error || error}</p>
        )}
        <div className="flex-1 overflow-auto p-5">
          {!build && !error ? (
            <PageLoader size="sm" />
          ) : log ? (
            <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5" data-testid="base-build-log-text">{log}</pre>
          ) : (
            <p className="text-xs text-gray-400">
              {state === 'idle' ? t('containers.baseBuildLog.none') : t('containers.baseBuildLog.empty')}
            </p>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
