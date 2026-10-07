import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, CircleDashed, Loader, MinusCircle, Rocket } from 'lucide-react';
import { cancelReadySet, getReadySet, startReadySet } from '../../api/localModels';
import { ACTIVE_JOB_STATUSES as ACTIVE, humanBytes } from './jobs';
import { isAdmin, isMultiUser, useAuth } from '../auth';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const POLL_MS = 2000;

const STEP_ICON = {
  running: <Loader className="w-4 h-4 animate-spin text-indigo-500" />,
  done: <CheckCircle className="w-4 h-4 text-green-500" />,
  skipped: <MinusCircle className="w-4 h-4 text-gray-400" />,
  error: <AlertCircle className="w-4 h-4 text-red-500" />,
  todo: <CircleDashed className="w-4 h-4 text-gray-300" />,
};

/**
 * The Local tab's one-button path: providers/local_set.py. While its job runs
 * the card follows the job's steps (the job is also on the shared list
 * below); otherwise it shows the plan: the chat model this machine gets and
 * which steps are already done.
 */
export default function ReadySetCard({ onJobStarted }) {
  const { t } = useI18n();
  const toast = useToast();
  const auth = useAuth();
  const canStart = !isMultiUser(auth) || isAdmin(auth);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

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

  // The running or latest job's own steps, else the plan's.
  const steps = (job?.meta?.steps?.length ? job.meta.steps : data.steps) || [];
  const chat = job?.meta?.chat || data.chat;
  const showJobFailure = job?.status === 'error' && job.error && job.error !== 'cancelled';

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="ready-set">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-gray-100">
        <span className="flex items-center gap-2 text-sm font-semibold text-gray-800">
          <Rocket className="w-4 h-4 text-indigo-500" />
          {t('localModels.readySet.title')}
        </span>
        <span className="flex items-center gap-2">
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
      <div className="px-4 py-3 space-y-2">
        <p className="text-xs text-gray-500">{t('localModels.readySet.intro')}</p>
        {chat && (
          <p className="text-xs text-gray-600">
            {t('localModels.readySet.planModel', { label: chat.label, size: humanBytes(chat.size_bytes) || '?' })}
            {data.default_would_change ? ` ${t('localModels.readySet.defaultNote')}` : ''}
          </p>
        )}
        {!canStart && <p className="text-xs text-gray-400">{t('localModels.readySet.adminOnly')}</p>}
        {data.ready && !job && <p className="text-xs text-green-700">{t('localModels.readySet.ready')}</p>}
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
      </div>
    </div>
  );
}
