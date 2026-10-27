import { useEffect, useState } from 'react';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  listNotifyRules, createNotifyRule, updateNotifyRule, deleteNotifyRule,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useWorkspace } from '../workspace';
import PageLoader from '../PageLoader';

// ── Alert rules tab ──────────────────────────────────────────────────────────
//
// When the workspace should raise a notification in the bell on its own (a
// failed run, spend past a threshold) without an agent or a person asking for
// it. Scoped to the active workspace; the inbox is the only channel.

const RULE_KINDS = ['run_failed', 'spend_daily_over', 'spend_run_over'];
// run_failed reacts to any failure and has no dollar threshold of its own.
const NO_THRESHOLD_KINDS = ['run_failed'];

/** The threshold column: dollars for spend rules. */
function ruleThreshold(rule) {
  if (NO_THRESHOLD_KINDS.includes(rule.kind)) return '—';
  return `$${Number(rule.threshold_usd || 0).toFixed(2)}`;
}

function RuleForm({ workspace, onCreated }) {
  const { t } = useI18n();
  const [kind, setKind] = useState('run_failed');
  const [threshold, setThreshold] = useState('1.00');
  const [agentId, setAgentId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleAdd = async () => {
    setSaving(true);
    setError('');
    try {
      const payload = {
        kind,
        threshold_usd: NO_THRESHOLD_KINDS.includes(kind) ? 0 : parseFloat(threshold) || 0,
        agent_id: agentId.trim() || null,
        channels: ['dashboard'],
        enabled: true,
      };
      const { data } = await createNotifyRule(payload, workspace);
      onCreated(data.rule);
      setAgentId('');
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-wrap items-end gap-2 pt-2 border-t border-gray-100">
      <div>
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.alerts.ruleKind')}</label>
        <select value={kind} onChange={(e) => setKind(e.target.value)} className={`${inputCls} w-40`}>
          {RULE_KINDS.map((k) => (
            <option key={k} value={k}>{t(`connectors.alerts.ruleKinds.${k}`)}</option>
          ))}
        </select>
      </div>
      {!NO_THRESHOLD_KINDS.includes(kind) && (
        <div>
          <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.alerts.threshold')}</label>
          <input
            type="number"
            step="0.01"
            min="0"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            className={`${inputCls} w-24`}
          />
        </div>
      )}
      <div>
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.alerts.agentFilter')}</label>
        <input
          type="text"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          placeholder={t('connectors.alerts.agentFilterPlaceholder')}
          className={`${inputCls} w-36`}
        />
      </div>
      <button
        type="button"
        onClick={handleAdd}
        disabled={saving}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
      >
        {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
        {t('connectors.alerts.addRule')}
      </button>
      {error && <div className="w-full text-sm text-red-600">{error}</div>}
    </div>
  );
}

function RulesSection({ workspace }) {
  const { t } = useI18n();
  const [rules, setRules] = useState([]);
  const [loadedFor, setLoadedFor] = useState(null);
  const loading = loadedFor !== workspace;
  const [error, setError] = useState('');

  // The list is only loaded here, so the effect owns the request and the
  // ignore flag drops an answer for a workspace that was left meanwhile.
  useEffect(() => {
    let ignore = false;
    const load = async () => {
      try {
        const { data } = await listNotifyRules(workspace);
        if (!ignore) setRules(data.rules || []);
      } catch (e) {
        if (!ignore) setError(e.response?.data?.detail || e.message);
      } finally {
        if (!ignore) setLoadedFor(workspace);
      }
    };
    load();
    return () => { ignore = true; };
  }, [workspace]);

  const handleToggle = async (rule) => {
    try {
      const { data } = await updateNotifyRule(rule.id, { enabled: !rule.enabled }, workspace);
      setRules((items) => items.map((r) => (r.id === rule.id ? data.rule : r)));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm(t('connectors.alerts.confirmRemoveRule'))) return;
    try {
      await deleteNotifyRule(id, workspace);
      setRules((items) => items.filter((r) => r.id !== id));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  return (
    <SectionCard title={`${t('connectors.alerts.rules')} (${rules.length})`}>
      <p className="text-sm text-gray-600">{t('connectors.alerts.intro')}</p>
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
      {loading ? (
        <PageLoader size="sm" />
      ) : rules.length === 0 ? (
        <div className="text-sm text-gray-500 py-2">{t('connectors.alerts.noRules')}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase">
                <th className="py-2 pr-3">{t('connectors.alerts.ruleKind')}</th>
                <th className="py-2 pr-3">{t('connectors.alerts.threshold')}</th>
                <th className="py-2 pr-3">{t('connectors.alerts.agentFilter')}</th>
                <th className="py-2 pr-3">{t('connectors.alerts.enabled')}</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className="border-t border-gray-100">
                  <td className="py-2 pr-3 text-gray-800">{t(`connectors.alerts.ruleKinds.${rule.kind}`)}</td>
                  <td className="py-2 pr-3 text-gray-700">{ruleThreshold(rule)}</td>
                  <td className="py-2 pr-3 text-gray-500 font-mono text-xs">{rule.agent_id || '—'}</td>
                  <td className="py-2 pr-3">
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        className="sr-only peer"
                        checked={rule.enabled}
                        onChange={() => handleToggle(rule)}
                      />
                      <span className="w-9 h-5 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 relative transition-colors">
                        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${rule.enabled ? 'translate-x-4' : ''}`} />
                      </span>
                    </label>
                  </td>
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      onClick={() => handleDelete(rule.id)}
                      className="inline-flex items-center gap-1 text-xs text-red-600 hover:bg-red-50 border border-red-200 rounded-md px-2 py-1"
                    >
                      <Trash2 className="w-3 h-3" /> {t('settings.remove')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <RuleForm workspace={workspace} onCreated={(r) => setRules((items) => [...items, r])} />
    </SectionCard>
  );
}

export default function AlertRulesConnector() {
  const { selectedWorkspace } = useWorkspace();

  return (
    <div className="space-y-5">
      <RulesSection workspace={selectedWorkspace} />
    </div>
  );
}
