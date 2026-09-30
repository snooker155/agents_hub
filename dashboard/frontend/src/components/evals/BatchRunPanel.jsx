/**
 * Where a batch eval run stands: each provider batch it submitted (the cells'
 * first calls, then their judge calls), how many requests came back, and the
 * two actions on a waiting run: check now, or cancel. While the run waits it
 * refreshes itself every 30 seconds; the scheduler polls the providers once a
 * minute on its own, so leaving the page loses nothing.
 */
import { useCallback, useEffect, useState } from 'react';
import { Layers, RefreshCw, XCircle } from 'lucide-react';
import { getEvalRun } from '../../api';
import { cancelEvalRun, pollEvalRun } from '../../api/evalBatches';
import { useI18n } from '../../i18n';

const REFRESH_MS = 30000;

const STATUS_STYLE = {
  submitted: 'bg-blue-100 text-blue-700',
  processing: 'bg-indigo-100 text-indigo-700',
  done: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-amber-100 text-amber-700',
};

export default function BatchRunPanel({ run, onChange }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const waiting = run.status === 'batch_pending';
  const batches = run.batch?.batches || [];

  const refresh = useCallback(async () => {
    try {
      const { data } = await getEvalRun(run.eval_run_id);
      onChange?.(data);
    } catch {
      // A missed refresh is retried on the next interval.
    }
  }, [run.eval_run_id, onChange]);

  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [waiting, refresh]);

  const act = async (fn) => {
    setBusy(true);
    setError('');
    try {
      const { data } = await fn(run.eval_run_id);
      onChange?.({ ...run, ...data });
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-4 rounded-lg border border-blue-100 bg-blue-50/50 p-3" data-testid="batch-run-panel">
      <div className="flex items-center gap-2 mb-2">
        <Layers className="w-4 h-4 text-blue-600" />
        <span className="text-xs font-semibold text-gray-700">
          {waiting ? t('evals.batch.waiting') : t('evals.batch.finished')}
        </span>
        {waiting && (
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => act(pollEvalRun)}
              disabled={busy}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${busy ? 'animate-spin' : ''}`} /> {t('evals.batch.checkNow')}
            </button>
            <button
              onClick={() => {
                if (window.confirm(t('evals.batch.confirmCancel'))) act(cancelEvalRun);
              }}
              disabled={busy}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-red-200 text-red-600 bg-white hover:bg-red-50 disabled:opacity-50"
            >
              <XCircle className="w-3.5 h-3.5" /> {t('evals.batch.cancel')}
            </button>
          </div>
        )}
      </div>
      {error && <p className="text-xs text-red-600 mb-2">{error}</p>}
      {batches.length === 0 ? (
        <p className="text-xs text-gray-500">{t('evals.batch.noBatches')}</p>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-gray-500">
              <th className="py-1 pr-2 font-medium">{t('evals.batch.phase')}</th>
              <th className="py-1 pr-2 font-medium">{t('evals.batch.provider')}</th>
              <th className="py-1 pr-2 font-medium">{t('evals.batch.status')}</th>
              <th className="py-1 pr-2 font-medium">{t('evals.batch.requests')}</th>
              <th className="py-1 font-medium">{t('evals.batch.checked')}</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.batch_row_id} className="border-t border-blue-100">
                <td className="py-1 pr-2">{t(`evals.batch.phases.${b.phase}`)}</td>
                <td className="py-1 pr-2 text-gray-600" title={b.provider_batch_id}>{b.provider} / {b.model}</td>
                <td className="py-1 pr-2">
                  <span className={`px-1.5 py-0.5 rounded ${STATUS_STYLE[b.status] || 'bg-gray-100 text-gray-700'}`}>
                    {t(`evals.batch.statuses.${b.status}`)}
                  </span>
                  {b.error && <span className="ml-1 text-red-600" title={b.error}>!</span>}
                </td>
                <td className="py-1 pr-2 text-gray-600">
                  {t('evals.batch.recordedOf', { recorded: b.recorded, total: b.requests })}
                </td>
                <td className="py-1 text-gray-500">
                  {b.checked_at ? new Date(b.checked_at).toLocaleTimeString() : '·'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
