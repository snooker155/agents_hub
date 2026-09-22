import { useCallback, useEffect, useState } from 'react';
import { Plus, RefreshCw, Send, Trash2, Wifi } from 'lucide-react';
import {
  listNotifyEndpoints, createNotifyEndpoint, deleteNotifyEndpoint, testNotifyEndpoint,
  listNotifyRules, createNotifyRule, updateNotifyRule, deleteNotifyRule,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useWorkspace } from '../workspace';

// ── Webhooks tab: outbound endpoints + alert rules ───────────────────────────
//
// The third connector direction, alongside Telegram and Git: this hub POSTs
// out to a URL the operator owns, instead of using an SDK for a service it
// already knows the shape of. Two lists, both scoped to the active workspace:
// where an event can be delivered (a webhook or a Slack incoming-webhook),
// and when the workspace should raise one on its own (a failed run, spend
// past a threshold) without an agent or a person asking for it.

const RULE_KINDS = ['run_failed', 'spend_daily_over', 'spend_run_over'];
const CHANNELS = ['dashboard', 'telegram', 'slack', 'webhook'];

function EndpointForm({ workspace, onCreated }) {
  const { t } = useI18n();
  const [kind, setKind] = useState('webhook');
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleAdd = async () => {
    if (!url.trim()) return;
    setSaving(true);
    setError('');
    try {
      const payload = { kind, url: url.trim(), events: ['notification'], enabled: true };
      if (kind === 'webhook' && secret.trim()) payload.secret = secret.trim();
      const { data } = await createNotifyEndpoint(payload, workspace);
      onCreated(data.endpoint);
      setUrl('');
      setSecret('');
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-wrap items-end gap-2 pt-2 border-t border-gray-100">
      <div>
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.kind')}</label>
        <select value={kind} onChange={(e) => setKind(e.target.value)} className={`${inputCls} w-28`}>
          <option value="webhook">{t('connectors.webhooks.kindWebhook')}</option>
          <option value="slack">{t('connectors.webhooks.kindSlack')}</option>
        </select>
      </div>
      <div className="flex-1 min-w-[200px]">
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.url')}</label>
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com/hook"
          className={inputCls}
        />
      </div>
      {kind === 'webhook' && (
        <div className="flex-1 min-w-[160px]">
          <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.secret')}</label>
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder={t('connectors.webhooks.secretPlaceholder')}
            className={inputCls}
            autoComplete="new-password"
          />
        </div>
      )}
      <button
        type="button"
        onClick={handleAdd}
        disabled={saving || !url.trim()}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
      >
        {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
        {t('connectors.webhooks.addEndpoint')}
      </button>
      {error && <div className="w-full text-sm text-red-600">{error}</div>}
    </div>
  );
}

function EndpointsSection({ workspace }) {
  const { t } = useI18n();
  const [endpoints, setEndpoints] = useState([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listNotifyEndpoints(workspace);
      setEndpoints(data.endpoints || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (id) => {
    if (!window.confirm(t('connectors.webhooks.confirmRemoveEndpoint'))) return;
    try {
      await deleteNotifyEndpoint(id, workspace);
      setEndpoints((items) => items.filter((e) => e.id !== id));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  const handleTest = async (id) => {
    setTesting(id);
    setTestResult(null);
    try {
      await testNotifyEndpoint(id, workspace);
      setTestResult({ id, ok: true });
    } catch (e) {
      setTestResult({ id, ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(null);
    }
  };

  return (
    <SectionCard title={`${t('connectors.webhooks.endpoints')} (${endpoints.length})`}>
      <p className="text-sm text-gray-600">{t('connectors.webhooks.endpointsHint')}</p>
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
      {loading ? (
        <div className="flex items-center justify-center py-6">
          <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />
        </div>
      ) : endpoints.length === 0 ? (
        <div className="text-sm text-gray-500 py-2">{t('connectors.webhooks.noEndpoints')}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase">
                <th className="py-2 pr-3">{t('connectors.webhooks.kind')}</th>
                <th className="py-2 pr-3">{t('connectors.webhooks.url')}</th>
                <th className="py-2 pr-3">{t('connectors.webhooks.secret')}</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map((ep) => (
                <tr key={ep.id} className="border-t border-gray-100">
                  <td className="py-2 pr-3">
                    <span className="text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200 px-1.5 py-0.5 rounded uppercase">
                      {ep.kind}
                    </span>
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs text-gray-700 max-w-xs truncate">{ep.url}</td>
                  <td className="py-2 pr-3 font-mono text-xs text-gray-400">{ep.secret || '—'}</td>
                  <td className="py-2 text-right whitespace-nowrap">
                    <button
                      type="button"
                      onClick={() => handleTest(ep.id)}
                      disabled={testing === ep.id}
                      className="inline-flex items-center gap-1 text-xs text-gray-700 hover:bg-gray-50 border border-gray-300 rounded-md px-2 py-1 mr-2"
                    >
                      {testing === ep.id ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                      {t('connectors.webhooks.test')}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(ep.id)}
                      className="inline-flex items-center gap-1 text-xs text-red-600 hover:bg-red-50 border border-red-200 rounded-md px-2 py-1"
                    >
                      <Trash2 className="w-3 h-3" /> {t('settings.remove')}
                    </button>
                    {testResult && testResult.id === ep.id && (
                      <div className={`mt-1 text-xs ${testResult.ok ? 'text-green-600' : 'text-red-600'}`}>
                        {testResult.ok ? t('connectors.webhooks.testSent') : `${t('settings.testFailed')}: ${testResult.error}`}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <EndpointForm workspace={workspace} onCreated={(ep) => setEndpoints((items) => [...items, ep])} />
    </SectionCard>
  );
}

function RuleForm({ workspace, onCreated }) {
  const { t } = useI18n();
  const [kind, setKind] = useState('run_failed');
  const [threshold, setThreshold] = useState('1.00');
  const [agentId, setAgentId] = useState('');
  const [channels, setChannels] = useState(['dashboard']);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const toggleChannel = (c) => {
    setChannels((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));
  };

  const handleAdd = async () => {
    setSaving(true);
    setError('');
    try {
      const payload = {
        kind,
        threshold_usd: kind === 'run_failed' ? 0 : parseFloat(threshold) || 0,
        agent_id: agentId.trim() || null,
        channels: channels.length ? channels : ['dashboard'],
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
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.ruleKind')}</label>
        <select value={kind} onChange={(e) => setKind(e.target.value)} className={`${inputCls} w-40`}>
          {RULE_KINDS.map((k) => (
            <option key={k} value={k}>{t(`connectors.webhooks.ruleKinds.${k}`)}</option>
          ))}
        </select>
      </div>
      {kind !== 'run_failed' && (
        <div>
          <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.threshold')}</label>
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
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.agentFilter')}</label>
        <input
          type="text"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          placeholder={t('connectors.webhooks.agentFilterPlaceholder')}
          className={`${inputCls} w-36`}
        />
      </div>
      <div>
        <label className="text-xs font-medium text-gray-500 mb-1 block">{t('connectors.webhooks.channels')}</label>
        <div className="flex items-center gap-2 h-[38px]">
          {CHANNELS.map((c) => (
            <label key={c} className="flex items-center gap-1 text-xs text-gray-600">
              <input type="checkbox" checked={channels.includes(c)} onChange={() => toggleChannel(c)} />
              {t(`connectors.webhooks.channelLabels.${c}`)}
            </label>
          ))}
        </div>
      </div>
      <button
        type="button"
        onClick={handleAdd}
        disabled={saving}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
      >
        {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
        {t('connectors.webhooks.addRule')}
      </button>
      {error && <div className="w-full text-sm text-red-600">{error}</div>}
    </div>
  );
}

function RulesSection({ workspace }) {
  const { t } = useI18n();
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listNotifyRules(workspace);
      setRules(data.rules || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => { load(); }, [load]);

  const handleToggle = async (rule) => {
    try {
      const { data } = await updateNotifyRule(rule.id, { enabled: !rule.enabled }, workspace);
      setRules((items) => items.map((r) => (r.id === rule.id ? data.rule : r)));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm(t('connectors.webhooks.confirmRemoveRule'))) return;
    try {
      await deleteNotifyRule(id, workspace);
      setRules((items) => items.filter((r) => r.id !== id));
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    }
  };

  return (
    <SectionCard title={`${t('connectors.webhooks.rules')} (${rules.length})`}>
      <p className="text-sm text-gray-600">{t('connectors.webhooks.rulesHint')}</p>
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
      {loading ? (
        <div className="flex items-center justify-center py-6">
          <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />
        </div>
      ) : rules.length === 0 ? (
        <div className="text-sm text-gray-500 py-2">{t('connectors.webhooks.noRules')}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase">
                <th className="py-2 pr-3">{t('connectors.webhooks.ruleKind')}</th>
                <th className="py-2 pr-3">{t('connectors.webhooks.threshold')}</th>
                <th className="py-2 pr-3">{t('connectors.webhooks.agentFilter')}</th>
                <th className="py-2 pr-3">{t('connectors.webhooks.channels')}</th>
                <th className="py-2 pr-3">{t('connectors.webhooks.enabled')}</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className="border-t border-gray-100">
                  <td className="py-2 pr-3 text-gray-800">{t(`connectors.webhooks.ruleKinds.${rule.kind}`)}</td>
                  <td className="py-2 pr-3 text-gray-700">{rule.kind === 'run_failed' ? '—' : `$${Number(rule.threshold_usd || 0).toFixed(2)}`}</td>
                  <td className="py-2 pr-3 text-gray-500 font-mono text-xs">{rule.agent_id || '—'}</td>
                  <td className="py-2 pr-3 text-gray-500 text-xs">{(rule.channels || []).join(', ')}</td>
                  <td className="py-2 pr-3">
                    <label className="inline-flex items-center cursor-pointer">
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

export default function WebhooksConnector() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-2 text-sm text-gray-600 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">
        <Wifi className="w-4 h-4 mt-0.5 text-indigo-500 shrink-0" />
        <span>{t('connectors.webhooks.intro')}</span>
      </div>
      <EndpointsSection workspace={selectedWorkspace} />
      <RulesSection workspace={selectedWorkspace} />
    </div>
  );
}
