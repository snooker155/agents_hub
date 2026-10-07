import { useState } from 'react';
import { Loader, CheckCircle, AlertCircle, RotateCcw, ChevronDown, ChevronRight } from 'lucide-react';
import { useI18n } from '../../i18n';
import { ACTIVE_JOB_STATUSES as ACTIVE, humanBytes } from './jobs';

function jobPercent(job) {
  if (job.percent != null) return Math.max(0, Math.min(100, job.percent));
  if (job.total) return Math.max(0, Math.min(100, (100 * (job.completed || 0)) / job.total));
  return null;
}

function JobBar({ job, onResume }) {
  const { t } = useI18n();
  const pct = jobPercent(job);
  const isError = job.status === 'error';
  const isDone = job.status === 'done';
  const isActive = ACTIVE.has(job.status);
  return (
    <div className="py-2 border-t border-gray-50 first:border-t-0">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-1.5 min-w-0">
          {isActive && <Loader className="w-3.5 h-3.5 animate-spin text-indigo-500 shrink-0" />}
          {isDone && <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" />}
          {isError && <AlertCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />}
          <span className="font-mono text-gray-700 truncate">{job.name}</span>
          <span className="text-xs text-gray-400 shrink-0">
            {t(`localModels.jobs.kinds.${job.kind}`, { defaultValue: job.kind })}
          </span>
        </span>
        <span className="text-xs text-gray-400 shrink-0">{t(`localModels.jobs.status.${job.status}`, { defaultValue: job.status })}</span>
      </div>
      {(isActive || (pct != null && !isError)) && (
        <div className="mt-1.5 h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${isDone ? 'bg-green-500' : 'bg-indigo-500'}`}
            style={{ width: `${pct != null ? pct : 0}%` }}
          />
        </div>
      )}
      <div className="mt-1 flex items-center justify-between text-xs text-gray-400">
        <span>
          {job.total
            ? t('localModels.jobs.completedOfTotal', { completed: humanBytes(job.completed), total: humanBytes(job.total) })
            : (job.message || '')}
        </span>
        {pct != null && !isError && <span className="tabular-nums">{Math.round(pct)}%</span>}
      </div>
      {job.message && job.total ? <p className="mt-0.5 text-xs text-gray-400">{job.message}</p> : null}
      {isError && job.error && <p className="mt-0.5 text-xs text-red-600">{job.error}</p>}
      {isError && job.resumable && onResume && (
        <button
          type="button"
          onClick={() => onResume(job)}
          className="mt-1 inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-0.5 text-xs font-medium text-gray-700 hover:border-indigo-300 hover:text-indigo-700"
        >
          <RotateCcw className="w-3 h-3" />
          {t('localModels.jobs.resume')}
        </button>
      )}
    </div>
  );
}

// One bar for everything in progress: bytes over bytes when every running
// download knows its size, otherwise the mean of the percentages known.
function overallPercent(active) {
  if (active.length === 0) return null;
  if (active.every((j) => j.total)) {
    const total = active.reduce((s, j) => s + j.total, 0);
    const done = active.reduce((s, j) => s + (j.completed || 0), 0);
    return Math.max(0, Math.min(100, (100 * done) / total));
  }
  const known = active.map(jobPercent).filter((p) => p != null);
  return known.length ? known.reduce((s, p) => s + p, 0) / known.length : null;
}

// Renders the shared job list, folded by default to one header line with
// how many are in progress and their combined bar. Nothing to show, nothing
// rendered: this is a running log of activity, not a fixture of the page.
// `onResume(job)` starts a resumable job again (a download continues from
// its partial file, a pull picks up the layers Ollama already has).
export default function JobProgress({ jobs, onResume }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  if (!jobs || jobs.length === 0) return null;
  // Most recent first, so a new download lands at the top instead of under
  // old ones. Sorted here rather than trusted from the backend: started_at
  // is an ISO UTC string from both the hub and the runtime, so it sorts as text.
  const ordered = [...jobs].sort((a, b) => String(b.started_at || '').localeCompare(String(a.started_at || '')));
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const failed = jobs.filter((j) => j.status === 'error').length;
  const pct = overallPercent(active);
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="local-jobs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center gap-2 px-4 py-3 text-left bg-gray-50 hover:bg-gray-100 transition-colors focus:outline-none border-gray-100 ${open ? 'border-b' : ''}`}
        data-testid="local-jobs-toggle"
      >
        {open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />}
        <span className="text-sm font-semibold text-gray-800 shrink-0">{t('localModels.jobs.title')}</span>
        <span className="flex items-center gap-1.5 text-xs text-gray-400 shrink-0">
          {active.length > 0 && <Loader className="w-3.5 h-3.5 animate-spin text-indigo-500" />}
          {active.length > 0 ? t('localModels.jobs.active', { count: active.length }) : t('localModels.jobs.noneActive')}
        </span>
        {active.length > 0 && (
          <span className="flex items-center gap-2 flex-1 min-w-0 ml-1" data-testid="local-jobs-overall">
            <span className="h-1.5 flex-1 min-w-[3rem] rounded-full bg-gray-200 overflow-hidden">
              <span className="block h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${pct ?? 0}%` }} />
            </span>
            {pct != null && <span className="text-xs text-gray-400 tabular-nums shrink-0">{Math.round(pct)}%</span>}
          </span>
        )}
        {failed > 0 && (
          <span className={`text-xs text-red-600 shrink-0 ${active.length > 0 ? '' : 'ml-auto'}`}>{t('localModels.jobs.failed', { count: failed })}</span>
        )}
      </button>
      {open && (
        <div className="px-4 py-1">
          {ordered.map((job) => <JobBar key={job.id} job={job} onResume={onResume} />)}
        </div>
      )}
    </div>
  );
}
