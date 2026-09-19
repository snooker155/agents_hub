import { useState, useEffect, useCallback } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import axios from 'axios';
import {
  RefreshCw, Key, Cpu, Activity, Wrench, Database,
  CheckCircle, AlertCircle, Wifi, Lock, Save,
  Send, Trash2, MessageSquare, GitBranch, Server, X, Boxes, Power,
  ScrollText, Settings as SettingsIcon,
} from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import {
  getWorkspaceSettingsOverrides, updateWorkspaceSettingsOverrides,
  getTelegramConfig, updateTelegramConfig, testTelegramToken,
  getTelegramStatus, getTelegramBindings, deleteTelegramBinding,
  getGitConfig, updateGitConfig, testGitConnection,
  getBlenderConfig, updateBlenderConfig, testBlenderBinary,
  getBlenderDaemons, stopBlenderDaemon, stopAllBlenderDaemons,
  updateSettings,
} from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const api = axios.create({ baseURL: 'http://localhost:8000' });


// Sections, grouped and laid out the way the Docs page lays out its chapters:
// a left-hand menu and one content column. The grouping is the point — these
// nine panels used to be nine tabs in a single row that ran off the page, with
// model settings and connectors sitting side by side for no reason.
//
// `workspaceScoped` marks the sections whose fields are workspace overrides
// (written by the page's Save button). The connector sections save themselves
// through their own endpoints, so the Save button stays out of their way.
const GROUPS = [
  {
    key: 'models',
    items: [
      { id: 'providers',     key: 'providers',     icon: Key,    workspaceScoped: true },
      { id: 'local',         key: 'local',         icon: Cpu,    workspaceScoped: true },
      { id: 'custom',        key: 'custom',        icon: Server },
    ],
  },
  {
    key: 'connectors',
    items: [
      { id: 'telegram',      key: 'telegram',      icon: Send },
      { id: 'git',           key: 'git',           icon: GitBranch },
      { id: 'blender',       key: 'blender',       icon: Boxes },
    ],
  },
  {
    key: 'system',
    items: [
      { id: 'execution',     key: 'execution',     icon: Wrench,     workspaceScoped: true },
      { id: 'rag',           key: 'rag',           icon: Database,   workspaceScoped: true },
      { id: 'observability', key: 'observability', icon: Activity,   workspaceScoped: true },
      { id: 'logging',       key: 'logging',       icon: ScrollText, workspaceScoped: true },
    ],
  },
];

const SECTIONS = GROUPS.flatMap((group) => group.items);

// Brand names stay as they are; only the descriptive rows carry a key.
const VECTOR_DBS = [
  { value: 'none',     labelKey: 'settings.vectorDbs.none' },
  { value: 'chroma',   label: 'ChromaDB' },
  { value: 'pinecone', label: 'Pinecone' },
  { value: 'qdrant',   label: 'Qdrant' },
];

const EMBEDDING_PROVIDERS = [
  { value: 'none',                  labelKey: 'settings.embeddingProviders.none' },
  { value: 'openai',                label: 'OpenAI' },
  { value: 'sentence-transformers', labelKey: 'settings.embeddingProviders.sentenceTransformers' },
  { value: 'ollama',                labelKey: 'settings.embeddingProviders.ollama' },
  { value: 'google',                labelKey: 'settings.embeddingProviders.google' },
];

// Mirrors the Literal in common/config.py — common.logging_config applies the
// chosen level to the backend process and to every agent subprocess it spawns.
const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const optionLabel = (o, t) => (o.labelKey ? t(o.labelKey) : o.label);

// Most hints are literal pip commands; the Ollama one is prose, so it is a key.
const installHint = (key, t) => {
  const hint = INSTALL_HINTS[key];
  return hint && hint.startsWith('settings.') ? t(hint) : hint;
};

const INSTALL_HINTS = {
  chroma: 'pip install chromadb',
  pinecone: 'pip install pinecone-client',
  qdrant: 'pip install qdrant-client',
  openai: 'pip install openai',
  'sentence-transformers': 'pip install sentence-transformers',
  ollama: 'settings.noExtraPackage',
  google: 'pip install google-generativeai',
};

const inputCls = "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none";

// ── Source badge — shows where a setting value comes from ─────────────────────

function SourceBadge({ fieldName, wsOverrides, envDefinedFields = [] }) {
  const { t } = useI18n();
  const hasWsOverride = Boolean(
    wsOverrides
    && Object.prototype.hasOwnProperty.call(wsOverrides, fieldName)
    && String(wsOverrides[fieldName] ?? '').trim() !== ''
  );
  if (hasWsOverride) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-1.5 py-0.5">
        <CheckCircle className="w-2.5 h-2.5" /> {t('settings.workspace')}
      </span>
    );
  }
  if (envDefinedFields.includes(fieldName)) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-1.5 py-0.5">
        <Lock className="w-2.5 h-2.5" /> {t('settings.fromEnv')}
      </span>
    );
  }
  return null;
}

// ── Telegram tab ─────────────────────────────────────────────────────────────

