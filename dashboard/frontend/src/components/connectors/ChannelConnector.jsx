import { useCallback, useEffect, useState } from 'react';
import { MessageSquare, Plus, RefreshCw, Save, Trash2, Wifi } from 'lucide-react';
import {
  listChannels, getChannelConfig, updateChannelConfig, deleteChannelConfig, testChannel,
  getChannelStatus, getChannelBindings, createChannelBinding, deleteChannelBinding,
  getAgents, listFlows, getGmailStatus,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useLiveRefetch } from '../stream';
import { useWorkspace } from '../workspace';
import PageLoader from '../PageLoader';
import MailPresetPicker from './MailPresetPicker';
import { presetForAddress } from './mailPresets';
import GmailSignInNote from './GmailSignInNote';
import { ConnectorSourceBadge, ConnectorDefinedIn, ConnectorSourceActions } from './ConnectorSource';

// One tab for every chat channel that is not Telegram: Slack, Discord,
// Microsoft Teams, mail. The backend describes each channel's config fields
// (GET /api/channels), and the rest of the page is the same for all of them:
// the loop's status, the chat allowlist, the bindings table. Telegram keeps
// its own tab because its routes predate this generic one.

const btnPrimary = 'flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';
const btnSecondary = 'flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50';
const btnDanger = 'flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';

/** Whether a field's ``{other: value}`` condition (hidden_when, optional_when) holds. */
const when = (cond, values) => Object.entries(cond || {}).some(([k, v]) => String(values?.[k] ?? '') === String(v));

function FieldInput({ field, value, isSet, onChange, t, name, required, disabled }) {
  const label = t(`connectors.fields.${name}.${field.key}`, { defaultValue: field.key });
  const hint = t(`connectors.fieldHints.${name}.${field.key}`, { defaultValue: '' });
  const common = { className: inputCls, value: value ?? '', onChange: (e) => onChange(e.target.value), disabled };
  let input;
  if (field.kind === 'select') {
    input = (
      <select {...common}>
        {field.options.map((o) => <option key={o} value={o}>{t(`connectors.options.${name}.${field.key}.${o}`, { defaultValue: o })}</option>)}
      </select>
    );
  } else if (field.kind === 'textarea') {
    input = <textarea rows={3} {...common} placeholder={field.placeholder} />;
  } else if (field.secret) {
    input = (
      <input
        type="password"
        autoComplete="new-password"
        {...common}
        placeholder={isSet ? t('settings.keepExistingToken') : field.placeholder}
      />
    );
  } else {
    input = <input type={field.kind === 'number' ? 'number' : 'text'} {...common} placeholder={field.placeholder} />;
  }
  return (
    <div>
      <label className="text-sm font-medium text-gray-700 mb-1 block">
        {label}
        {required && <span className="text-red-500"> *</span>}
        {field.secret && isSet && <span className="text-gray-400 font-normal"> ({t('settings.currentlySet')})</span>}
      </label>
      {hint && <p className="text-xs text-gray-500 mb-1">{hint}</p>}
      {input}
    </div>
  );
}

// Non-secret values come from the server; secrets start empty (write-only).
function initialValues(fields, config) {
  const next = {};
  fields.forEach((f) => { if (!f.secret) next[f.key] = config?.[f.key] ?? ''; });
  return next;
}

