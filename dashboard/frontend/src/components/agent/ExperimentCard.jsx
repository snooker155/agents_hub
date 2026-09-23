import React, { useCallback, useEffect, useState } from 'react';
import { FlaskConical, Loader, Pause, Play, Square } from 'lucide-react';
import {
  endAgentExperiment, getAgentExperiment, getAgentExperimentReport, getAgentVersions,
  putAgentExperiment,
} from '../../api';
import { useI18n } from '../../i18n';
import { experimentArms } from './onlineEvals';

const inputCls = 'border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

const num = (v, digits = 0) => (v === null || v === undefined ? '—' : Number(v).toFixed(digits));
const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(Number(v) * 100)}%`);

function Report({ report, t }) {
  if (!report?.arms?.length) return null;
  const cols = [
    ['runs', (a) => a.runs],
    ['completed', (a) => a.completed],
    ['failed', (a) => a.failed],
    ['meanCost', (a) => (a.mean_cost_usd == null ? '—' : `$${Number(a.mean_cost_usd).toFixed(4)}`)],
    ['meanTokens', (a) => num(a.mean_tokens)],
    ['meanDuration', (a) => (a.mean_duration_ms == null ? '—' : `${(Number(a.mean_duration_ms) / 1000).toFixed(1)}s`)],
    ['meanScore', (a) => num(a.mean_score, 2)],
    ['passRate', (a) => pct(a.pass_rate)],
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500">
            <th className="py-1 pr-3">{t('agentDetails.experiment.arm')}</th>
            {cols.map(([key]) => <th key={key} className="py-1 pr-3">{t(`agentDetails.experiment.cols.${key}`)}</th>)}
          </tr>
        </thead>
        <tbody>
          {report.arms.map((arm) => (
            <tr key={arm.version} className="border-t border-gray-100">
              <td className="py-1 pr-3 font-mono text-xs">v{arm.version} ({pct(arm.share)})</td>
              {cols.map(([key, get]) => <td key={key} className="py-1 pr-3">{get(arm)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The A/B experiment of this agent (docs/experiments.md): two stored
 * versions of its definition, each serving a share of its runs, and the
 * per-arm comparison of outcomes, cost and online eval scores.
 */
export default function ExperimentCard({ agentId }) {
  const { t } = useI18n();
  const [versions, setVersions] = useState([]);
  const [experiment, setExperiment] = useState(null);
  const [active, setActive] = useState(false);
  const [report, setReport] = useState(null);
  const [armA, setArmA] = useState('');
  const [armB, setArmB] = useState('current');
  const [shareA, setShareA] = useState('50');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [{ data: vers }, { data: exp }] = await Promise.all([
        getAgentVersions(agentId), getAgentExperiment(agentId),
      ]);
      const list = vers?.versions || [];
      setVersions(list);
      setArmA((cur) => (cur !== '' ? cur : (list.length ? String(list[list.length - 1].version) : '')));
      setExperiment(exp?.experiment || null);
      setActive(!!exp?.active);
      if (exp?.experiment) {
        const { data: rep } = await getAgentExperimentReport(agentId);
        setReport(rep);
      } else {
        setReport(null);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('agentDetails.experiment.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, t]);

  useEffect(() => { load(); }, [load]);

  const run = async (fn) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('agentDetails.experiment.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const start = (e) => {
    e.preventDefault();
    const arms = experimentArms(armA, armB, shareA);
    if (!arms) { setError(t('agentDetails.experiment.badArms')); return; }
    run(() => putAgentExperiment(agentId, { enabled: true, arms, note }));
  };

  const setEnabled = (enabled) => run(() => putAgentExperiment(agentId, {
    enabled, arms: experiment.arms, note: experiment.note || '',
  }));

  const stop = () => {
    if (!window.confirm(t('agentDetails.experiment.confirmStop'))) return;
    run(() => endAgentExperiment(agentId));
  };

  const versionOptions = [
    ...versions.map((v) => ({ value: String(v.version), label: t('agentDetails.versions.version', { n: v.version }) })),
    { value: 'current', label: t('agentDetails.experiment.current') },
  ];

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-lg font-semibold text-gray-900 flex items-center">
          <FlaskConical className="w-5 h-5 mr-2 text-indigo-500" />
          {t('agentDetails.experiment.title')}
        </h3>
        {loading && <Loader className="w-4 h-4 animate-spin text-gray-400" />}
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('agentDetails.experiment.description')}</p>
      {error && <div className="text-sm text-red-600 mb-3">{error}</div>}

      {active && experiment ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded ${experiment.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'}`}>
              {experiment.enabled ? t('agentDetails.experiment.running') : t('agentDetails.experiment.paused')}
            </span>
            <span className="text-gray-700">
              {experiment.arms.map((a) => `v${a.version} ${pct(a.share)}`).join(' / ')}
            </span>
            {experiment.note && <span className="text-gray-500">{experiment.note}</span>}
            <div className="ml-auto flex gap-2">
              <button type="button" disabled={busy} onClick={() => setEnabled(!experiment.enabled)}
                      className="inline-flex items-center px-2.5 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
                {experiment.enabled ? <Pause className="w-3.5 h-3.5 mr-1" /> : <Play className="w-3.5 h-3.5 mr-1" />}
                {experiment.enabled ? t('agentDetails.experiment.pause') : t('agentDetails.experiment.resume')}
              </button>
              <button type="button" disabled={busy} onClick={stop}
                      className="inline-flex items-center px-2.5 py-1 text-sm text-red-700 border border-red-200 rounded hover:bg-red-50 disabled:opacity-50">
                <Square className="w-3.5 h-3.5 mr-1" />{t('agentDetails.experiment.stop')}
              </button>
            </div>
          </div>
          <Report report={report} t={t} />
        </div>
      ) : (
        <>
          <form onSubmit={start} className="flex flex-wrap gap-3 items-end" aria-label={t('agentDetails.experiment.start')}>
            <label className="text-xs text-gray-600">
              <span className="block mb-1">{t('agentDetails.experiment.armA')}</span>
              <select value={armA} onChange={(e) => setArmA(e.target.value)} className={inputCls}>
                <option value="">{t('agentDetails.experiment.pickVersion')}</option>
                {versionOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-600">
              <span className="block mb-1">{t('agentDetails.experiment.shareA')}</span>
              <input type="number" min="1" max="99" step="1" value={shareA}
                     onChange={(e) => setShareA(e.target.value)} className={`${inputCls} w-20`} />
            </label>
            <label className="text-xs text-gray-600">
              <span className="block mb-1">{t('agentDetails.experiment.armB')}</span>
              <select value={armB} onChange={(e) => setArmB(e.target.value)} className={inputCls}>
                {versionOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-600 flex-1 min-w-[10rem]">
              <span className="block mb-1">{t('agentDetails.experiment.note')}</span>
              <input value={note} onChange={(e) => setNote(e.target.value)} className={`${inputCls} w-full`} />
            </label>
            <button type="submit" disabled={busy}
                    className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-white bg-indigo-600 rounded hover:bg-indigo-700 disabled:opacity-50">
              {busy ? <Loader className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1.5" />}
              {t('agentDetails.experiment.start')}
            </button>
          </form>
          {experiment && report && (
            <div className="mt-4">
              <div className="text-xs font-medium text-gray-600 mb-1">
                {t('agentDetails.experiment.lastEnded', { when: experiment.ended_at ? new Date(experiment.ended_at).toLocaleString() : '' })}
              </div>
              <Report report={report} t={t} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
