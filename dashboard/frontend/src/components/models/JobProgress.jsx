import { Loader, CheckCircle, AlertCircle, RotateCcw } from 'lucide-react';
import { useI18n } from '../../i18n';
import { ACTIVE_JOB_STATUSES as ACTIVE, humanBytes } from './jobs';

function JobBar({ job, onResume }) {
  const { t } = useI18n();
  const pct = job.percent != null
    ? Math.max(0, Math.min(100, job.percent))
    : (job.total ? Math.max(0, Math.min(100, (100 * (job.completed || 0)) / job.total)) : null);
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
            {job.kind === 'hf_download' ? t('localModels.jobs.kindDownload') : t('localModels.jobs.kindPull')}
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

// Renders the shared job list. Nothing to show, nothing rendered: this is a
// running log of activity, not a fixture of the page.
// `onResume(job)` starts a resumable job again (a download continues from
// its partial file, a pull picks up the layers Ollama already has).
export default function JobProgress({ jobs, onResume }) {
  const { t } = useI18n();
  if (!jobs || jobs.length === 0) return null;
  // Most recent first (jobs already arrive in some order from the backend;
  // this keeps a currently running job from scrolling off under old ones).
  const ordered = [...jobs].reverse();
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 bg-gray-50">
        <span className="text-sm font-semibold text-gray-800">{t('localModels.jobs.title')}</span>
      </div>
      <div className="px-4 py-1">
        {ordered.map((job) => <JobBar key={job.id} job={job} onResume={onResume} />)}
      </div>
    </div>
  );
}
