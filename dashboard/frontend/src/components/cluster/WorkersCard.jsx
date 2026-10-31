/**
 * The Cluster page's workers: who started each, what it runs and carries,
 * Add and Stop, and the autoscaling policy with its latest actions.
 *
 * GET /api/cluster/workers (common/worker_fleet.py, common/worker_autoscale.py).
 * A stop asks the worker to finish its runs and exit; a worker that carries
 * instances is never stopped, their containers work through it.
 */
import { useCallback, useEffect, useState } from 'react';
import { Gauge, Loader, Plus, Server, Square, Undo2 } from 'lucide-react';
import { addWorkers, cancelWorkerStop, getWorkers, stopWorker, updateAutoscale } from '../../api';
import { useI18n } from '../../i18n';
import { Card, Table } from './ClusterCard';

const FIELDS = [
  ['min_workers', 'min'], ['max_workers', 'max'], ['scale_up_wait_seconds', 'wait'],
  ['scale_down_idle_minutes', 'idle'], ['cooldown_seconds', 'cooldown'],
];

const errorText = (e) => {
  const detail = e?.response?.data?.detail;
  if (detail && typeof detail === 'object') return null;
  return detail || e?.message || '';
};

function AutoscaleForm({ autoscale, onSaved, t }) {
  const [draft, setDraft] = useState(autoscale.policy);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  const save = async (next = draft) => {
    setSaving(true);
    setError('');
    try {
      const { data } = await updateAutoscale(next);
      setDraft(data);
      setNote(t('cluster.workers.saved'));
      onSaved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const last = autoscale.last_decision || {};
  const reason = (key) => t(`cluster.workers.reason.${key}`, { defaultValue: key });

  return (
    <div className="mt-4 pt-4 border-t border-gray-100" data-testid="autoscale">
      <div className="flex flex-wrap items-center gap-3 mb-1">
        <h4 className="text-sm font-semibold text-gray-800 inline-flex items-center gap-1.5">
          <Gauge className="w-4 h-4 text-indigo-500" /> {t('cluster.workers.autoscale')}
        </h4>
        <label className="inline-flex items-center gap-1.5 text-xs text-gray-700">
          <input type="checkbox" checked={!!draft.enabled} disabled={saving}
                 onChange={(e) => save({ ...draft, enabled: e.target.checked })} />
          {t('cluster.workers.enabled')}
        </label>
        {last.reason && (
          <span className="text-xs text-gray-500">{t('cluster.workers.last', { reason: reason(last.reason) })}</span>
        )}
      </div>
      <p className="text-xs text-gray-400 mb-3">{t('cluster.workers.autoscaleHint')}</p>
      <div className="flex flex-wrap items-end gap-3">
        {FIELDS.map(([key, label]) => (
          <label key={key} className="text-xs text-gray-600 flex flex-col gap-1">
            {t(`cluster.workers.${label}`)}
            <input type="number" min={0} value={draft[key] ?? ''}
                   onChange={(e) => { setNote(''); setDraft({ ...draft, [key]: e.target.value }); }}
                   className="w-28 px-2 py-1 text-sm border border-gray-200 rounded-lg bg-white" />
          </label>
        ))}
        <button type="button" onClick={() => save()} disabled={saving}
                className="px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
          {t('cluster.workers.save')}
        </button>
        {note && <span className="text-xs text-emerald-600">{note}</span>}
      </div>
      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}

      <div className="mt-3">
        <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('cluster.workers.events')}</div>
        {(autoscale.events || []).length ? (
          <ul className="text-xs text-gray-600 space-y-0.5">
            {autoscale.events.slice(0, 6).map((ev) => (
              <li key={`${ev.at}-${ev.action}`}>
                <span className="text-gray-400">{new Date(ev.at).toLocaleString()}</span>{' · '}
                <span className={ev.action === 'error' ? 'text-red-600' : 'font-medium'}>
                  {t(`cluster.workers.action.${ev.action}`, { defaultValue: ev.action })}
                </span>{' · '}{reason(ev.reason)}
                {ev.member_id ? ` · ${ev.member_id}` : ''}
                {ev.started?.length ? ` · ${ev.started.join(', ')}` : ''}
                {ev.error ? ` · ${ev.error}` : ''}
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-gray-400">{t('cluster.workers.noEvents')}</p>}
      </div>
    </div>
  );
}

export default function WorkersCard({ onChanged }) {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try {
      const res = await getWorkers();
      setData(res.data);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader sets state after its await
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const act = async (key, fn) => {
    setBusy(key);
    setError('');
    setNote('');
    try {
      const res = await fn();
      if (res?.data?.started) setNote(t('cluster.workers.started', { names: res.data.started.join(', ') }));
      await load();
      onChanged?.();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setError(detail?.carriers ? t('cluster.workers.busy', { count: detail.carriers }) : errorText(e));
    } finally {
      setBusy('');
    }
  };

  if (!data) return null;
  const launcher = data.launcher || 'none';
  const rows = (data.workers || []).map((w) => ({ ...w, key: w.member_id }));

  return (
    <Card icon={Server} title={t('cluster.workers.title')} hint={t('cluster.workers.hint')}>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <button type="button" disabled={!data.launcher || !!busy}
                onClick={() => act('add', () => addWorkers(1))}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
          {busy === 'add' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          {t('cluster.workers.add')}
        </button>
        <span className="text-xs text-gray-500">{t(`cluster.workers.launcher.${launcher}`, { name: data.launcher_detail })}</span>
        {note && <span className="text-xs text-emerald-600">{note}</span>}
      </div>
      {error && <p className="text-xs text-red-600 mb-2">{error}</p>}
      <Table
        empty={t('cluster.workers.none')}
        rows={rows}
        columns={[
          { key: 'member_id', label: t('cluster.workers.worker'), render: (w) => (
            <span className="font-mono text-xs">{w.member_id}{w.container ? ` · ${w.container}` : ''}</span>
          ) },
          { key: 'origin', label: t('cluster.workers.originLabel'),
            render: (w) => t(`cluster.workers.origin.${w.origin}`, { defaultValue: w.origin }) },
          { key: 'runs', label: t('cluster.workers.runs'), render: (w) => `${w.runs}/${w.concurrency || '—'}` },
          { key: 'carriers', label: t('cluster.workers.instances') },
          { key: 'state', label: t('cluster.workers.state'), render: (w) => (
            <span className={w.draining ? 'text-amber-700' : 'text-emerald-700'}>
              {w.drained ? t('cluster.workers.drained')
                : w.draining ? t('cluster.workers.draining') : t('cluster.workers.working')}
            </span>
          ) },
          { key: 'actions', label: '', render: (w) => (w.drained ? null : w.draining ? (
            <button type="button" disabled={!!busy} onClick={() => act(w.member_id, () => cancelWorkerStop(w.member_id))}
                    className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline disabled:opacity-50">
              <Undo2 className="w-3.5 h-3.5" /> {t('cluster.workers.cancelStop')}
            </button>
          ) : (
            <button type="button" disabled={!!busy || !w.stoppable}
                    title={w.stoppable ? undefined : t('cluster.workers.busy', { count: w.carriers })}
                    onClick={() => act(w.member_id, () => stopWorker(w.member_id))}
                    className="inline-flex items-center gap-1 text-xs text-red-600 hover:underline disabled:opacity-40 disabled:no-underline">
              <Square className="w-3.5 h-3.5" /> {t('cluster.workers.stop')}
            </button>
          )) },
        ]}
      />
      <AutoscaleForm autoscale={data.autoscale} onSaved={load} t={t} />
    </Card>
  );
}
