import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader, ShieldCheck } from 'lucide-react';
import { getAgentToolPolicy, getToolPolicyDecisions, updateAgentToolPolicy } from '../../api/toolPolicy';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';

const MODES = ['always_allow', 'always_ask', 'auto'];
const INHERIT = '';

const selectCls = 'border border-gray-200 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';

const DECISION_CLS = {
  run: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  deny: 'bg-red-50 text-red-700 border-red-200',
  ask: 'bg-amber-50 text-amber-800 border-amber-200',
};

const sameMap = (a, b) => {
  const ka = Object.keys(a || {});
  const kb = Object.keys(b || {});
  return ka.length === kb.length && ka.every((k) => a[k] === b[k]);
};

/**
 * The agent's per-tool permission policy (`AgentSpec.tool_policy`, see
 * tools/permission_policy.py): a default for every tool ("*") and a mode per
 * tool on the agent's record, each one inheriting when left empty.
 *
 * The effective mode shown next to each tool comes from the backend, which
 * resolves agent entries, then the workspace policy, then the approval list,
 * so the card says exactly what a call will get and why. Below it, the
 * newest decisions the policy recorded for this agent.
 */
export default function ToolPolicyCard({ agentId, agent, onSaved }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [decisions, setDecisions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const workspace = selectedWorkspace || agent?.owner_workspace || undefined;
  // Read through a ref so a new `t` (a language switch) does not refetch.
  const tRef = useRef(t);
  tRef.current = t;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data: body } = await getAgentToolPolicy(agentId, workspace);
      setData(body);
      setDraft(body?.tool_policy || {});
    } catch (e) {
      setError(e?.response?.data?.detail || tRef.current('toolPolicy.loadFailed'));
    } finally {
      setLoading(false);
    }
    try {
      const { data: rows } = await getToolPolicyDecisions({ agentId, limit: 10 });
      setDecisions(Array.isArray(rows?.decisions) ? rows.decisions : []);
    } catch {
      setDecisions([]);
    }
  }, [agentId, workspace]);

  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(() => !sameMap(draft, data?.tool_policy || {}), [draft, data]);
  const effective = useMemo(
    () => Object.fromEntries((data?.effective || []).map((row) => [row.tool, row])),
    [data],
  );
  const tools = (data?.effective || []).map((row) => row.tool);
  // Tools an ``mcp:<server>`` group on the record stands for (the backend
  // expands it from the server's last connect): shown with their group.
  const groupOf = useMemo(() => {
    const out = {};
    for (const [alias, ids] of Object.entries(data?.groups || {})) {
      for (const id of ids || []) out[id] = alias;
    }
    return out;
  }, [data]);

  const setMode = (tool, mode) => {
    setMessage('');
    setDraft((prev) => {
      const next = { ...prev };
      if (mode === INHERIT) delete next[tool];
      else next[tool] = mode;
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data: body } = await updateAgentToolPolicy(agentId, draft, workspace);
      setData(body);
      setDraft(body?.tool_policy || {});
      setMessage(t('toolPolicy.saved'));
      if (onSaved) onSaved();
    } catch (e) {
      setError(e?.response?.data?.detail || t('toolPolicy.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const modeLabel = (mode) => t(`toolPolicy.modes.${mode}`);
  const sourceLabel = (source) => t(`toolPolicy.sources.${source}`);

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6" data-testid="tool-policy-card">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('toolPolicy.title')}</h3>
        </div>
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty || loading}
          className={`px-4 py-2 rounded-lg text-sm font-semibold ${
            saving || !dirty || loading
              ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
              : 'bg-indigo-600 text-white hover:bg-indigo-700'
          }`}
        >
          {saving ? t('common.saving') : t('toolPolicy.save')}
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('toolPolicy.intro')}</p>

      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <>
          <label className="flex flex-wrap items-center gap-2 text-sm text-gray-700 mb-1">
            <span className="font-medium">{t('toolPolicy.defaultMode')}</span>
            <select
              value={draft['*'] || INHERIT}
              onChange={(e) => setMode('*', e.target.value)}
              aria-label={t('toolPolicy.defaultMode')}
              className={selectCls}
            >
              <option value={INHERIT}>{t('toolPolicy.modes.inherit')}</option>
              {MODES.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
            </select>
            {data?.default && (
              <span className="text-xs text-gray-500">
                {t('toolPolicy.effectiveValue', {
                  mode: modeLabel(data.default.mode), source: sourceLabel(data.default.source),
                })}
              </span>
            )}
          </label>
          <p className="text-xs text-gray-500 mb-4">{t('toolPolicy.defaultHint')}</p>

          {tools.length === 0 ? (
            <p className="text-sm text-gray-500 italic mb-4">{t('toolPolicy.noTools')}</p>
          ) : (
            <div className="overflow-x-auto mb-4">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500">
                    <th className="py-1 pr-3">{t('toolPolicy.tool')}</th>
                    <th className="py-1 pr-3">{t('toolPolicy.mode')}</th>
                    <th className="py-1 pr-3">{t('toolPolicy.effective')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((tool) => {
                    const row = effective[tool];
                    const locked = row?.source === 'never_gated';
                    return (
                      <tr key={tool} className="border-t border-gray-100">
                        <td className="py-1.5 pr-3 font-mono text-xs">
                          {tool}
                          {groupOf[tool] && (
                            <span className="block font-sans text-[11px] text-gray-400">
                              {t('toolPolicy.fromGroup', { group: groupOf[tool] })}
                            </span>
                          )}
                        </td>
                        <td className="py-1.5 pr-3">
                          <select
                            value={draft[tool] || INHERIT}
                            disabled={locked}
                            onChange={(e) => setMode(tool, e.target.value)}
                            aria-label={t('toolPolicy.modeFor', { tool })}
                            className={selectCls}
                          >
                            <option value={INHERIT}>{t('toolPolicy.modes.inherit')}</option>
                            {MODES.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
                          </select>
                        </td>
                        <td className="py-1.5 pr-3 text-xs text-gray-600">
                          {row && t('toolPolicy.effectiveValue', {
                            mode: modeLabel(row.mode), source: sourceLabel(row.source),
                          })}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {dirty && <p className="text-xs text-gray-500 mt-2">{t('toolPolicy.effectiveAfterSave')}</p>}
            </div>
          )}

          <p className="text-xs text-gray-500 mb-4">
            {data?.classifier_model
              ? t('toolPolicy.classifier', { model: data.classifier_model })
              : t('toolPolicy.classifierDefault')}
          </p>

          <h4 className="text-sm font-semibold text-gray-800 mb-2">{t('toolPolicy.recent')}</h4>
          {decisions.length === 0 ? (
            <p className="text-sm text-gray-500 italic">{t('toolPolicy.noDecisions')}</p>
          ) : (
            <ul className="space-y-1.5" data-testid="tool-policy-decisions">
              {decisions.map((d, i) => (
                <li key={d.id || `${d.fingerprint}-${i}`} className="text-xs text-gray-700 flex flex-wrap items-center gap-2">
                  <span className={`px-2 py-0.5 rounded-full border font-semibold ${DECISION_CLS[d.decision] || 'bg-gray-50 text-gray-700 border-gray-200'}`}>
                    {t(`toolPolicy.decision.${d.decision}`)}
                  </span>
                  <span className="font-mono">{d.tool}</span>
                  <span className="text-gray-500">{t(`toolPolicy.by.${d.by}`)}</span>
                  {d.reason && <span className="text-gray-600">{d.reason}</span>}
                  {d.at && <span className="text-gray-400">{new Date(d.at).toLocaleString()}</span>}
                </li>
              ))}
            </ul>
          )}

          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mt-3">{error}</p>
          )}
          {message && <p className="text-xs text-gray-600 mt-3">{message}</p>}
        </>
      )}
    </div>
  );
}
