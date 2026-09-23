import React, { useCallback, useEffect, useState } from 'react';
import { Gauge, Loader, Plus, Trash2, X } from 'lucide-react';
import {
  createNotifyRule, deleteNotifyRule, getAgentOnlineEvalSummary, getAgentOnlineEvals,
  listNotifyRules, updateNotifyRule,
} from '../../api';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';
import { GRADER_FIELDS, graderSpec } from './onlineEvals';

const GRADER_KINDS = Object.keys(GRADER_FIELDS);
const SEVERITIES = ['info', 'warning', 'error'];
const CHANNELS = ['dashboard', 'telegram', 'slack', 'webhook'];

const inputCls = 'border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500';

const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(Number(v) * 100)}%`);
const score = (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(2));

function RuleForm({ agentId, workspace, onCreated }) {
  const { t } = useI18n();
  const [rate, setRate] = useState('10');
  const [minScore, setMinScore] = useState('0.7');
  const [severity, setSeverity] = useState('warning');
  const [channels, setChannels] = useState(['dashboard']);
  const [graders, setGraders] = useState([{ kind: 'llm_judge', value: '', weight: 1 }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const setRow = (index, patch) => setGraders((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    const sample = Number(rate) / 100;
    const threshold = Number(minScore);
    if (!(sample >= 0 && sample <= 1)) { setError(t('agentDetails.liveQuality.badRate')); return; }
    if (!(threshold >= 0 && threshold <= 1)) { setError(t('agentDetails.liveQuality.badScore')); return; }
    setSaving(true);
    try {
      const { data } = await createNotifyRule({
        kind: 'online_eval',
        agent_id: agentId,
        sample_rate: sample,
        min_score: threshold,
        severity,
        channels: channels.length ? channels : ['dashboard'],
        graders: graders.map(graderSpec),
        enabled: true,
      }, workspace);
      onCreated(data.rule);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || t('agentDetails.liveQuality.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-4 pt-4 border-t border-gray-100 space-y-3" aria-label={t('agentDetails.liveQuality.addRule')}>
      <div className="text-sm font-semibold text-gray-800">{t('agentDetails.liveQuality.addRule')}</div>
      <div className="flex flex-wrap gap-3 items-end">
        <label className="text-xs text-gray-600">
          <span className="block mb-1">{t('agentDetails.liveQuality.sampleRate')}</span>
          <input type="number" min="0" max="100" step="1" value={rate}
                 onChange={(e) => setRate(e.target.value)} className={`${inputCls} w-24`} />
        </label>
        <label className="text-xs text-gray-600">
          <span className="block mb-1">{t('agentDetails.liveQuality.minScore')}</span>
          <input type="number" min="0" max="1" step="0.05" value={minScore}
                 onChange={(e) => setMinScore(e.target.value)} className={`${inputCls} w-24`} />
        </label>
        <label className="text-xs text-gray-600">
          <span className="block mb-1">{t('agentDetails.liveQuality.severity')}</span>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)} className={inputCls}>
            {SEVERITIES.map((s) => <option key={s} value={s}>{t(`agentDetails.liveQuality.severities.${s}`)}</option>)}
          </select>
        </label>
        <fieldset className="text-xs text-gray-600">
          <legend className="mb-1">{t('agentDetails.liveQuality.channels')}</legend>
          <div className="flex gap-2">
            {CHANNELS.map((c) => (
              <label key={c} className="inline-flex items-center gap-1">
                <input type="checkbox" checked={channels.includes(c)}
                       onChange={() => setChannels((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]))} />
                {c}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-medium text-gray-600">{t('agentDetails.liveQuality.graders')}</div>
        {graders.map((row, index) => {
          const field = GRADER_FIELDS[row.kind];
          return (
            <div key={index} className="flex flex-wrap gap-2 items-center">
              <select aria-label={t('agentDetails.liveQuality.graderKind')} value={row.kind}
                      onChange={(e) => setRow(index, { kind: e.target.value, value: '' })} className={inputCls}>
                {GRADER_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              {field && (
                <input aria-label={t(`agentDetails.liveQuality.fields.${field}`)}
                       placeholder={t(`agentDetails.liveQuality.fields.${field}`)}
                       value={row.value} onChange={(e) => setRow(index, { value: e.target.value })}
                       className={`${inputCls} flex-1 min-w-[12rem]`} />
              )}
              <input type="number" min="0" step="0.5" value={row.weight}
                     aria-label={t('agentDetails.liveQuality.weight')}
                     onChange={(e) => setRow(index, { weight: e.target.value })} className={`${inputCls} w-20`} />
              {graders.length > 1 && (
                <button type="button" onClick={() => setGraders((rows) => rows.filter((_, i) => i !== index))}
                        className="text-gray-400 hover:text-red-600" aria-label={t('agentDetails.liveQuality.removeGrader')}>
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          );
        })}
        <button type="button" onClick={() => setGraders((rows) => [...rows, { kind: 'regex', value: '', weight: 1 }])}
                className="inline-flex items-center text-xs text-indigo-600 hover:text-indigo-800">
          <Plus className="w-3.5 h-3.5 mr-1" />{t('agentDetails.liveQuality.addGrader')}
        </button>
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}
      <button type="submit" disabled={saving}
              className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-white bg-indigo-600 rounded hover:bg-indigo-700 disabled:opacity-50">
        {saving && <Loader className="w-3.5 h-3.5 mr-1.5 animate-spin" />}
        {t('agentDetails.liveQuality.createRule')}
      </button>
    </form>
  );
}

/**
 * Live quality: what the online eval rules of this agent say about its
 * production runs (docs/evals.md, "Online evals"). A rule grades a sample of
 * finished runs in the background and raises its notification when a run
 * scores below the rule's minimum; the card shows the scores by definition
 * version, the latest graded runs, and the rules themselves.
 */
export default function LiveQualityCard({ agentId }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    Promise.all([
      getAgentOnlineEvalSummary(agentId).then(({ data }) => setSummary(data)),
      getAgentOnlineEvals(agentId, 10).then(({ data }) => setResults(data?.results || [])),
      listNotifyRules(workspace).then(({ data }) => setRules(
        (data?.rules || []).filter((r) => r.kind === 'online_eval' && (!r.agent_id || r.agent_id === agentId)),
      )),
    ])
      .catch((e) => setError(e?.response?.data?.detail || e?.message || t('agentDetails.liveQuality.loadFailed')))
      .finally(() => setLoading(false));
  }, [agentId, workspace, t]);

  // On a microtask, as useAgentVersions does: `load` raises its loading flag
  // as it starts, and a setState made inside the effect body costs a render.
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) load(); });
    return () => { cancelled = true; };
  }, [load]);

  const toggle = async (rule) => {
    try {
      const { data } = await updateNotifyRule(rule.id, { enabled: !rule.enabled }, workspace);
      setRules((items) => items.map((r) => (r.id === rule.id ? data.rule : r)));
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message);
    }
  };

  const remove = async (rule) => {
    if (!window.confirm(t('agentDetails.liveQuality.confirmRemove'))) return;
    try {
      await deleteNotifyRule(rule.id, workspace);
      setRules((items) => items.filter((r) => r.id !== rule.id));
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message);
    }
  };

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-lg font-semibold text-gray-900 flex items-center">
          <Gauge className="w-5 h-5 mr-2 text-indigo-500" />
          {t('agentDetails.liveQuality.title')}
        </h3>
        {loading && <Loader className="w-4 h-4 animate-spin text-gray-400" />}
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('agentDetails.liveQuality.description')}</p>
      {error && <div className="text-sm text-red-600 mb-3">{error}</div>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        {[
          ['graded', summary?.count ?? 0],
          ['meanScore', score(summary?.mean_score)],
          ['passRate', pct(summary?.pass_rate)],
          ['pending', summary?.pending_jobs ?? 0],
        ].map(([key, value]) => (
          <div key={key} className="bg-gray-50 border border-gray-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">{t(`agentDetails.liveQuality.${key}`)}</div>
            <div className="text-sm font-semibold text-gray-900">{value}</div>
          </div>
        ))}
      </div>

      {summary?.by_version?.length > 0 && (
        <table className="w-full text-sm mb-4">
          <thead>
            <tr className="text-left text-xs text-gray-500">
              <th className="py-1 pr-3">{t('agentDetails.liveQuality.version')}</th>
              <th className="py-1 pr-3">{t('agentDetails.liveQuality.graded')}</th>
              <th className="py-1 pr-3">{t('agentDetails.liveQuality.meanScore')}</th>
              <th className="py-1 pr-3">{t('agentDetails.liveQuality.passRate')}</th>
            </tr>
          </thead>
          <tbody>
            {summary.by_version.map((v) => (
              <tr key={`${v.definition_version}-${v.definition_hash}`} className="border-t border-gray-100">
                <td className="py-1 pr-3 font-mono text-xs">
                  {v.definition_version != null ? `v${v.definition_version}`
                    : (v.definition_hash ? v.definition_hash.slice(0, 10) : t('agentDetails.liveQuality.unknownVersion'))}
                </td>
                <td className="py-1 pr-3">{v.count}</td>
                <td className="py-1 pr-3">{score(v.mean_score)}</td>
                <td className="py-1 pr-3">{pct(v.pass_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {results.length > 0 ? (
        <div className="mb-4">
          <div className="text-xs font-medium text-gray-600 mb-1">{t('agentDetails.liveQuality.recent')}</div>
          <ul className="divide-y divide-gray-100 text-sm">
            {results.map((r) => (
              <li key={`${r.run_id}-${r.rule_id}`} className="py-1.5 flex items-center gap-3">
                <span className={`text-xs font-semibold ${r.passed ? 'text-green-700' : 'text-red-600'}`}>
                  {r.passed ? t('agentDetails.liveQuality.passed') : t('agentDetails.liveQuality.failed')}
                </span>
                <span className="font-mono text-xs text-gray-500">{String(r.run_id).slice(0, 8)}</span>
                <span className="text-gray-900">{score(r.score)}</span>
                {r.definition_version != null && <span className="text-xs text-gray-500">v{r.definition_version}</span>}
                <span className="text-xs text-gray-400 ml-auto">{r.graded_at ? new Date(r.graded_at).toLocaleString() : ''}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (!loading && <div className="text-sm text-gray-500 mb-4">{t('agentDetails.liveQuality.noResults')}</div>)}

      <div className="text-xs font-medium text-gray-600 mb-1">{t('agentDetails.liveQuality.rules')}</div>
      {rules.length === 0 ? (
        <div className="text-sm text-gray-500">{t('agentDetails.liveQuality.noRules')}</div>
      ) : (
        <ul className="divide-y divide-gray-100 text-sm">
          {rules.map((rule) => (
            <li key={rule.id} className="py-1.5 flex items-center gap-3">
              <span className="text-gray-800">
                {t('agentDetails.liveQuality.ruleSummary', {
                  rate: pct(rule.sample_rate), min: score(rule.min_score),
                  graders: (rule.graders || []).map((g) => g.kind).join(', '),
                })}
              </span>
              <label className="inline-flex items-center gap-1 text-xs text-gray-500 ml-auto">
                <input type="checkbox" checked={!!rule.enabled} onChange={() => toggle(rule)} />
                {t('agentDetails.liveQuality.enabled')}
              </label>
              <button type="button" onClick={() => remove(rule)} className="text-gray-400 hover:text-red-600"
                      aria-label={t('agentDetails.liveQuality.removeRule')}>
                <Trash2 className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {showForm ? (
        <RuleForm agentId={agentId} workspace={workspace}
                  onCreated={(rule) => { setRules((items) => [...items, rule]); setShowForm(false); }} />
      ) : (
        <button type="button" onClick={() => setShowForm(true)}
                className="mt-3 inline-flex items-center text-sm text-indigo-600 hover:text-indigo-800">
          <Plus className="w-4 h-4 mr-1" />{t('agentDetails.liveQuality.addRule')}
        </button>
      )}
    </div>
  );
}