export function ConfigForm({
  name, fields: allFields, presets = [], config, onSave, onClear, onTest, saving, testing,
  testResult, t, readOnly, workspace,
}) {
  // A hidden field is written by the backend (an OAuth callback), never typed.
  const fields = allFields.filter((f) => f.kind !== 'hidden');
  const [values, setValues] = useState(() => initialValues(fields, config));
  // The mail channel's provider presets (connectors/mail/presets.py): the
  // picked one is read back from the IMAP host, and a typed address with a
  // known domain fills the hosts when none is set yet.
  const presetId = presets.find((p) => p.channel?.imap_host === values.imap_host)?.id || '';
  const applyPreset = (p) => { if (p) setValues((vs) => ({ ...vs, ...p.channel })); };
  // A Google sign in (the mail channel's auth_mode) shows whether the Google
  // connector can reach Gmail, fetched once the mode is picked.
  const hasGoogleMode = fields.some((f) => f.key === 'auth_mode' && (f.options || []).includes('google'));
  const googleMode = hasGoogleMode && values.auth_mode === 'google';
  const [gmail, setGmail] = useState(null);
  useEffect(() => {
    if (!googleMode) return undefined;
    let live = true;
    getGmailStatus(workspace).then((r) => { if (live) setGmail(r.data); }).catch(() => { if (live) setGmail(null); });
    return () => { live = false; };
  }, [googleMode, workspace]);
  const setValue = (key, v) => setValues((vs) => {
    const next = { ...vs, [key]: v };
    if ((key === 'imap_user' || key === 'from_address') && !vs.imap_host) {
      const guess = presetForAddress(presets, v);
      if (guess) Object.assign(next, guess.channel);
    }
    return next;
  });
  // A save returns fresh config; re-seed the form from it so a cleared
  // secret or a server side default shows up without a reload.
  const [seenConfig, setSeenConfig] = useState(config);
  if (config !== seenConfig) {
    setSeenConfig(config);
    setValues(initialValues(fields, config));
  }

  const secretsSet = fields.filter((f) => f.secret && config?.[`has_${f.key}`]);
  const submit = () => {
    const payload = {};
    fields.forEach((f) => {
      const v = values[f.key];
      if (f.secret) { if (v && String(v).trim()) payload[f.key] = String(v).trim(); return; }
      if (v !== undefined) payload[f.key] = f.kind === 'number' && v !== '' ? Number(v) : v;
    });
    onSave(payload);
    setValues((vs) => {
      const cleared = { ...vs };
      fields.forEach((f) => { if (f.secret) cleared[f.key] = ''; });
      return cleared;
    });
  };

  return (
    <div className="space-y-3">
      {!readOnly && (
        <>
          <MailPresetPicker presets={presets} value={presetId} onPick={applyPreset} t={t} inputCls={inputCls}
            googleActive={googleMode}
            onUseGoogle={hasGoogleMode ? () => setValue('auth_mode', 'google') : undefined} />
          {googleMode && <GmailSignInNote status={gmail} t={t} />}
        </>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {fields.filter((f) => !when(f.hidden_when, values)).map((f) => (
          <FieldInput
            required={f.required && !when(f.optional_when, values)}
            key={f.key}
            field={f}
            name={name}
            t={t}
            value={values[f.key]}
            isSet={Boolean(config?.[`has_${f.key}`])}
            onChange={(v) => setValue(f.key, v)}
            disabled={readOnly}
          />
        ))}
      </div>
      {!readOnly && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={submit} disabled={saving} className={btnPrimary}>
              {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {t('common.save')}
            </button>
            {onTest && (
              <button type="button" onClick={onTest} disabled={testing} className={btnSecondary}>
                {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
                {t('settings.testConnection')}
              </button>
            )}
            {secretsSet.length > 0 && onClear && (
              <button type="button" onClick={() => onClear(secretsSet.map((f) => f.key))} disabled={saving} className={btnDanger}>
                <Trash2 className="w-3.5 h-3.5" /> {t('connectors.clearSecrets')}
              </button>
            )}
          </div>
          {testResult && (
            <div className={`text-sm rounded-lg px-3 py-2 ${testResult.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
              {testResult.ok
                ? <>{t('settings.connectedAs')} <strong>{testResult.identity || 'ok'}</strong></>
                : <>{t('settings.testFailed')}: {testResult.error}</>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// A new binding goes to the current workspace, like everything else made on
// this page; the bindings table below still lists every workspace's chats.
function BindingForm({ name, onCreated, t }) {
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const [agents, setAgents] = useState([]);
  const [flows, setFlows] = useState([]);
  const [chatKey, setChatKey] = useState('');
  const [target, setTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setTarget('');
    getAgents(workspace).then((r) => setAgents(r.data || [])).catch(() => setAgents([]));
    listFlows(workspace).then((r) => setFlows(r.data || [])).catch(() => setFlows([]));
  }, [workspace]);

  const submit = async () => {
    if (!chatKey.trim()) return;
    setBusy(true);
    setError('');
    try {
      const payload = { chat_key: chatKey.trim(), workspace };
      if (target.startsWith('agent:')) payload.agent_id = target.slice(6);
      if (target.startsWith('flow:')) payload.flow_id = target.slice(5);
      const { data } = await createChannelBinding(name, payload, workspace);
      setChatKey('');
      onCreated(data);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-gray-100 pt-3">
      <p className="text-xs text-gray-500">{t('connectors.channels.bindHint')}</p>
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
      <div className="grid gap-2 md:grid-cols-3">
        <input
          className={inputCls}
          value={chatKey}
          onChange={(e) => setChatKey(e.target.value)}
          placeholder={t(`connectors.chatKey.${name}`, { defaultValue: t('connectors.channels.chatKey') })}
        />
        <select className={inputCls} value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">{t('connectors.channels.pickLater')}</option>
          {agents.map((a) => <option key={a.id} value={`agent:${a.id}`}>{a.name || a.id}</option>)}
          {flows.map((f) => <option key={f.id} value={`flow:${f.id}`}>{t('settings.flow')}: {f.name || f.id}</option>)}
        </select>
        <button type="button" onClick={submit} disabled={busy || !chatKey.trim()} className={btnPrimary}>
          <Plus className="w-3.5 h-3.5" /> {t('connectors.channels.bind')}
        </button>
      </div>
    </div>
  );
}

export default function ChannelConnector({ name }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const isDefaultWorkspace = workspace === 'default';
  const [loading, setLoading] = useState(true);
  const [spec, setSpec] = useState(null);
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState({});
  const [bindings, setBindings] = useState([]);
  const [allowed, setAllowed] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');
  // Whether "Define for this workspace" was clicked: the inherited, read
  // only form becomes editable, and saving is what creates this workspace's
  // own definition (connectors/channels/store.py).
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    setEditing(false);
    try {
      const [specs, cfg, st, bs] = await Promise.all([
        listChannels(), getChannelConfig(name, workspace), getChannelStatus(name, workspace),
        getChannelBindings(name, workspace),
      ]);
      setSpec((specs.data || []).find((s) => s.name === name) || { name, fields: [] });
      setConfig(cfg.data);
      setAllowed((cfg.data?.allowed || []).join('\n'));
      setStatus(st.data || {});
      setBindings(bs.data || []);
    } catch (e) {
      setError(`${t('connectors.channels.loadFailed')}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [name, workspace, t]);

  useEffect(() => { load(); }, [load]);

  const refreshStatus = useCallback(async () => {
    try {
      const [st, bs] = await Promise.all([getChannelStatus(name, workspace), getChannelBindings(name, workspace)]);
      setStatus(st.data || {});
      setBindings(bs.data || []);
    } catch { /* wait for the next event */ }
  }, [name, workspace]);
  useLiveRefetch(refreshStatus, { type: `channel_${name}.changed` });

  const save = async (payload) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const { data } = await updateChannelConfig(name, payload, workspace);
      setConfig(data);
      setAllowed((data.allowed || []).join('\n'));
      setEditing(false);
      await refreshStatus();
    } catch (e) {
      setError(`${t('settings.errors.save')}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await testChannel(name, workspace);
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  const removeDefinition = async () => {
    if (!window.confirm(t('connectors.source.confirmRemove'))) return;
    setSaving(true);
    setError('');
    try {
      const { data } = await deleteChannelConfig(name, workspace);
      setConfig(data);
      setAllowed((data.allowed || []).join('\n'));
    } catch (e) {
      setError(`${t('settings.errors.save')}: ${e.response?.data?.detail || e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const removeBinding = async (key) => {
    if (!window.confirm(t('connectors.channels.confirmRemoveBinding', { chatKey: key }))) return;
    try {
      await deleteChannelBinding(name, key, workspace);
      setBindings((bs) => bs.filter((b) => b.chat_key !== key));
    } catch (e) {
      setError(`${t('settings.errors.removeBinding')}: ${e.response?.data?.detail || e.message}`);
    }
  };

  if (loading) return <PageLoader size="sm" />;

  const fields = spec?.fields || [];
  const inboundUrl = config?.inbound_url
    ? `${window.location.origin}${config.inbound_url}`
    : null;
  const readOnly = !isDefaultWorkspace && config?.source === 'default' && !editing;

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      <SectionCard
        title={t(`connectors.channels.title.${name}`)}
        actions={<ConnectorSourceBadge source={config?.source} />}
      >
        <p className="text-sm text-gray-600">{t(`connectors.channels.intro.${name}`)}</p>
        {inboundUrl && (
          <p className="text-xs text-gray-500">
            {t('connectors.channels.inboundUrl')}: <code className="bg-gray-100 rounded px-1">{inboundUrl}</code>
          </p>
        )}
        {isDefaultWorkspace ? (
          <ConnectorDefinedIn definedIn={config?.defined_in} />
        ) : (
          <ConnectorSourceActions
            payload={config}
            isDefaultWorkspace={isDefaultWorkspace}
            editing={editing}
            onDefine={() => setEditing(true)}
            onRemove={removeDefinition}
            busy={saving}
          />
        )}
        <ConfigForm
          name={name}
          fields={fields}
          presets={spec?.presets || []}
          config={config?.config}
          onSave={(payload) => save({ config: payload })}
          onClear={(keys) => save({ clear: keys, enabled: false })}
          onTest={test}
          saving={saving}
          testing={testing}
          testResult={testResult}
          t={t}
          readOnly={readOnly}
          workspace={workspace}
        />
        {config?.has_loop && (
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-100">
            <div>
              <label className="text-sm font-medium text-gray-700">{t('connectors.channels.enabled')}</label>
              <p className="text-xs text-gray-500">{t('connectors.channels.enabledHint')}</p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                className="sr-only peer"
                checked={Boolean(config?.enabled)}
                disabled={saving || !config?.configured}
                onChange={(e) => save({ enabled: e.target.checked })}
              />
              <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 peer-disabled:opacity-50 relative transition-colors">
                <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${config?.enabled ? 'translate-x-5' : ''}`} />
              </span>
            </label>
          </div>
        )}
        {!config?.has_loop && (
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-100">
            <div>
              <label className="text-sm font-medium text-gray-700">{t('connectors.channels.enabled')}</label>
              <p className="text-xs text-gray-500">{t('connectors.channels.enabledInboundHint')}</p>
            </div>
            <input
              type="checkbox"
              checked={Boolean(config?.enabled)}
              disabled={saving || !config?.configured}
              onChange={(e) => save({ enabled: e.target.checked })}
            />
          </div>
        )}
      </SectionCard>

      {config?.has_loop && (
        <SectionCard title={t('settings.pollerStatus')}>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${status.running ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
              <span className="text-gray-700">{status.running ? t('settings.running') : t('settings.stopped')}</span>
            </div>
            <div className="text-gray-700">
              {t('connectors.channels.identity')}: <strong>{status.identity || '—'}</strong>
            </div>
            <div className="text-gray-700">
              {t('settings.telegram.lastPoll')}: <span className="text-gray-500">{status.last_poll ? new Date(status.last_poll).toLocaleString() : '—'}</span>
            </div>
            <div className="text-gray-700">
              {t('settings.telegram.lastError')}: <span className="text-red-600">{status.last_error || '—'}</span>
            </div>
          </div>
        </SectionCard>
      )}

      <SectionCard title={t('connectors.channels.allowlist')}>
        <p className="text-sm text-gray-600">{t(`connectors.channels.allowlistHint.${name}`)}</p>
        <textarea
          rows={3}
          className={inputCls}
          value={allowed}
          onChange={(e) => setAllowed(e.target.value)}
          placeholder={t(`connectors.chatKey.${name}`, { defaultValue: t('connectors.channels.chatKey') })}
        />
        <button
          type="button"
          onClick={() => save({ allowed: allowed.split(/[\n,]/).map((s) => s.trim()).filter(Boolean) })}
          disabled={saving}
          className={btnSecondary}
        >
          <Save className="w-3.5 h-3.5" /> {t('connectors.channels.saveAllowlist')}
        </button>
      </SectionCard>

      <SectionCard title={`${t('settings.telegram.chatBindings')} (${bindings.length})`}>
        <p className="text-sm text-gray-600">{t('connectors.channels.bindingsHint')}</p>
        {bindings.length === 0 ? (
          <div className="text-sm text-gray-500 flex items-center gap-2 py-3">
            <MessageSquare className="w-4 h-4" /> {t('settings.noBindingsYet')}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 uppercase">
                  <th className="py-2 pr-3">{t('settings.chat')}</th>
                  <th className="py-2 pr-3">{t('settings.target')}</th>
                  <th className="py-2 pr-3">{t('settings.workspace')}</th>
                  <th className="py-2 pr-3">{t('settings.lastMessage')}</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {bindings.map((b) => (
                  <tr key={b.chat_key} className="border-t border-gray-100">
                    <td className="py-2 pr-3 font-mono text-xs text-gray-700">
                      {b.title || b.chat_key}
                      <div className="text-[10px] text-gray-400">{b.chat_key}</div>
                    </td>
                    <td className="py-2 pr-3 text-gray-800">
                      {b.flow_id ? (
                        <span className="inline-flex items-center gap-1">
                          <span className="text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200 px-1.5 py-0.5 rounded">{t('settings.flow')}</span>
                          {b.flow_name || b.flow_id}
                        </span>
                      ) : b.agent_id ? (
                        <>{b.agent_name || b.agent_id}<div className="text-[10px] text-gray-400">{b.agent_id}</div></>
                      ) : (
                        <span className="text-gray-400 italic">{t('settings.notSet')}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-gray-700">{b.workspace || '—'}</td>
                    <td className="py-2 pr-3 text-xs text-gray-500">
                      {b.last_message_at ? new Date(b.last_message_at).toLocaleString() : '—'}
                    </td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => removeBinding(b.chat_key)}
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
        <BindingForm
          name={name}
          t={t}
          onCreated={(b) => {
            setBindings((bs) => [...bs.filter((x) => x.chat_key !== b.chat_key), b]);
            refreshStatus();
            setAllowed((cur) => (cur.split(/[\n,]/).map((s) => s.trim()).includes(b.chat_key) ? cur : `${cur}\n${b.chat_key}`.trim()));
          }}
        />
      </SectionCard>
    </div>
  );
}
