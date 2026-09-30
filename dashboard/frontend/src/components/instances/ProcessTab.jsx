import { useCallback, useEffect, useState } from 'react';
import { Box, Loader, RefreshCw, Save, Server } from 'lucide-react';

import { getInstanceCarriers, updateInstanceInputs } from '../../api';
import { useI18n } from '../../i18n';

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function ageSeconds(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.round((Date.now() - then) / 1000));
}

function Field({ label, children }) {
  return (
    <div className="px-3 py-2 rounded-lg bg-gray-50 border border-gray-100">
      <div className="text-[11px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className="text-sm font-medium text-gray-800 mt-0.5 break-all">{children}</div>
    </div>
  );
}

/**
 * The resident instance's own process: the carrier that answers its mailbox
 * (instances/carrier.py), the inputs it was started with (which apply without
 * a restart), and the history of every carrier it has run under.
 */
export default function ProcessTab({ instance, onInstanceUpdated }) {
  const { t } = useI18n();
  const [carriers, setCarriers] = useState([]);
  const [loadingCarriers, setLoadingCarriers] = useState(true);

  const [takeTasks, setTakeTasks] = useState(!!instance.take_tasks);
  const [concurrency, setConcurrency] = useState(instance.concurrency != null ? String(instance.concurrency) : '');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);

  const fetchCarriers = useCallback(async () => {
    setLoadingCarriers(true);
    try {
      const { data } = await getInstanceCarriers(instance.instance_id);
      setCarriers(data?.items || []);
    } catch {
      setCarriers([]);
    } finally {
      setLoadingCarriers(false);
    }
  }, [instance.instance_id]);

  useEffect(() => { fetchCarriers(); }, [fetchCarriers]);

  useEffect(() => {
    setTakeTasks(!!instance.take_tasks);
    setConcurrency(instance.concurrency != null ? String(instance.concurrency) : '');
  }, [instance.take_tasks, instance.concurrency]);

  const dirty = takeTasks !== !!instance.take_tasks
    || concurrency !== (instance.concurrency != null ? String(instance.concurrency) : '');

  const handleSaveInputs = async () => {
    setSaving(true);
    setSaveError('');
    setSaved(false);
    try {
      const conc = concurrency.trim() ? parseInt(concurrency, 10) : null;
      const { data } = await updateInstanceInputs(instance.instance_id, { take_tasks: takeTasks, concurrency: conc });
      onInstanceUpdated(data);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setSaveError(err.response?.data?.detail || t('instanceDetail.process.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const heartbeatAge = ageSeconds(instance.heartbeat_at);

  return (
    <div className="space-y-4">
      {/* Carrier status */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
          {instance.carrier_mode === 'docker' ? <Box className="w-4 h-4 text-indigo-500" /> : <Server className="w-4 h-4 text-indigo-500" />}
          {t('instanceDetail.process.carrier')}
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Field label={t('instanceDetail.process.status')}>{instance.carrier_status || '—'}</Field>
          <Field label={t('instanceDetail.process.mode')}>
            {instance.carrier_mode === 'docker' ? t('instanceDetail.process.docker') : t('instanceDetail.process.local')}
          </Field>
          <Field label={t('instanceDetail.process.host')}>{instance.carrier_host || '—'}</Field>
          <Field label={instance.carrier_mode === 'docker' ? t('instanceDetail.process.container') : 'PID'}>
            {instance.carrier_mode === 'docker' ? (instance.container_name || '—') : (instance.pid || '—')}
          </Field>
          <Field label={t('instanceDetail.process.started')}>{fmtDate(instance.carrier_started_at)}</Field>
          <Field label={t('instanceDetail.process.heartbeat')}>
            {heartbeatAge != null ? t('instanceDetail.process.secondsAgo', { count: heartbeatAge }) : '—'}
          </Field>
          <Field label={t('instanceDetail.process.environment')}>{instance.environment_name || t('instanceDetail.process.environmentDefault')}</Field>
          <Field label={t('instanceDetail.process.directUrl')}>{instance.http_url || '—'}</Field>
        </div>
        {instance.carrier_error && (
          <div className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {instance.carrier_error}
          </div>
        )}
      </div>

      {/* Inputs */}
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">{t('instanceDetail.process.inputs')}</h3>
        <div className="space-y-3">
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <input type="checkbox" checked={takeTasks} onChange={(e) => setTakeTasks(e.target.checked)} />
            <span>
              <span className="block text-sm font-medium text-gray-800">{t('startInstance.takeTasks')}</span>
              <span className="block text-xs text-gray-500 mt-0.5">{t('startInstance.takeTasksHint')}</span>
            </span>
          </label>
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
              {t('startInstance.concurrency')}
            </label>
            <input
              type="number"
              min="1"
              max="32"
              className="w-28 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder={t('startInstance.concurrencyDefault')}
              value={concurrency}
              onChange={(e) => setConcurrency(e.target.value)}
            />
          </div>
          {saveError && <div className="text-xs text-red-600">{saveError}</div>}
          <button
            type="button"
            onClick={handleSaveInputs}
            disabled={saving || !dirty}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-40"
          >
            {saving ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {saved ? t('instanceDetail.process.saved') : t('instanceDetail.process.save')}
          </button>
          <p className="text-xs text-gray-400">{t('instanceDetail.process.inputsHint')}</p>
        </div>
      </div>

      {/* Carrier history */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-gray-800">{t('instanceDetail.process.carrierHistory')}</h3>
          <button type="button" onClick={fetchCarriers} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <RefreshCw className={`w-3.5 h-3.5 ${loadingCarriers ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {loadingCarriers ? (
          <div className="p-6 text-center text-sm text-gray-400">{t('instanceDetail.loading')}</div>
        ) : !carriers.length ? (
          <div className="p-6 text-center text-sm text-gray-400">{t('instanceDetail.process.noCarriers')}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.process.mode')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.process.host')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.process.started')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.process.finished')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.process.exitCode')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.process.reason')}</th>
                </tr>
              </thead>
              <tbody>
                {carriers.map((c) => (
                  <tr key={c.carrier_id} className="border-b border-gray-50 last:border-0">
                    <td className="px-4 py-2">{c.mode}</td>
                    <td className="px-4 py-2">{c.host || '—'}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{fmtDate(c.started_at)}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{fmtDate(c.finished_at)}</td>
                    <td className="px-4 py-2">{c.exit_code ?? '—'}</td>
                    <td className="px-4 py-2 text-gray-500">{c.error || c.reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