function TelegramTab() {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState({ enabled: false, has_token: false, bot_username: null, running: false });
  const [status, setStatus] = useState({});
  const [bindings, setBindings] = useState([]);
  const [tokenInput, setTokenInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [cfgResp, statusResp, bindingsResp] = await Promise.all([
        getTelegramConfig(),
        getTelegramStatus(),
        getTelegramBindings(),
      ]);
      setConfig(cfgResp.data);
      setStatus(statusResp.data);
      setBindings(bindingsResp.data || []);
    } catch (e) {
      setError(`${t('settings.errors.telegramLoad')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  // Poll status every 5s so the running flag and last_poll/last_error stay fresh.
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const { data } = await getTelegramStatus();
        setStatus(data);
        setConfig((c) => ({ ...c, running: data.running, bot_username: data.bot_username }));
      } catch { /* a failed poll just waits for the next tick */ }
    }, 5000);
    return () => clearInterval(id);
  }, []);

  const handleSave = async ({ enabled, clear_token } = {}) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const payload = {};
      if (tokenInput.trim()) payload.bot_token = tokenInput.trim();
      if (clear_token) payload.clear_token = true;
      if (enabled !== undefined) payload.enabled = enabled;
      const { data } = await updateTelegramConfig(payload);
      setConfig(data);
      setTokenInput('');
      // Refresh status + bindings after a save (poller may have just started/stopped).
      const [statusResp, bindingsResp] = await Promise.all([
        getTelegramStatus(),
        getTelegramBindings(),
      ]);
      setStatus(statusResp.data);
      setBindings(bindingsResp.data || []);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await testTelegramToken();
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const handleDeleteBinding = async (chatId) => {
    if (!window.confirm(t('settings.confirmRemoveBinding', { chatId }))) return;
    try {
      await deleteTelegramBinding(chatId);
      setBindings((bs) => bs.filter((b) => b.chat_id !== chatId));
    } catch (e) {
      setError(`${t('settings.errors.removeBinding')}: ` + (e.response?.data?.detail || e.message));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      <SectionCard title={t('settings.telegramBot')}>
        <p className="text-sm text-gray-600">
          {t('settings.telegram.introBefore')} <code className="text-xs bg-gray-100 rounded px-1">/agent &lt;id&gt;</code>{t('settings.telegram.introAfter')}
        </p>

        <div>
          <div className="flex items-center gap-2 mb-1">
            <label className="text-sm font-medium text-gray-700">
              {t('settings.telegram.botToken')} {config.has_token && <span className="text-gray-400 font-normal">({t('settings.currentlySet')})</span>}
            </label>
          </div>
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder={config.has_token ? t('settings.keepExistingToken') : t('settings.telegram.pasteBotToken')}
            className={inputCls}
            autoComplete="new-password"
          />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <button
              type="button"
              onClick={() => handleSave({})}
              disabled={saving || !tokenInput.trim()}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {t('settings.saveToken')}
            </button>
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !config.has_token}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
              {t('settings.testConnection')}
            </button>
            {config.has_token && (
              <button
                type="button"
                onClick={() => handleSave({ clear_token: true, enabled: false })}
                disabled={saving}
                className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" /> {t('settings.clearToken')}
              </button>
            )}
          </div>
          {testResult && (
            <div className={`mt-2 text-sm rounded-lg px-3 py-2 ${testResult.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
              {testResult.ok
                ? <>{t('settings.connectedAs')} <strong>@{testResult.bot?.username}</strong> (id {testResult.bot?.id})</>
                : <>{t('settings.testFailed')}: {testResult.error}</>}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-100">
          <div>
            <label className="text-sm font-medium text-gray-700">{t('settings.pollingEnabled')}</label>
            <p className="text-xs text-gray-500">{t('settings.whenOnTheBackendLong')}</p>
          </div>
          <label className="inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              className="sr-only peer"
              checked={config.enabled}
              disabled={saving || !config.has_token}
              onChange={(e) => handleSave({ enabled: e.target.checked })}
            />
            <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 peer-disabled:opacity-50 relative transition-colors">
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${config.enabled ? 'translate-x-5' : ''}`} />
            </span>
          </label>
        </div>
      </SectionCard>

      <SectionCard title={t('settings.pollerStatus')}>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${status.running ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
            <span className="text-gray-700">{status.running ? t('settings.running') : t('settings.stopped')}</span>
          </div>
          <div className="text-gray-700">
            {t('settings.telegram.bot')}: <strong>{status.bot_username ? `@${status.bot_username}` : '—'}</strong>
          </div>
          <div className="text-gray-700">
            {t('settings.telegram.lastPoll')}: <span className="text-gray-500">{status.last_poll ? new Date(status.last_poll).toLocaleString() : '—'}</span>
          </div>
          <div className="text-gray-700">
            {t('settings.telegram.lastError')}: <span className="text-red-600">{status.last_error || '—'}</span>
          </div>
        </div>
      </SectionCard>

      <SectionCard title={`${t('settings.telegram.chatBindings')} (${bindings.length})`}>
        <p className="text-sm text-gray-600">
          {t('settings.telegram.bindingsBefore')} <code className="text-xs bg-gray-100 rounded px-1">/agent &lt;id&gt;</code> {t('settings.telegram.bindingsAfter')}
        </p>
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
                  <tr key={b.chat_id} className="border-t border-gray-100">
                    <td className="py-2 pr-3 font-mono text-xs text-gray-700">
                      {b.title || b.chat_id}
                      <div className="text-[10px] text-gray-400">{b.chat_id}</div>
                    </td>
                    <td className="py-2 pr-3 text-gray-800">
                      {b.flow_id ? (
                        <>
                          <span className="inline-flex items-center gap-1">
                            <span className="text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200 px-1.5 py-0.5 rounded">{t('settings.flow')}</span>
                            {b.flow_name || b.flow_id}
                          </span>
                          <div className="text-[10px] text-gray-400">{b.flow_id}</div>
                        </>
                      ) : b.agent_id ? (
                        <>
                          {b.agent_name || b.agent_id}
                          <div className="text-[10px] text-gray-400">{b.agent_id}</div>
                        </>
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
                        onClick={() => handleDeleteBinding(b.chat_id)}
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
      </SectionCard>
    </div>
  );
}


// ── Git connectors tab (GitHub / GitLab) ─────────────────────────────────────

function GitProviderSection({ provider, label, hint, config, onSaved }) {
  const { t } = useI18n();
  const [tokenInput, setTokenInput] = useState('');
  const [baseUrl, setBaseUrl] = useState(config.base_url || '');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');

  const isGitlab = provider === 'gitlab';

  const handleSave = async ({ clear_token } = {}) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const payload = { provider };
      if (clear_token) payload.clear_token = true;
      else if (tokenInput.trim()) payload.token = tokenInput.trim();
      if (isGitlab && baseUrl.trim()) payload.base_url = baseUrl.trim();
      const { data } = await updateGitConfig(payload);
      setTokenInput('');
      onSaved(data);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await testGitConnection(provider);
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <SectionCard title={label}>
      <p className="text-sm text-gray-600">{hint}</p>
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}

      {isGitlab && (
        <div>
          <label className="text-sm font-medium text-gray-700 mb-1 block">{t('settings.baseUrl')}</label>
          <p className="text-xs text-gray-500 mb-1">{t('settings.changeForSelfHostedGitlab')}</p>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://gitlab.com"
            className={inputCls}
          />
        </div>
      )}

      <div>
        <label className="text-sm font-medium text-gray-700 mb-1 block">
          {t('settings.git.personalAccessToken')} {config.has_token && <span className="text-gray-400 font-normal">({t('settings.currentlySet')})</span>}
        </label>
        <input
          type="password"
          value={tokenInput}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder={config.has_token ? t('settings.keepExistingToken') : t('settings.git.pasteToken', { provider: label })}
          className={inputCls}
          autoComplete="new-password"
        />
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <button
            type="button"
            onClick={() => handleSave({})}
            disabled={saving || (!tokenInput.trim() && !isGitlab)}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
          >
            {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {t('common.save')}
          </button>
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !config.has_token}
            className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50"
          >
            {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
            {t('settings.testConnection')}
          </button>
          {config.has_token && (
            <button
              type="button"
              onClick={() => handleSave({ clear_token: true })}
              disabled={saving}
              className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              <Trash2 className="w-3.5 h-3.5" /> {t('settings.clearToken')}
            </button>
          )}
        </div>
        {testResult && (
          <div className={`mt-2 text-sm rounded-lg px-3 py-2 ${testResult.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
            {testResult.ok
              ? <>{t('settings.connectedAs')} <strong>{testResult.login}</strong></>
              : <>{t('settings.testFailed')}: {testResult.error}</>}
          </div>
        )}
      </div>
    </SectionCard>
  );
}

function GitTab() {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState({ github: { has_token: false }, gitlab: { has_token: false, base_url: 'https://gitlab.com' } });
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await getGitConfig();
      setConfig(data);
    } catch (e) {
      setError(`${t('settings.errors.gitLoad')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}
      <GitProviderSection
        provider="github"
        label={t('settings.github')}
        hint={t('settings.usedToBrowseYourRepos')}
        config={config.github || {}}
        onSaved={setConfig}
      />
      <GitProviderSection
        provider="gitlab"
        label={t('settings.gitlab')}
        hint={t('settings.usedToBrowseYourProjects')}
        config={config.gitlab || {}}
        onSaved={setConfig}
      />
    </div>
  );
}


// ── Blender tab ──────────────────────────────────────────────────────────────
// Two things an operator needs from a geometry engine: whether one can run at
// all on this machine, and what is running right now. The daemon list is read
// from the cross-process registry, so it shows the engines agents started in
// their own processes, not only the ones the backend happens to have spawned.

function humanBytes(n) {
  if (!n) return '—';
  return `${Math.round(n / 1e6)} MB`;
}

function humanAge(seconds) {
  if (seconds == null) return '—';
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function BlenderTab() {
  const { t } = useI18n();
  const [config, setConfig] = useState(null);
  const [daemons, setDaemons] = useState({ daemons: [], running: 0, max_daemons: 0 });
  const [pathInput, setPathInput] = useState('');
  const [probe, setProbe] = useState(null);
  const [testing, setTesting] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const loadConfig = useCallback(async () => {
    try {
      const { data } = await getBlenderConfig();
      setConfig(data);
      setPathInput(data.binary_path || '');
    } catch (e) {
      setError(`${t('settings.blender.loadFailed')}: ` + (e.response?.data?.detail || e.message));
    }
  }, [t]);

  const loadDaemons = useCallback(async () => {
    try {
      const { data } = await getBlenderDaemons();
      setDaemons(data);
    } catch { /* the engines list is a live view; a failed poll is not an error state */ }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);
  useEffect(() => {
    loadDaemons();
    const timer = setInterval(loadDaemons, 5000);
    return () => clearInterval(timer);
  }, [loadDaemons]);

  const save = async (patch) => {
    setBusy('save');
    setError('');
    try {
      const { data } = await updateBlenderConfig(patch);
      setConfig(data);
      setPathInput(data.binary_path || '');
      setProbe(null);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy('');
    }
  };

  const test = async () => {
    setTesting(true);
    try {
      const { data } = await testBlenderBinary(pathInput.trim());
      setProbe(data);
    } catch (e) {
      setProbe({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  const stopOne = async (key) => {
    setBusy(key);
    try {
      await stopBlenderDaemon(key);
      await loadDaemons();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy('');
    }
  };

  const stopAll = async () => {
    setBusy('all');
    try {
      await stopAllBlenderDaemons();
      await loadDaemons();
    } finally {
      setBusy('');
    }
  };

  if (!config) {
    return (
      <div className="flex items-center justify-center py-10">
        <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
      </div>
    );
  }

  const availability = config.availability || {};
  const numberField = (key, label, hint) => (
    <div>
      <label className="text-sm font-medium text-gray-700">{label}</label>
      <input
        type="number"
        value={config[key] ?? ''}
        onChange={(e) => setConfig({ ...config, [key]: e.target.value })}
        onBlur={(e) => {
          const value = parseInt(e.target.value, 10);
          if (Number.isFinite(value) && value !== 0) save({ [key]: value });
        }}
        className={inputCls}
      />
      <p className="text-xs text-gray-500 mt-1">{hint}</p>
    </div>
  );

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      <SectionCard title={t('settings.blender.title')}>
        <p className="text-sm text-gray-600">{t('settings.blender.intro')}</p>

        <div className="flex items-center gap-3">
          {availability.available ? (
            <span className="flex items-center gap-1.5 text-xs text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
              {availability.version || t('settings.available')}
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
              {availability.reason || t('settings.blender.unavailable')}
            </span>
          )}
          <label className="flex items-center gap-2 text-sm text-gray-700 ml-auto">
            <input
              type="checkbox"
              checked={!!config.enabled}
              onChange={(e) => save({ enabled: e.target.checked })}
            />
            {t('settings.blender.enabled')}
          </label>
        </div>

        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.blender.binaryPath')}</label>
          <div className="flex gap-2 mt-1">
            <input
              type="text"
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              placeholder={config.discovered_binary || '/path/to/blender'}
              className={inputCls}
            />
            <button
              type="button"
              onClick={test}
              disabled={testing}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50 whitespace-nowrap"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
              {t('settings.blender.test')}
            </button>
            <button
              type="button"
              onClick={() => save({ binary_path: pathInput.trim() })}
              disabled={busy === 'save'}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              {t('common.save')}
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {config.discovered_binary
              ? t('settings.blender.discovered', { path: config.discovered_binary })
              : t('settings.blender.notDiscovered')}
          </p>
          {probe && (
            <p className={`text-xs mt-1 ${probe.ok ? 'text-green-700' : 'text-red-600'}`}>
              {probe.ok ? `${probe.version} · ${probe.path}` : probe.error}
            </p>
          )}
        </div>

        <div className="grid grid-cols-3 gap-4">
          {numberField('max_daemons', t('settings.blender.maxDaemons'), t('settings.blender.maxDaemonsHint'))}
          {numberField('idle_timeout_s', t('settings.blender.idleTimeout'), t('settings.blender.idleTimeoutHint'))}
          {numberField('command_timeout_s', t('settings.blender.commandTimeout'), t('settings.blender.commandTimeoutHint'))}
        </div>
      </SectionCard>

      <SectionCard title={t('settings.blender.engines')}>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-600">
            {t('settings.blender.engineCount', { running: daemons.running, max: daemons.max_daemons })}
          </span>
          {daemons.daemons?.length > 0 && (
            <button
              type="button"
              onClick={stopAll}
              disabled={busy === 'all'}
              className="ml-auto flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 text-gray-700 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              <Power className="w-3.5 h-3.5" />
              {t('settings.blender.stopAll')}
            </button>
          )}
        </div>

        {daemons.daemons?.length === 0 ? (
          <p className="text-sm text-gray-500">{t('settings.blender.noEngines')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-400">
                  <th className="py-2 pr-4">{t('settings.blender.colScene')}</th>
                  <th className="py-2 pr-4">{t('settings.blender.colObjects')}</th>
                  <th className="py-2 pr-4">PID</th>
                  <th className="py-2 pr-4">{t('settings.blender.colCommands')}</th>
                  <th className="py-2 pr-4">{t('settings.blender.colUptime')}</th>
                  <th className="py-2 pr-4">{t('settings.blender.colMemory')}</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {daemons.daemons.map((d) => (
                  <tr key={d.key} className="border-t border-gray-100">
                    <td className="py-2 pr-4 font-mono text-xs text-gray-700">{d.key}</td>
                    <td className="py-2 pr-4 text-gray-600">
                      {d.busy ? <span className="text-amber-600">{t('settings.blender.working')}</span>
                        : ((d.objects || []).join(', ') || '—')}
                    </td>
                    <td className="py-2 pr-4 text-gray-500">{d.pid}</td>
                    <td className="py-2 pr-4 text-gray-500">{d.commands ?? '—'}</td>
                    <td className="py-2 pr-4 text-gray-500">{humanAge(d.uptime_s)}</td>
                    <td className="py-2 pr-4 text-gray-500">{humanBytes(d.rss_bytes)}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => stopOne(d.key)}
                        disabled={busy === d.key}
                        className="text-xs text-red-600 hover:text-red-700 font-medium disabled:opacity-50"
                      >
                        {t('settings.blender.stop')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-gray-500">{t('settings.blender.enginesHint')}</p>
      </SectionCard>
    </div>
  );
}


function SectionCard({ title, actions, children }) {
  return (
    <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
      {/* The title row carries the card's own controls (a provider's status
          badge and its Test button). They used to be the first child of the
          body, which put them on a line of their own under the heading. */}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h2 className="text-base font-semibold text-gray-800">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function ProviderStatusBadge({ status, testing }) {
  const { t } = useI18n();
  if (testing) return <span className="flex items-center gap-1 text-xs text-gray-500"><RefreshCw className="w-3 h-3 animate-spin" /> {t('settings.testing')}</span>;
  if (!status) return null;
  if (status.ok) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        {t('settings.available')}
        {status.models?.length > 0 && <span className="opacity-70">· {t('settings.modelCount', { count: status.models.length })}</span>}
        {status.latency_ms && <span className="opacity-70">· {status.latency_ms}ms</span>}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full font-medium" title={status.error}>
      <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
      {t('settings.unavailable')} · <span className="opacity-70 max-w-40 truncate">{status.error}</span>
    </span>
  );
}

function ProviderHeader({ status, testing, onTest }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ProviderStatusBadge status={status} testing={testing} />
      <button type="button" onClick={onTest} disabled={testing}
        className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors">
        {testing ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />}
        {t('settings.test')}
      </button>
    </div>
  );
}

// ── Custom backends tab ───────────────────────────────────────────────────────

const BLANK_BACKEND = { id: '', label: '', adapter: 'openai', base_url: '', api_key: '', default_model: '', headers: '' };

function CustomBackendsTab() {
  const { t } = useI18n();
  const [backends, setBackends] = useState([]);
  const [adapters, setAdapters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(BLANK_BACKEND);
  const [editingId, setEditingId] = useState(null); // non-null when editing existing
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [tests, setTests] = useState({});   // id -> {ok, models, error, latency_ms}
  const [testing, setTesting] = useState({});

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/api/settings/custom-backends');
      setBackends(data.backends || []);
      setAdapters(data.adapters || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const setF = (k, v) => setForm(s => ({ ...s, [k]: v }));
  const startAdd = () => { setEditingId(null); setForm(BLANK_BACKEND); setError(''); };
  const startEdit = (b) => {
    setEditingId(b.id);
    setError('');
    setForm({
      id: b.id, label: b.label || '', adapter: b.adapter || 'openai',
      base_url: b.base_url || '', api_key: '', default_model: b.default_model || '',
      headers: b.headers && Object.keys(b.headers).length ? JSON.stringify(b.headers, null, 2) : '',
    });
  };

  const save = async () => {
    setError('');
    let headers;
    if (form.headers.trim()) {
      try { headers = JSON.parse(form.headers); }
      catch { setError(t('settings.headersMustBeJson')); return; }
    }
    setSaving(true);
    try {
      const payload = {
        id: form.id, label: form.label, adapter: form.adapter,
        base_url: form.base_url, default_model: form.default_model,
        headers: headers || {},
      };
      // Only send api_key when the user typed one (blank keeps the stored key).
      if (form.api_key) payload.api_key = form.api_key;
      await api.post('/api/settings/custom-backends', payload);
      setForm(BLANK_BACKEND);
      setEditingId(null);
      await load();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id) => {
    if (!window.confirm(t('settings.confirmDeleteBackend', { id }))) return;
    try { await api.delete(`/api/settings/custom-backends/${encodeURIComponent(id)}`); await load(); }
    catch (e) { setError(e.response?.data?.detail || e.message); }
  };

  const test = async (id) => {
    setTesting(s => ({ ...s, [id]: true }));
    setTests(s => ({ ...s, [id]: null }));
    try {
      const { data } = await api.post('/api/settings/test-provider', { provider: id });
      setTests(s => ({ ...s, [id]: data }));
    } catch (e) {
      setTests(s => ({ ...s, [id]: { ok: false, error: e.response?.data?.detail || e.message } }));
    } finally {
      setTesting(s => ({ ...s, [id]: false }));
    }
  };

  const adapterMeta = adapters.find(a => a.kind === form.adapter);

  if (loading) {
    return <div className="flex items-center justify-center py-10"><RefreshCw className="w-5 h-5 animate-spin text-indigo-500" /></div>;
  }

  return (
    <div className="space-y-5">
      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-500">
        {t('settings.customIntroBefore')} <span className="font-medium text-gray-700">{t('settings.models')}</span> {t('settings.customIntroAfter')}
      </div>

      {/* Existing backends */}
      {backends.length > 0 && (
        <div className="space-y-3">
          {backends.map(b => (
            <SectionCard key={b.id} title={`${b.label} (${b.id})`}>
              <div className="flex items-start justify-between gap-3">
                <div className="text-xs text-gray-600 space-y-0.5">
                  <div><span className="text-gray-400">{t('settings.adapter')}</span> {b.adapter}</div>
                  <div><span className="text-gray-400">{t('settings.baseUrl2')}</span> {b.base_url || <span className="text-red-500">{t('settings.notSet')}</span>}</div>
                  <div><span className="text-gray-400">{t('settings.apiKey')}</span> {b.api_key_set ? b.api_key : <span className="text-gray-400">{t('settings.none')}</span>}</div>
                  {b.default_model && <div><span className="text-gray-400">{t('settings.defaultModel')}</span> {b.default_model}</div>}
                </div>
                <div className="flex flex-col items-end gap-2 shrink-0">
                  <ProviderStatusBadge status={tests[b.id]} testing={testing[b.id]} />
                  <div className="flex gap-2">
                    <button type="button" onClick={() => test(b.id)} disabled={testing[b.id]}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50">
                      {testing[b.id] ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />} {t('settings.test')}
                    </button>
                    <button type="button" onClick={() => startEdit(b)}
                      className="px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-600 hover:bg-gray-50">{t('settings.edit')}</button>
                    <button type="button" onClick={() => remove(b.id)}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-red-200 text-xs font-medium text-red-600 hover:bg-red-50">
                      <Trash2 className="w-3 h-3" /> {t('settings.delete')}
                    </button>
                  </div>
                </div>
              </div>
            </SectionCard>
          ))}
        </div>
      )}

      {/* Add / edit form */}
      <SectionCard title={editingId ? t('settings.editBackend', { id: editingId }) : t('settings.addACustomBackend')}>
        {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="text-sm font-medium text-gray-700">{t('settings.id')}</label>
            <p className="text-xs text-gray-500 mb-1">{t('settings.lowercaseSlugUsedAsThe')}</p>
            <input type="text" value={form.id} disabled={!!editingId}
              onChange={e => setF('id', e.target.value)} placeholder="my-vllm"
              className={inputCls + (editingId ? ' bg-gray-100 text-gray-500' : '')} />
          </div>
          <div>
            <label className="text-sm font-medium text-gray-700">{t('settings.label')}</label>
            <p className="text-xs text-gray-500 mb-1">{t('settings.displayNameShownInPickers')}</p>
            <input type="text" value={form.label} onChange={e => setF('label', e.target.value)} placeholder="My vLLM" className={inputCls} />
          </div>
          <div>
            <label className="text-sm font-medium text-gray-700">{t('settings.adapter2')}</label>
            <p className="text-xs text-gray-500 mb-1">{adapterMeta?.openai_compatible === false ? t('settings.manualModels') : t('settings.openaiCompatible')}</p>
            <select value={form.adapter} onChange={e => setF('adapter', e.target.value)} className={inputCls}>
              {adapters.map(a => <option key={a.kind} value={a.kind}>{a.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-sm font-medium text-gray-700">{t('settings.defaultModel2')}</label>
            <p className="text-xs text-gray-500 mb-1">{t('settings.optionalDefaultModel')}</p>
            <input type="text" value={form.default_model} onChange={e => setF('default_model', e.target.value)} placeholder="my-model" className={inputCls} />
          </div>
          <div className="sm:col-span-2">
            <label className="text-sm font-medium text-gray-700">{t('settings.baseUrl')}</label>
            <p className="text-xs text-gray-500 mb-1">{t('settings.includeTheApiVersionPath')} <code className="bg-gray-100 rounded px-1">https://host:8000/v1</code>.</p>
            <input type="text" value={form.base_url} onChange={e => setF('base_url', e.target.value)} placeholder="https://gpu.box:8000/v1" className={inputCls} />
          </div>
          <div className="sm:col-span-2">
            <label className="text-sm font-medium text-gray-700">{t('settings.apiKeyLabel')} {editingId && <span className="text-gray-400 font-normal">({t('settings.leaveBlankKeepCurrent')})</span>}</label>
            <input type="password" value={form.api_key} onChange={e => setF('api_key', e.target.value)}
              placeholder={editingId ? t('settings.unchanged') : t('settings.leaveBlankKeyless')} autoComplete="new-password" className={inputCls} />
          </div>
          <div className="sm:col-span-2">
            <label className="text-sm font-medium text-gray-700">{t('settings.extraHeadersJson')}</label>
            <p className="text-xs text-gray-500 mb-1">{t('settings.optionalSentWithEveryRequest')} <code className="bg-gray-100 rounded px-1">{'{"X-Org": "acme"}'}</code>.</p>
            <textarea value={form.headers} onChange={e => setF('headers', e.target.value)} rows={2}
              placeholder='{"X-Org": "acme"}' className={inputCls + ' font-mono text-xs'} />
          </div>
        </div>
        <div className="flex items-center gap-2 pt-1">
          <button type="button" onClick={save} disabled={saving || !form.id || !form.base_url}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50">
            {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {editingId ? t('settings.saveChanges') : t('settings.addBackend')}
          </button>
          {editingId && (
            <button type="button" onClick={startAdd}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700">
              <X className="w-3.5 h-3.5" /> {t('settings.cancel')}
            </button>
          )}
        </div>
      </SectionCard>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function Settings() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const activeWorkspace = selectedWorkspace || 'default';

  // The open section lives in the URL (/settings/:section) so a section can be
  // linked to and survives a reload, the same contract the Docs page uses.
  const { section } = useParams();
  const navigate = useNavigate();
  const active = SECTIONS.find((s) => s.id === section) || SECTIONS[0];

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  // Global settings (editable)
  const [globalSettings, setGlobalSettings] = useState({});
  const [envDefinedFields, setEnvDefinedFields] = useState([]);
  const [masked, setMasked] = useState({
    openai_api_key_masked: '',
    anthropic_api_key_masked: '',
    google_api_key_masked: '',
    langfuse_secret_key_masked: '',
    langfuse_public_key_masked: '',
    rag_vector_db_api_key_masked: '',
    rag_embedding_api_key_masked: '',
  });

  const [wsOverrides, setWsOverrides] = useState({});

  const [fetchErrors, setFetchErrors] = useState({ ollama: '', lmstudio: '' });
  const [providerStatus, setProviderStatus] = useState({ openai: null, anthropic: null, google: null, ollama: null, lmstudio: null });
  const [providerTesting, setProviderTesting] = useState({ openai: false, anthropic: false, google: false, ollama: false, lmstudio: false });
  // Token streaming is a global (.env) switch, not a workspace override: it
  // changes how every agent process on this machine builds its LLM. Saved on
  // toggle rather than via the workspace Save button, which writes elsewhere.
  const [streamingSaving, setStreamingSaving] = useState(false);

  const toggleStreaming = async (next) => {
    setStreamingSaving(true);
    setError('');
    try {
      await updateSettings({ agent_streaming: next });
      setGlobalSettings(s => ({ ...s, agent_streaming: next }));
    } catch (e) {
      setError(`${t('settings.errors.streamingSave')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setStreamingSaving(false);
    }
  };

  const load = useCallback(async (ws = activeWorkspace) => {
    setLoading(true);
    setError('');
    try {
      const [globalResp, overridesResp] = await Promise.all([
        api.get('/api/settings'),
        getWorkspaceSettingsOverrides(ws),
      ]);

      const data = globalResp.data;
      setGlobalSettings(data);
      setEnvDefinedFields(data.env_defined_fields || []);
      setMasked({
        openai_api_key_masked: data.openai_api_key_masked,
        anthropic_api_key_masked: data.anthropic_api_key_masked,
        google_api_key_masked: data.google_api_key_masked,
        langfuse_secret_key_masked: data.langfuse_secret_key_masked,
        langfuse_public_key_masked: data.langfuse_public_key_masked,
        rag_vector_db_api_key_masked: data.rag_vector_db_api_key_masked,
        rag_embedding_api_key_masked: data.rag_embedding_api_key_masked,
      });

      setWsOverrides(overridesResp?.data?.overrides || {});
    } catch (e) {
      setError(`${t('settings.errors.load')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [t, activeWorkspace]);

  useEffect(() => { load(activeWorkspace); }, [load, activeWorkspace]);

  const hasOverrideField = (field) => Object.prototype.hasOwnProperty.call(wsOverrides || {}, field);
  const getFieldValue = (field, fallback = '') => (
    hasOverrideField(field) ? (wsOverrides[field] ?? '') : (globalSettings[field] ?? fallback)
  );
  const setG = (field, value) => setWsOverrides(prev => ({ ...prev, [field]: value }));
  const handleSaveWorkspace = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const g = { ...wsOverrides };
      const payload = {
        openai_base_url: g.openai_base_url || undefined,
        openai_api_key: g.openai_api_key || undefined,
        anthropic_api_key: g.anthropic_api_key || undefined,
        google_api_key: g.google_api_key || undefined,
        ollama_base_url: g.ollama_base_url || undefined,
        lmstudio_base_url: g.lmstudio_base_url || undefined,
        langfuse_secret_key: g.langfuse_secret_key || undefined,
        langfuse_public_key: g.langfuse_public_key || undefined,
        langfuse_base_url: g.langfuse_base_url || undefined,
        orch_log_level: g.orch_log_level || undefined,
        rag_vector_db: g.rag_vector_db || undefined,
        rag_vector_db_url: g.rag_vector_db_url || undefined,
        rag_vector_db_api_key: g.rag_vector_db_api_key || undefined,
        rag_vector_db_collection: g.rag_vector_db_collection || undefined,
        rag_embedding_provider: g.rag_embedding_provider || undefined,
        rag_embedding_model: g.rag_embedding_model || undefined,
        rag_embedding_api_key: g.rag_embedding_api_key || undefined,
        rag_embedding_base_url: g.rag_embedding_base_url || undefined,
        agent_mode: g.agent_mode || undefined,
        agent_docker_image: g.agent_docker_image || undefined,
        agent_docker_network: g.agent_docker_network || undefined,
        agent_docker_extra_args: g.agent_docker_extra_args || undefined,
        task_assignment_mode: g.task_assignment_mode || undefined,
      };
      // Remove undefined values
      Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k]);

      await updateWorkspaceSettingsOverrides(activeWorkspace, payload);
      await load(activeWorkspace);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  const handleTestProvider = async (provider) => {
    if (provider === 'ollama' || provider === 'lmstudio') return handleFetchModels(provider);
    setProviderTesting(s => ({ ...s, [provider]: true }));
    setProviderStatus(s => ({ ...s, [provider]: null }));
    try {
      const payload = { provider };
      if (provider === 'openai') {
        if (getFieldValue('openai_api_key')) payload.api_key = getFieldValue('openai_api_key');
        if (getFieldValue('openai_base_url')) payload.base_url = getFieldValue('openai_base_url');
      } else if (provider === 'anthropic') {
        if (getFieldValue('anthropic_api_key')) payload.api_key = getFieldValue('anthropic_api_key');
      } else if (provider === 'google') {
        if (getFieldValue('google_api_key')) payload.api_key = getFieldValue('google_api_key');
      }
      const { data } = await api.post('/api/settings/test-provider', payload);
      setProviderStatus(s => ({ ...s, [provider]: data }));
    } catch (e) {
      setProviderStatus(s => ({ ...s, [provider]: { ok: false, error: e.message } }));
    } finally {
      setProviderTesting(s => ({ ...s, [provider]: false }));
    }
  };

  const handleFetchModels = async (provider) => {
    const baseUrl = provider === 'ollama'
      ? (getFieldValue('ollama_base_url') || 'http://localhost:11434')
      : (getFieldValue('lmstudio_base_url') || 'http://localhost:1234');
    setProviderTesting(s => ({ ...s, [provider]: true }));
    setFetchErrors(s => ({ ...s, [provider]: '' }));
    setProviderStatus(s => ({ ...s, [provider]: null }));
    try {
      const { data } = await api.post('/api/settings/test-local-model', { provider, base_url: baseUrl });
      if (data.ok) {
        if (!data.models?.length) setFetchErrors(s => ({ ...s, [provider]: t('settings.connectedNoModels') }));
        setProviderStatus(s => ({ ...s, [provider]: data }));
      } else {
        setFetchErrors(s => ({ ...s, [provider]: data.error || t('settings.connectionFailed') }));
        setProviderStatus(s => ({ ...s, [provider]: data }));
      }
    } catch (e) {
      setFetchErrors(s => ({ ...s, [provider]: e.message }));
      setProviderStatus(s => ({ ...s, [provider]: { ok: false, error: e.message } }));
    } finally {
      setProviderTesting(s => ({ ...s, [provider]: false }));
    }
  };

  const g = {
    ...globalSettings,
    ...wsOverrides,
    openai_api_key: getFieldValue('openai_api_key'),
    anthropic_api_key: getFieldValue('anthropic_api_key'),
    google_api_key: getFieldValue('google_api_key'),
    langfuse_secret_key: getFieldValue('langfuse_secret_key'),
    langfuse_public_key: getFieldValue('langfuse_public_key'),
    rag_vector_db_api_key: getFieldValue('rag_vector_db_api_key'),
    rag_embedding_api_key: getFieldValue('rag_embedding_api_key'),
  };
  const badgeProps = { wsOverrides, envDefinedFields };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <PageContainer fill>
      <PageHeader
        icon={SettingsIcon}
        title={t('settings.settings')}
        description={<>
          {/* What used to be a standing blue banner above every workspace-scoped
              section: which workspace is being edited and where its values land.
              It says the same thing on all of them, so it belongs behind the
              heading's ⓘ rather than repeated down the page. */}
          <p>
            <span className="font-medium text-gray-700">{t('settings.workspaceLabel')} {activeWorkspace}</span>{' '}
            {t('settings.storedIn')} <code className="text-xs bg-gray-100 rounded px-1">{t('settings.settings2')}</code> {t('settings.insideThisWorkspaces')} <code className="text-xs bg-gray-100 rounded px-1">.workspace.json</code>{t('settings.clearingAFieldMakesIt')}
          </p>
          <p className="mt-2">
            {t('settings.fieldsMarkedBefore')} <span className="inline-flex items-center gap-0.5 text-amber-600"><Lock className="w-3 h-3" /> {t('settings.fromEnv')}</span> {t('settings.fieldsMarkedAfter')}
          </p>
        </>}
        actions={active.workspaceScoped && (
          <button onClick={handleSaveWorkspace} disabled={saving || loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
            {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {saving ? t('common.saving') : t('settings.saveWorkspaceSettings', { workspace: activeWorkspace })}
          </button>
        )}
      />

      <div className="flex min-h-0 flex-1 gap-6">
        {/* In-page section nav — same shape as the Docs sidebar */}
        <nav className="hidden w-56 shrink-0 overflow-y-auto md:block">
          <div className="pb-8">
            {GROUPS.map((group) => (
              <div key={group.key} className="mb-2">
                <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                  {t(`settings.nav.groups.${group.key}`)}
                </p>
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const isActive = item.id === active.id;
                  return (
                    <Link
                      key={item.id}
                      to={`/settings/${item.id}`}
                      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                        isActive
                          ? 'bg-indigo-50 font-semibold text-indigo-600'
                          : 'text-gray-600 hover:bg-gray-100'
                      }`}
                    >
                      <Icon className="w-4 h-4 shrink-0" />
                      {t(`settings.nav.${item.key}`)}
                    </Link>
                  );
                })}
              </div>
            ))}
          </div>
        </nav>

        {/* Content */}
        <div className="min-w-0 flex-1 overflow-y-auto">
          <div className="max-w-3xl space-y-5 pb-16">
            {/* Mobile section selector */}
            <div className="md:hidden">
              <select
                value={active.id}
                onChange={(e) => navigate(`/settings/${e.target.value}`)}
                className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm"
              >
                {GROUPS.map((group) => (
                  <optgroup key={group.key} label={t(`settings.nav.groups.${group.key}`)}>
                    {group.items.map((item) => (
                      <option key={item.id} value={item.id}>{t(`settings.nav.${item.key}`)}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            {saved && (
              <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
                {t('settings.workspaceSettingsSaved', { workspace: activeWorkspace })}
              </div>
            )}
            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
            )}

            {/* Section: cloud providers */}
            {active.id === 'providers' && (
              <div className="space-y-5">
                <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-500">
                  {t('settings.providersIntroBefore')} <span className="font-medium text-gray-700">{t('settings.models')}</span> {t('settings.providersIntroAfter')}
                </div>
                <SectionCard
                  title={t('settings.openai')}
                  actions={<ProviderHeader status={providerStatus.openai} testing={providerTesting.openai} onTest={() => handleTestProvider('openai')} />}
                >
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">
                        {t('settings.apiKeyLabel')} {masked.openai_api_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.openai_api_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="openai_api_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.openai_api_key || ''} onChange={e => setG('openai_api_key', e.target.value)}
                      placeholder={t('settings.leaveEmptyToInheritThe')}
                      className={inputCls} autoComplete="new-password" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.baseUrlOverride')}</label>
                      <SourceBadge fieldName="openai_base_url" {...badgeProps} />
                    </div>
                    <p className="text-xs text-gray-500 mb-1">{t('settings.useThisToPointTo')}</p>
                    <input type="text" value={g.openai_base_url || ''} onChange={e => setG('openai_base_url', e.target.value)}
                      placeholder={t('settings.httpsApiOpenaiComV1')}
                      className={inputCls} />
                  </div>
                </SectionCard>

                <SectionCard
                  title={t('settings.anthropicClaude')}
                  actions={<ProviderHeader status={providerStatus.anthropic} testing={providerTesting.anthropic} onTest={() => handleTestProvider('anthropic')} />}
                >
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">
                        {t('settings.apiKeyLabel')} {masked.anthropic_api_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.anthropic_api_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="anthropic_api_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.anthropic_api_key || ''} onChange={e => setG('anthropic_api_key', e.target.value)}
                      placeholder={t('settings.leaveEmptyToInheritThe')}
                      className={inputCls} autoComplete="new-password" />
                  </div>
                </SectionCard>

                <SectionCard
                  title={t('settings.googleGemini')}
                  actions={<ProviderHeader status={providerStatus.google} testing={providerTesting.google} onTest={() => handleTestProvider('google')} />}
                >
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">
                        {t('settings.apiKeyLabel')} {masked.google_api_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.google_api_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="google_api_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.google_api_key || ''} onChange={e => setG('google_api_key', e.target.value)}
                      placeholder={t('settings.leaveEmptyToInheritThe')}
                      className={inputCls} autoComplete="new-password" />
                  </div>
                </SectionCard>
              </div>
            )}

            {/* Section: local model servers */}
            {active.id === 'local' && (
              <div className="space-y-5">
                <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-500">
                  {t('settings.localIntroBefore')} <span className="font-medium text-gray-700">{t('settings.models')}</span> {t('settings.localIntroAfter')}
                </div>
                <SectionCard
                  title={t('settings.ollama')}
                  actions={<ProviderHeader status={providerStatus.ollama} testing={providerTesting.ollama} onTest={() => handleTestProvider('ollama')} />}
                >
                  <p className="text-sm text-gray-600">
                    {t('settings.ollamaIntroBefore')} <span className="font-medium text-gray-800">{t('settings.ollama')}</span>{t('settings.ollamaIntroAfter')}
                  </p>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.baseUrl')}</label>
                      <SourceBadge fieldName="ollama_base_url" {...badgeProps} />
                    </div>
                    <p className="text-xs text-gray-500 mb-1">{t('settings.ollamaServerAddress')}</p>
                    <input type="text" value={g.ollama_base_url || ''} onChange={e => setG('ollama_base_url', e.target.value)}
                      placeholder="http://localhost:11434" className={inputCls} />
                    {fetchErrors.ollama && <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{fetchErrors.ollama}</p>}
                  </div>
                </SectionCard>

                <SectionCard
                  title={t('settings.lmStudio')}
                  actions={<ProviderHeader status={providerStatus.lmstudio} testing={providerTesting.lmstudio} onTest={() => handleTestProvider('lmstudio')} />}
                >
                  <p className="text-sm text-gray-600">
                    <span className="font-medium text-gray-800">{t('settings.lmStudio')}</span> {t('settings.lmStudioIntro')}
                  </p>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.baseUrl')}</label>
                      <SourceBadge fieldName="lmstudio_base_url" {...badgeProps} />
                    </div>
                    <p className="text-xs text-gray-500 mb-1">{t('settings.lmstudioServerAddress')}</p>
                    <input type="text" value={g.lmstudio_base_url || ''} onChange={e => setG('lmstudio_base_url', e.target.value)}
                      placeholder="http://localhost:1234" className={inputCls} />
                    {fetchErrors.lmstudio && <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{fetchErrors.lmstudio}</p>}
                  </div>
                </SectionCard>
              </div>
            )}

            {/* Section: observability */}
            {active.id === 'observability' && (
              <div className="space-y-5">
                <SectionCard title={t('settings.langfuse')}>
                  <p className="text-sm text-gray-600">
                    {t('settings.langfuseIntroBefore')} <span className="font-medium text-gray-800">{t('settings.langfuse')}</span> {t('settings.langfuseIntroAfter')}
                  </p>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">
                        {t('settings.secretKey')} {masked.langfuse_secret_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.langfuse_secret_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="langfuse_secret_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.langfuse_secret_key || ''} onChange={e => setG('langfuse_secret_key', e.target.value)}
                      placeholder={t('settings.leaveEmptyToInheritThe')}
                      className={inputCls} autoComplete="new-password" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">
                        {t('settings.publicKey')} {masked.langfuse_public_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.langfuse_public_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="langfuse_public_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.langfuse_public_key || ''} onChange={e => setG('langfuse_public_key', e.target.value)}
                      placeholder={t('settings.leaveEmptyToInheritThe')}
                      className={inputCls} autoComplete="new-password" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.baseUrl')}</label>
                      <SourceBadge fieldName="langfuse_base_url" {...badgeProps} />
                    </div>
                    <input type="text" value={g.langfuse_base_url || ''} onChange={e => setG('langfuse_base_url', e.target.value)}
                      placeholder="https://cloud.langfuse.com"
                      className={inputCls} />
                  </div>
                </SectionCard>
              </div>
            )}

            {/* Section: RAG & vectors */}
            {active.id === 'rag' && (
              <div className="space-y-5">
                {(g.rag_vector_db !== 'none' || g.rag_embedding_provider !== 'none') && (
                  <div className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${
                    g.rag_vector_db !== 'none' && g.rag_embedding_provider !== 'none'
                      ? 'bg-green-50 border-green-200 text-green-800'
                      : 'bg-yellow-50 border-yellow-200 text-yellow-800'
                  }`}>
                    {g.rag_vector_db !== 'none' && g.rag_embedding_provider !== 'none'
                      ? <CheckCircle className="w-4 h-4 mt-0.5 shrink-0" />
                      : <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />}
                    <div>
                      {g.rag_vector_db !== 'none' && g.rag_embedding_provider !== 'none'
                        ? <><strong>{t('settings.ragPipelineActive')}</strong> {t('settings.embeddedWith')} <strong>{g.rag_embedding_provider}</strong>{t('settings.storedIn2')} <strong>{g.rag_vector_db}</strong>.</>
                        : <>{t('settings.ragPartiallyConfigured')}</>}
                    </div>
                  </div>
                )}

                <SectionCard title={t('settings.vectorDatabase')}>
                  <p className="text-sm text-gray-600">{t('settings.chooseWhereProcessedDocumentChunks')}</p>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.provider')}</label>
                      <SourceBadge fieldName="rag_vector_db" {...badgeProps} />
                    </div>
                    <select value={g.rag_vector_db || 'none'} onChange={e => setG('rag_vector_db', e.target.value)}
                      className={inputCls}>
                      {VECTOR_DBS.map(d => <option key={d.value} value={d.value}>{optionLabel(d, t)}</option>)}
                    </select>
                    {g.rag_vector_db !== 'none' && (
                      <p className="text-xs text-gray-400 mt-1">{t('settings.install')} <code className="bg-gray-100 rounded px-1 py-0.5">{installHint(g.rag_vector_db, t)}</code></p>
                    )}
                  </div>

                  {g.rag_vector_db !== 'none' && (
                    <>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <label className="text-sm font-medium text-gray-700">{t('settings.serverUrl')}</label>
                          <SourceBadge fieldName="rag_vector_db_url" {...badgeProps} />
                        </div>
                        <input type="text" value={g.rag_vector_db_url || ''} onChange={e => setG('rag_vector_db_url', e.target.value)}
                          placeholder={g.rag_vector_db === 'qdrant' ? 'http://localhost:6333' : g.rag_vector_db === 'pinecone' ? 'https://your-index-host.pinecone.io' : 'http://localhost:8000'}
                          className={inputCls} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <label className="text-sm font-medium text-gray-700">{t('settings.collectionIndexName')}</label>
                          <SourceBadge fieldName="rag_vector_db_collection" {...badgeProps} />
                        </div>
                        <input type="text" value={g.rag_vector_db_collection || ''} onChange={e => setG('rag_vector_db_collection', e.target.value)}
                          placeholder="agents_hub_rag"
                          className={inputCls} />
                      </div>
                      {(g.rag_vector_db === 'pinecone' || g.rag_vector_db === 'qdrant') && (
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <label className="text-sm font-medium text-gray-700">
                              {t('settings.apiKeyLabel')} {masked.rag_vector_db_api_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.rag_vector_db_api_key_masked})</span>}
                            </label>
                            <SourceBadge fieldName="rag_vector_db_api_key" {...badgeProps} />
                          </div>
                          <input type="password" value={g.rag_vector_db_api_key || ''} onChange={e => setG('rag_vector_db_api_key', e.target.value)}
                            placeholder={t('settings.leaveEmptyToInheritThe')}
                            className={inputCls} autoComplete="new-password" />
                        </div>
                      )}
                    </>
                  )}
                </SectionCard>

                <SectionCard title={t('settings.embeddingModel')}>
                  <p className="text-sm text-gray-600">{t('settings.chooseTheModelUsedTo')}</p>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.provider')}</label>
                      <SourceBadge fieldName="rag_embedding_provider" {...badgeProps} />
                    </div>
                    <select value={g.rag_embedding_provider || 'none'} onChange={e => setG('rag_embedding_provider', e.target.value)}
                      className={inputCls}>
                      {EMBEDDING_PROVIDERS.map(p => <option key={p.value} value={p.value}>{optionLabel(p, t)}</option>)}
                    </select>
                    {g.rag_embedding_provider !== 'none' && (
                      <p className="text-xs text-gray-400 mt-1">{t('settings.install')} <code className="bg-gray-100 rounded px-1 py-0.5">{installHint(g.rag_embedding_provider, t)}</code></p>
                    )}
                  </div>

                  {g.rag_embedding_provider !== 'none' && (
                    <>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <label className="text-sm font-medium text-gray-700">{t('settings.modelName')}</label>
                          <SourceBadge fieldName="rag_embedding_model" {...badgeProps} />
                        </div>
                        <input type="text" value={g.rag_embedding_model || ''} onChange={e => setG('rag_embedding_model', e.target.value)}
                          placeholder={
                            g.rag_embedding_provider === 'openai' ? 'text-embedding-3-small'
                            : g.rag_embedding_provider === 'google' ? 'text-embedding-004'
                            : g.rag_embedding_provider === 'ollama' ? 'nomic-embed-text'
                            : 'all-MiniLM-L6-v2'
                          }
                          className={inputCls} />
                      </div>
                      {(g.rag_embedding_provider === 'openai' || g.rag_embedding_provider === 'google') && (
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <label className="text-sm font-medium text-gray-700">
                              {t('settings.apiKeyLabel')} {masked.rag_embedding_api_key_masked && <span className="text-gray-400 font-normal">({t('settings.current')}: {masked.rag_embedding_api_key_masked})</span>}
                            </label>
                            <SourceBadge fieldName="rag_embedding_api_key" {...badgeProps} />
                          </div>
                          <input type="password" value={g.rag_embedding_api_key || ''} onChange={e => setG('rag_embedding_api_key', e.target.value)}
                            placeholder={t('settings.leaveEmptyToInheritThe')}
                            className={inputCls} autoComplete="new-password" />
                        </div>
                      )}
                      {g.rag_embedding_provider === 'ollama' && (
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <label className="text-sm font-medium text-gray-700">{t('settings.ollamaBaseUrl')}</label>
                            <SourceBadge fieldName="rag_embedding_base_url" {...badgeProps} />
                          </div>
                          <input type="text" value={g.rag_embedding_base_url || ''} onChange={e => setG('rag_embedding_base_url', e.target.value)}
                            placeholder="http://localhost:11434"
                            className={inputCls} />
                        </div>
                      )}
                    </>
                  )}
                </SectionCard>
              </div>
            )}

            {/* Section: custom backends */}
            {active.id === 'custom' && <CustomBackendsTab />}

            {/* Section: Telegram connector */}
            {active.id === 'telegram' && <TelegramTab />}

            {/* Section: Git connectors */}
            {active.id === 'git' && <GitTab />}

            {/* Section: Blender geometry engine */}
            {active.id === 'blender' && <BlenderTab />}

            {/* Section: logging */}
            {active.id === 'logging' && (
              <div className="space-y-5">
                <SectionCard title={t('settings.logging.title')}>
                  <p className="text-sm text-gray-600">{t('settings.logging.intro')}</p>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.logLevel')}</label>
                      <SourceBadge fieldName="orch_log_level" {...badgeProps} />
                    </div>
                    <select value={g.orch_log_level || 'INFO'} onChange={e => setG('orch_log_level', e.target.value)}
                      className={inputCls}>
                      {LOG_LEVELS.map(l => <option key={l} value={l}>{l}</option>)}
                    </select>
                    <p className="text-xs text-gray-500 mt-1">{t('settings.logging.hint')}</p>
                  </div>
                </SectionCard>
              </div>
            )}

            {/* Section: agent execution */}
            {active.id === 'execution' && (
              <div className="space-y-5">
                <SectionCard title={t('settings.liveStreaming')}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <label className="text-sm font-medium text-gray-700">{t('settings.streamAgentOutput')}</label>
                      <p className="text-xs text-gray-500 mt-1">
                        {t('settings.streamingHintBefore')} <strong>{t('settings.stop')}</strong>{t('settings.streamingHintAfter')}
                      </p>
                      <p className="text-xs text-gray-400 mt-1">
                        {t('settings.globalSettingWrittenTo')} <code className="bg-gray-100 rounded px-1">.env</code> {t('settings.as')}
                        <code className="bg-gray-100 rounded px-1 ml-1">AGENT_STREAMING</code>{t('settings.appliesToEvery')}{' '}
                        {t('settings.streamingAgentOverrideBefore')} <code className="bg-gray-100 rounded px-1">{t('settings.streaming')}</code> {t('settings.streamingAgentOverrideAfter')}
                      </p>
                    </div>
                    <label className="inline-flex items-center cursor-pointer shrink-0">
                      <input
                        type="checkbox"
                        className="sr-only peer"
                        checked={!!globalSettings.agent_streaming}
                        disabled={streamingSaving}
                        onChange={(e) => toggleStreaming(e.target.checked)}
                      />
                      <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 peer-disabled:opacity-50 relative transition-colors">
                        <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${globalSettings.agent_streaming ? 'translate-x-5' : ''}`} />
                      </span>
                    </label>
                  </div>
                </SectionCard>

                <SectionCard title={t('settings.agentExecutionMode')}>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.agentMode')}</label>
                      <SourceBadge fieldName="agent_mode" {...badgeProps} />
                      {activeWorkspace && (
                        <span className="text-xs text-indigo-600 font-medium bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
                          {t('settings.workspaceBadge', { workspace: activeWorkspace })}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 mb-2">
                      <strong>{t('settings.local')}</strong> {t('settings.agentsRunAsSubprocesses')} <strong>{t('settings.docker')}</strong> {t('settings.agentsRunInDocker')}
                    </p>
                    <div className="flex gap-4">
                      {['local', 'docker'].map(mode => (
                        <label key={mode} className="flex items-center gap-2 cursor-pointer">
                          <input type="radio" name="agent_mode" value={mode}
                            checked={(g.agent_mode || 'local') === mode}
                            onChange={() => setG('agent_mode', mode)}
                            className="accent-indigo-600" />
                          <span className="text-sm font-medium text-gray-700 capitalize">{mode}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  {g.agent_mode === 'docker' && (
                    <div className="space-y-4 mt-2 pt-4 border-t border-gray-100">
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <label className="text-sm font-medium text-gray-700">{t('settings.dockerImage')}</label>
                          <SourceBadge fieldName="agent_docker_image" {...badgeProps} />
                        </div>
                        <input type="text" value={g.agent_docker_image || ''} onChange={e => setG('agent_docker_image', e.target.value)}
                          placeholder="agents-hub:latest"
                          className={inputCls} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <label className="text-sm font-medium text-gray-700">{t('settings.dockerNetwork')}</label>
                          <SourceBadge fieldName="agent_docker_network" {...badgeProps} />
                        </div>
                        <input type="text" value={g.agent_docker_network || ''} onChange={e => setG('agent_docker_network', e.target.value)}
                          placeholder="agents_hub_default"
                          className={inputCls} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <label className="text-sm font-medium text-gray-700">{t('settings.extraDockerArgs')}</label>
                          <SourceBadge fieldName="agent_docker_extra_args" {...badgeProps} />
                        </div>
                        <input type="text" value={g.agent_docker_extra_args || ''} onChange={e => setG('agent_docker_extra_args', e.target.value)}
                          placeholder="--add-host host.docker.internal:host-gateway"
                          className={inputCls} />
                      </div>
                    </div>
                  )}
                </SectionCard>

                <SectionCard title={t('settings.taskAssignment')}>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">{t('settings.assignmentMode')}</label>
                      <SourceBadge fieldName="task_assignment_mode" {...badgeProps} />
                    </div>
                    <p className="text-xs text-gray-500 mb-2">
                      <strong>{t('settings.anyAgent')}</strong> {t('settings.tasksAssignedToAnyRegistered')}<br />
                      <strong>{t('settings.runningNodesOnly')}</strong> {t('settings.nodesOnlyHint')}
                    </p>
                    <div className="flex gap-4">
                      {[
                        { value: 'any', label: t('settings.anyAgent') },
                        { value: 'nodes_only', label: t('settings.runningNodesOnly') },
                      ].map(opt => (
                        <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
                          <input type="radio" name="task_assignment_mode" value={opt.value}
                            checked={(g.task_assignment_mode || 'any') === opt.value}
                            onChange={() => setG('task_assignment_mode', opt.value)}
                            className="accent-indigo-600" />
                          <span className="text-sm font-medium text-gray-700">{opt.label}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </SectionCard>
              </div>
            )}
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
