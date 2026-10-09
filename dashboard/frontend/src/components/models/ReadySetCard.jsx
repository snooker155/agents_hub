import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, ChevronDown, ChevronRight, CircleDashed, Loader, Rocket } from 'lucide-react';
import { cancelReadySet, getReadySet, startReadySet } from '../../api/localModels';
import { ACTIVE_JOB_STATUSES as ACTIVE, humanBytes } from './jobs';
import { isAdmin, isMultiUser, useAuth } from '../auth';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const POLL_MS = 2000;

const STEP_ICON = {
  running: <Loader className="w-4 h-4 animate-spin text-indigo-500" />,
  done: <CheckCircle className="w-4 h-4 text-green-500" />,
  skipped: <CheckCircle className="w-4 h-4 text-green-500" />,
  error: <AlertCircle className="w-4 h-4 text-red-500" />,
  todo: <CircleDashed className="w-4 h-4 text-gray-300" />,
};

/**
 * The Local tab's one-button path: providers/local_set.py. While its job runs
 * the card follows the job's steps (the job is also on the shared list
 * below); otherwise it shows the plan: the chat model this machine gets and
 * which steps are already done. The body folds away: closed by default
 * once everything is in place (the header then carries a green mark), open
 * while a job runs or something is still missing.
 */
export default function ReadySetCard({ onJobStarted }) {
  const { t } = useI18n();
  const toast = useToast();
  const auth = useAuth();
  const canStart = !isMultiUser(auth) || isAdmin(auth);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [userOpen, setUserOpen] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data: d } = await getReadySet();
      setData(d);
      return d;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const job = data?.job;
  const running = !!job && ACTIVE.has(job.status);
  useEffect(() => {
    if (!running) return undefined;
    const id = setTimeout(load, POLL_MS);
    return () => clearTimeout(id);
  }, [running, data, load]);

  if (!data || !data.available) return null;

  const start = async () => {
    setBusy(true);
    try {
      await startReadySet();
      toast.success(t('localModels.readySet.started'));
      await load();
      if (onJobStarted) onJobStarted();
    } catch (e) {
      toast.error(t('localModels.readySet.startFailed'), errorDetail(e));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    try {
      await cancelReadySet();
      await load();
    } catch (e) {
      toast.error(t('localModels.readySet.cancelFailed'), errorDetail(e));
    }
  };

  // The job's own steps while it runs or when it failed (the failed step
  // shows there); otherwise the live plan, which knows what a restart undid.
  const showJob = running || job?.status === 'error';
  const steps = (showJob && job?.meta?.steps?.length ? job.meta.steps : data.steps) || [];
  const chat = (showJob && job?.meta?.chat) || data.chat;
  const showJobFailure = job?.status === 'error' && job.error && job.error !== 'cancelled';
  const allThere = !!(data.installed ?? data.ready) && !running;
  const onlyLoad = allThere && !data.ready;
  const open = userOpen ?? !allThere;
  const present = steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="ready-set" data-open={open ? 'true' : 'false'}>
      <div className={`flex items-center justify-between gap-2 pr-4 border-gray-100 ${open ? 'border-b' : ''}`}>
        <button type="button" onClick={() => setUserOpen(!open)} aria-expanded={open}
          className="flex-1 min-w-0 flex items-center gap-2 px-4 py-3 text-left hover:bg-gray-50 transition-colors focus:outline-none"
          data-testid="ready-set-toggle">
          {open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />}
          <Rocket className="w-4 h-4 text-indigo-500 shrink-0" />
          <span className="text-sm font-semibold text-gray-800 shrink-0">{t('localModels.readySet.title')}</span>
          {allThere ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-50 text-green-700 text-xs font-medium" data-testid="ready-set-mark">
              <CheckCircle className="w-3.5 h-3.5" />
              {t(onlyLoad ? 'localModels.readySet.allThereUnloaded' : 'localModels.readySet.allThere')}
            </span>
          ) : running ? (
            <span className="inline-flex items-center gap-1 text-xs text-indigo-600" data-testid="ready-set-mark">
              <Loader className="w-3.5 h-3.5 animate-spin" />
              {t('localModels.readySet.inProgress')}
            </span>
          ) : steps.length > 0 && (
            <span className="text-xs text-gray-400 truncate" data-testid="ready-set-mark">
              {t('localModels.readySet.partly', { present, total: steps.length })}
            </span>
          )}
        </button>
        <span className="flex items-center gap-2 shrink-0">
          {running && canStart && (
            <button type="button" onClick={cancel}
              className="px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium">
              {t('localModels.readySet.cancel')}
            </button>
          )}
          {canStart && !running && (
            <button type="button" onClick={start} disabled={busy}
              className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-medium disabled:opacity-50">
              {data.ready || job ? t('localModels.readySet.again') : t('localModels.readySet.start')}
            </button>
          )}
        </span>
      </div>
      {open && <div className="px-4 py-3 space-y-2">
        <p className="text-xs text-gray-500">{t('localModels.readySet.intro')}</p>
        {chat && (
          <p className="text-xs text-gray-600">
            {t('localModels.readySet.planModel', { label: chat.label, size: humanBytes(chat.size_bytes) || '?' })}
            {data.default_would_change ? ` ${t('localModels.readySet.defaultNote')}` : ''}
          </p>
        )}
        {!canStart && <p className="text-xs text-gray-400">{t('localModels.readySet.adminOnly')}</p>}
        {data.ready && !running && <p className="text-xs text-green-700">{t('localModels.readySet.ready')}</p>}
        {onlyLoad && <p className="text-xs text-gray-600">{t('localModels.readySet.unloadedNote')}</p>}
        <ul className="space-y-1.5">
          {steps.map((s) => (
            <li key={s.id} className="text-sm" data-step={s.id} data-status={s.status}>
              <div className="flex items-center gap-2">
                {STEP_ICON[s.status] || STEP_ICON.todo}
                <span className="text-gray-700">{t(`localModels.readySet.steps.${s.id}`, { defaultValue: s.label })}</span>
                <span className="text-xs text-gray-400">{t(`localModels.readySet.stepStatus.${s.status}`, { defaultValue: s.status })}</span>
                {s.status === 'running' && s.percent > 0 && (
                  <span className="text-xs text-gray-400 tabular-nums">{Math.round(s.percent)}%</span>
                )}
              </div>
              {s.status === 'running' && (
                <div className="ml-6 mt-1 h-1 w-48 rounded-full bg-gray-100 overflow-hidden">
                  <div className="h-full bg-indigo-500 rounded-full transition-all" style={{ width: `${s.percent || 0}%` }} />
                </div>
              )}
              {s.detail && s.status !== 'todo' && <p className="ml-6 text-xs text-gray-400">{s.detail}</p>}
            </li>
          ))}
        </ul>
        {showJobFailure && <p className="text-xs text-red-600">{job.error}</p>}
      </div>}
    </div>
  );
}
