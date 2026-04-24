import { useState, useEffect } from 'react';
import axios from 'axios';
import {
  RefreshCw, Key, Cpu, Activity, Wrench, Database,
  CheckCircle, AlertCircle, Wifi, Loader, Lock, Save,
} from 'lucide-react';
import { useWorkspace } from '../components/WorkspaceContext';
import { getWorkspaceSettingsOverrides, updateWorkspaceSettingsOverrides } from '../api';

const api = axios.create({ baseURL: 'http://localhost:8000' });


const TABS = [
  { id: 'apikeys',       label: 'API Keys & Models', icon: Key },
  { id: 'local',        label: 'Local Models',       icon: Cpu },
  { id: 'observability',label: 'Observability',      icon: Activity },
  { id: 'rag',          label: 'RAG & Vectors',      icon: Database },
  { id: 'system',       label: 'System',             icon: Wrench },
];

const VECTOR_DBS = [
  { value: 'none',     label: 'None (text chunking only)' },
  { value: 'chroma',   label: 'ChromaDB' },
  { value: 'pinecone', label: 'Pinecone' },
  { value: 'qdrant',   label: 'Qdrant' },
];

const EMBEDDING_PROVIDERS = [
  { value: 'none',                  label: 'None (no embeddings)' },
  { value: 'openai',                label: 'OpenAI' },
  { value: 'sentence-transformers', label: 'Sentence-Transformers (local)' },
  { value: 'ollama',                label: 'Ollama (local)' },
  { value: 'google',                label: 'Google (Gemini)' },
];

const INSTALL_HINTS = {
  chroma: 'pip install chromadb',
  pinecone: 'pip install pinecone-client',
  qdrant: 'pip install qdrant-client',
  openai: 'pip install openai',
  'sentence-transformers': 'pip install sentence-transformers',
  ollama: 'No extra package — uses Ollama HTTP API',
  google: 'pip install google-generativeai',
};

const inputCls = "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none";
const PROVIDER_MODEL_FIELDS = {
  openai: 'model',
  anthropic: 'anthropic_model',
  google: 'google_model',
  ollama: 'ollama_model',
  lmstudio: 'lmstudio_model',
};

// ── Source badge — shows where a setting value comes from ─────────────────────

function SourceBadge({ fieldName, wsOverrides, envDefinedFields = [] }) {
  const hasWsOverride = Boolean(
    wsOverrides
    && Object.prototype.hasOwnProperty.call(wsOverrides, fieldName)
    && String(wsOverrides[fieldName] ?? '').trim() !== ''
  );
  if (hasWsOverride) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full px-1.5 py-0.5">
        <CheckCircle className="w-2.5 h-2.5" /> Workspace
      </span>
    );
  }
  if (envDefinedFields.includes(fieldName)) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-1.5 py-0.5">
        <Lock className="w-2.5 h-2.5" /> from .env
      </span>
    );
  }
  return null;
}

function SectionCard({ title, children, futureDev = false }) {
  return (
    <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold text-gray-800">{title}</h2>
        {futureDev && (
          <span className="text-xs font-medium bg-amber-100 text-amber-700 border border-amber-200 px-2 py-0.5 rounded-full">
            Future Dev
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

function DefaultProviderRow({ provider, currentDefault }) {
  const isDefault = currentDefault === provider;
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-gray-500">Default provider:</span>
      {isDefault
        ? <span className="text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full font-medium">Active: {provider}</span>
        : <span className="text-xs text-gray-400">{provider}</span>}
    </div>
  );
}

function ProviderStatusBadge({ status, testing }) {
  if (testing) return <span className="flex items-center gap-1 text-xs text-gray-500"><RefreshCw className="w-3 h-3 animate-spin" /> Testing…</span>;
  if (!status) return null;
  if (status.ok) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        Available
        {status.models?.length > 0 && <span className="opacity-70">· {status.models.length} models</span>}
        {status.latency_ms && <span className="opacity-70">· {status.latency_ms}ms</span>}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full font-medium" title={status.error}>
      <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
      Unavailable · <span className="opacity-70 max-w-40 truncate">{status.error}</span>
    </span>
  );
}

function ProviderHeader({ provider, currentDefault, status, testing, onTest, onMakeDefault, canMakeDefault }) {
  const isDefault = currentDefault === provider;
  return (
    <div className="flex items-center justify-between flex-wrap gap-2">
      <DefaultProviderRow provider={provider} currentDefault={currentDefault} />
      <div className="flex items-center gap-2">
        {onMakeDefault && (
          <button
            type="button"
            onClick={onMakeDefault}
            disabled={!canMakeDefault || isDefault}
            className="px-2.5 py-1 rounded-lg border border-indigo-300 text-xs font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 disabled:hover:bg-white"
          >
            {isDefault ? 'Default' : 'Make Default'}
          </button>
        )}
        <ProviderStatusBadge status={status} testing={testing} />
        <button type="button" onClick={onTest} disabled={testing}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors">
          {testing ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />}
          Test
        </button>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function Settings() {
  const { selectedWorkspace } = useWorkspace();
  const activeWorkspace = selectedWorkspace || 'default';

  const [activeTab, setActiveTab] = useState('apikeys');
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

  const [fetchedModels, setFetchedModels] = useState({ ollama: [], lmstudio: [] });
  const [fetchingModels, setFetchingModels] = useState({ ollama: false, lmstudio: false });
  const [fetchErrors, setFetchErrors] = useState({ ollama: '', lmstudio: '' });
  const [providerStatus, setProviderStatus] = useState({ openai: null, anthropic: null, google: null, ollama: null, lmstudio: null });
  const [providerTesting, setProviderTesting] = useState({ openai: false, anthropic: false, google: false, ollama: false, lmstudio: false });

  const load = async (ws = activeWorkspace) => {
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
      setError('Failed to load settings: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(activeWorkspace); }, [activeWorkspace]);

  const hasOverrideField = (field) => Object.prototype.hasOwnProperty.call(wsOverrides || {}, field);
  const getFieldValue = (field, fallback = '') => (
    hasOverrideField(field) ? (wsOverrides[field] ?? '') : (globalSettings[field] ?? fallback)
  );
  const setG = (field, value) => setWsOverrides(prev => ({ ...prev, [field]: value }));
  const currentDefaultModel = (() => {
    const wsDefaultModel = wsOverrides.default_model;
    if (wsDefaultModel && typeof wsDefaultModel === 'object') {
      const provider = String(wsDefaultModel.provider || '').trim();
      const model = String(wsDefaultModel.model || '').trim();
      if (provider) return { provider, model };
    }
    const provider = getFieldValue('default_provider', 'openai');
    const modelField = PROVIDER_MODEL_FIELDS[provider] || 'model';
    return { provider, model: getFieldValue(modelField, '') };
  })();

  const handleMakeDefault = (provider) => {
    const modelField = PROVIDER_MODEL_FIELDS[provider] || 'model';
    const model = String(getFieldValue(modelField, '') || '').trim();
    if (!model) return;
    setWsOverrides(prev => ({
      ...prev,
      default_provider: provider,
      default_model: { provider, model },
    }));
  };

  const handleSaveWorkspace = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const g = { ...wsOverrides };
      const defaultProvider = String(g.default_provider || '').trim();
      const explicitDefaultModel = g.default_model && typeof g.default_model === 'object'
        ? {
            provider: String(g.default_model.provider || '').trim(),
            model: String(g.default_model.model || '').trim(),
          }
        : null;
      const syncedDefaultModel = (() => {
        const provider = explicitDefaultModel?.provider || defaultProvider;
        if (!provider) return undefined;
        const modelField = PROVIDER_MODEL_FIELDS[provider] || 'model';
        const model = String((g[modelField] ?? getFieldValue(modelField, '')) || '').trim();
        if (!model) return explicitDefaultModel?.provider ? explicitDefaultModel : undefined;
        return { provider, model };
      })();
      const payload = {
        default_provider: g.default_provider || undefined,
        default_model: syncedDefaultModel,
        model: g.model || undefined,
        anthropic_model: g.anthropic_model || undefined,
        google_model: g.google_model || undefined,
        openai_base_url: g.openai_base_url || undefined,
        openai_api_key: g.openai_api_key || undefined,
        anthropic_api_key: g.anthropic_api_key || undefined,
        google_api_key: g.google_api_key || undefined,
        temperature: g.temperature !== undefined && g.temperature !== '' ? Number(g.temperature) : undefined,
        max_tokens: g.max_tokens !== undefined && g.max_tokens !== '' ? Number(g.max_tokens) : undefined,
        ollama_base_url: g.ollama_base_url || undefined,
        ollama_model: g.ollama_model || undefined,
        lmstudio_base_url: g.lmstudio_base_url || undefined,
        lmstudio_model: g.lmstudio_model || undefined,
        langfuse_secret_key: g.langfuse_secret_key || undefined,
        langfuse_public_key: g.langfuse_public_key || undefined,
        langfuse_base_url: g.langfuse_base_url || undefined,
        orch_poll_interval: g.orch_poll_interval !== undefined && g.orch_poll_interval !== '' ? Number(g.orch_poll_interval) : undefined,
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
      setError('Failed to save: ' + (e.response?.data?.detail || e.message));
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
    setFetchingModels(s => ({ ...s, [provider]: true }));
    setFetchErrors(s => ({ ...s, [provider]: '' }));
    setFetchedModels(s => ({ ...s, [provider]: [] }));
    try {
      const { data } = await api.post('/api/settings/test-local-model', { provider, base_url: baseUrl });
      if (data.ok) {
        setFetchedModels(s => ({ ...s, [provider]: data.models || [] }));
        if (!data.models?.length) setFetchErrors(s => ({ ...s, [provider]: 'Connected but no models found.' }));
        setProviderStatus(s => ({ ...s, [provider]: data }));
      } else {
        setFetchErrors(s => ({ ...s, [provider]: data.error || 'Connection failed.' }));
        setProviderStatus(s => ({ ...s, [provider]: data }));
      }
    } catch (e) {
      setFetchErrors(s => ({ ...s, [provider]: e.message }));
      setProviderStatus(s => ({ ...s, [provider]: { ok: false, error: e.message } }));
    } finally {
      setFetchingModels(s => ({ ...s, [provider]: false }));
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
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Changes are saved to <code className="text-xs bg-gray-100 rounded px-1">.workspace.json</code> for this workspace.
            Fields marked <span className="inline-flex items-center gap-0.5 text-amber-600"><Lock className="w-3 h-3" /> from .env</span> are inherited until you override them here.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={handleSaveWorkspace} disabled={saving || loading}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
            {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {saving ? 'Saving…' : `Save "${activeWorkspace}" settings`}
          </button>
        </div>
      </div>

      {saved && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          Workspace settings saved to .workspace.json for "{activeWorkspace}".
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
      )}

      <div className="bg-indigo-50 border border-indigo-200 rounded-lg px-4 py-3 text-sm text-indigo-700 flex items-start gap-2">
        <span className="font-medium shrink-0">Workspace: {activeWorkspace}</span>
        <span className="text-indigo-500">Stored in <code className="text-xs bg-indigo-100 rounded px-1">settings</code> inside this workspace&apos;s <code className="text-xs bg-indigo-100 rounded px-1">.workspace.json</code>. Clearing a field makes it inherit the global value again.</span>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button key={tab.id} onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === tab.id ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}>
              <Icon className="w-4 h-4" />
              <span className="hidden sm:inline">{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Tab: API Keys & Models */}
      {activeTab === 'apikeys' && (
        <div className="space-y-5">
          <SectionCard title="OpenAI">
            <ProviderHeader provider="openai" currentDefault={currentDefaultModel.provider} status={providerStatus.openai} testing={providerTesting.openai} onTest={() => handleTestProvider('openai')} onMakeDefault={() => handleMakeDefault('openai')} canMakeDefault={Boolean((g.model || '').trim())} />
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">
                  API Key {masked.openai_api_key_masked && <span className="text-gray-400 font-normal">(current: {masked.openai_api_key_masked})</span>}
                </label>
                <SourceBadge fieldName="openai_api_key" {...badgeProps} />
              </div>
              <input type="password" value={g.openai_api_key || ''} onChange={e => setG('openai_api_key', e.target.value)}
                placeholder="Leave empty to inherit the global key"
                className={inputCls} autoComplete="new-password" />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Base URL override</label>
                <SourceBadge fieldName="openai_base_url" {...badgeProps} />
              </div>
              <p className="text-xs text-gray-500 mb-1">Use this to point to an OpenAI-compatible proxy or Azure endpoint.</p>
              <input type="text" value={g.openai_base_url || ''} onChange={e => setG('openai_base_url', e.target.value)}
                placeholder="https://api.openai.com/v1  (leave blank for default)"
                className={inputCls} />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Default Model</label>
                <SourceBadge fieldName="model" {...badgeProps} />
              </div>
              <input type="text" value={g.model || ''} onChange={e => setG('model', e.target.value)}
                placeholder="gpt-4o" className={inputCls} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm font-medium text-gray-700">Temperature</label>
                  <SourceBadge fieldName="temperature" {...badgeProps} />
                </div>
                <input type="number" step="0.1" min="0" max="2" value={g.temperature ?? ''} onChange={e => setG('temperature', e.target.value)}
                  placeholder="0.7" className={inputCls} />
              </div>
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm font-medium text-gray-700">Max Tokens</label>
                  <SourceBadge fieldName="max_tokens" {...badgeProps} />
                </div>
                <input type="number" min="1" value={g.max_tokens ?? ''} onChange={e => setG('max_tokens', e.target.value)}
                  placeholder="4096" className={inputCls} />
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Anthropic (Claude)">
            <ProviderHeader provider="anthropic" currentDefault={currentDefaultModel.provider} status={providerStatus.anthropic} testing={providerTesting.anthropic} onTest={() => handleTestProvider('anthropic')} onMakeDefault={() => handleMakeDefault('anthropic')} canMakeDefault={Boolean((g.anthropic_model || '').trim())} />
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">
                  API Key {masked.anthropic_api_key_masked && <span className="text-gray-400 font-normal">(current: {masked.anthropic_api_key_masked})</span>}
                </label>
                <SourceBadge fieldName="anthropic_api_key" {...badgeProps} />
              </div>
              <input type="password" value={g.anthropic_api_key || ''} onChange={e => setG('anthropic_api_key', e.target.value)}
                placeholder="Leave empty to inherit the global key"
                className={inputCls} autoComplete="new-password" />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Default Model</label>
                <SourceBadge fieldName="anthropic_model" {...badgeProps} />
              </div>
              <input type="text" value={g.anthropic_model || ''} onChange={e => setG('anthropic_model', e.target.value)}
                placeholder="claude-opus-4-7" className={inputCls} />
            </div>
          </SectionCard>

          <SectionCard title="Google (Gemini)">
            <ProviderHeader provider="google" currentDefault={currentDefaultModel.provider} status={providerStatus.google} testing={providerTesting.google} onTest={() => handleTestProvider('google')} onMakeDefault={() => handleMakeDefault('google')} canMakeDefault={Boolean((g.google_model || '').trim())} />
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">
                  API Key {masked.google_api_key_masked && <span className="text-gray-400 font-normal">(current: {masked.google_api_key_masked})</span>}
                </label>
                <SourceBadge fieldName="google_api_key" {...badgeProps} />
              </div>
              <input type="password" value={g.google_api_key || ''} onChange={e => setG('google_api_key', e.target.value)}
                placeholder="Leave empty to inherit the global key"
                className={inputCls} autoComplete="new-password" />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Default Model</label>
                <SourceBadge fieldName="google_model" {...badgeProps} />
              </div>
              <input type="text" value={g.google_model || ''} onChange={e => setG('google_model', e.target.value)}
                placeholder="gemini-2.0-flash" className={inputCls} />
            </div>
          </SectionCard>
        </div>
      )}

      {/* Tab: Local Models */}
      {activeTab === 'local' && (
        <div className="space-y-5">
          <SectionCard title="Ollama">
            <ProviderHeader provider="ollama" currentDefault={currentDefaultModel.provider} status={providerStatus.ollama} testing={providerTesting.ollama} onTest={() => handleTestProvider('ollama')} onMakeDefault={() => handleMakeDefault('ollama')} canMakeDefault={Boolean((g.ollama_model || '').trim())} />
            <p className="text-sm text-gray-600">
              Run models locally with <span className="font-medium text-gray-800">Ollama</span>.
            </p>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Base URL</label>
                <SourceBadge fieldName="ollama_base_url" {...badgeProps} />
              </div>
              <p className="text-xs text-gray-500 mb-1">Ollama server address (default: http://localhost:11434)</p>
              <div className="flex gap-2">
                <input type="text" value={g.ollama_base_url || ''} onChange={e => setG('ollama_base_url', e.target.value)}
                  placeholder="http://localhost:11434" className={`flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none`} />
                <button type="button" onClick={() => handleFetchModels('ollama')} disabled={fetchingModels.ollama}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap">
                  {fetchingModels.ollama ? <Loader className="w-4 h-4 animate-spin" /> : <Wifi className="w-4 h-4" />}
                  Fetch models
                </button>
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Model</label>
                <SourceBadge fieldName="ollama_model" {...badgeProps} />
              </div>
              <p className="text-xs text-gray-500 mb-1">The model name as shown by <code className="bg-gray-100 rounded px-1">ollama list</code>.</p>
              {fetchedModels.ollama.length > 0 ? (
                <select value={g.ollama_model || ''} onChange={e => setG('ollama_model', e.target.value)}
                  className={inputCls}>
                  <option value="">— select a model —</option>
                  {fetchedModels.ollama.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input type="text" value={g.ollama_model || ''} onChange={e => setG('ollama_model', e.target.value)}
                  placeholder="llama3" className={inputCls} />
              )}
              {fetchErrors.ollama && <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{fetchErrors.ollama}</p>}
            </div>
          </SectionCard>

          <SectionCard title="LM Studio">
            <ProviderHeader provider="lmstudio" currentDefault={currentDefaultModel.provider} status={providerStatus.lmstudio} testing={providerTesting.lmstudio} onTest={() => handleTestProvider('lmstudio')} onMakeDefault={() => handleMakeDefault('lmstudio')} canMakeDefault={Boolean((g.lmstudio_model || '').trim())} />
            <p className="text-sm text-gray-600">
              <span className="font-medium text-gray-800">LM Studio</span> exposes an OpenAI-compatible server.
            </p>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Base URL</label>
                <SourceBadge fieldName="lmstudio_base_url" {...badgeProps} />
              </div>
              <p className="text-xs text-gray-500 mb-1">LM Studio server address (default: http://localhost:1234)</p>
              <div className="flex gap-2">
                <input type="text" value={g.lmstudio_base_url || ''} onChange={e => setG('lmstudio_base_url', e.target.value)}
                  placeholder="http://localhost:1234" className={`flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none`} />
                <button type="button" onClick={() => handleFetchModels('lmstudio')} disabled={fetchingModels.lmstudio}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap">
                  {fetchingModels.lmstudio ? <Loader className="w-4 h-4 animate-spin" /> : <Wifi className="w-4 h-4" />}
                  Fetch models
                </button>
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Model</label>
                <SourceBadge fieldName="lmstudio_model" {...badgeProps} />
              </div>
              <p className="text-xs text-gray-500 mb-1">The model identifier shown in LM Studio.</p>
              {fetchedModels.lmstudio.length > 0 ? (
                <select value={g.lmstudio_model || ''} onChange={e => setG('lmstudio_model', e.target.value)}
                  className={inputCls}>
                  <option value="">— select a model —</option>
                  {fetchedModels.lmstudio.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input type="text" value={g.lmstudio_model || ''} onChange={e => setG('lmstudio_model', e.target.value)}
                  placeholder="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF"
                  className={inputCls} />
              )}
              {fetchErrors.lmstudio && <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{fetchErrors.lmstudio}</p>}
            </div>
          </SectionCard>
        </div>
      )}

      {/* Tab: Observability */}
      {activeTab === 'observability' && (
        <div className="space-y-5">
          <SectionCard title="Langfuse">
            <p className="text-sm text-gray-600">
              Connect to a <span className="font-medium text-gray-800">Langfuse</span> instance to trace and observe LLM calls.
            </p>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">
                  Secret Key {masked.langfuse_secret_key_masked && <span className="text-gray-400 font-normal">(current: {masked.langfuse_secret_key_masked})</span>}
                </label>
                <SourceBadge fieldName="langfuse_secret_key" {...badgeProps} />
              </div>
              <input type="password" value={g.langfuse_secret_key || ''} onChange={e => setG('langfuse_secret_key', e.target.value)}
                placeholder="Leave empty to inherit the global key"
                className={inputCls} autoComplete="new-password" />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">
                  Public Key {masked.langfuse_public_key_masked && <span className="text-gray-400 font-normal">(current: {masked.langfuse_public_key_masked})</span>}
                </label>
                <SourceBadge fieldName="langfuse_public_key" {...badgeProps} />
              </div>
              <input type="password" value={g.langfuse_public_key || ''} onChange={e => setG('langfuse_public_key', e.target.value)}
                placeholder="Leave empty to inherit the global key"
                className={inputCls} autoComplete="new-password" />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Base URL</label>
                <SourceBadge fieldName="langfuse_base_url" {...badgeProps} />
              </div>
              <input type="text" value={g.langfuse_base_url || ''} onChange={e => setG('langfuse_base_url', e.target.value)}
                placeholder="https://cloud.langfuse.com"
                className={inputCls} />
            </div>
          </SectionCard>
        </div>
      )}

      {/* Tab: RAG & Vectors */}
      {activeTab === 'rag' && (
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
                  ? <><strong>RAG pipeline active</strong> — embedded with <strong>{g.rag_embedding_provider}</strong>, stored in <strong>{g.rag_vector_db}</strong>.</>
                  : <>RAG partially configured. Set both a Vector Database and Embedding Provider to enable.</>}
              </div>
            </div>
          )}

          <SectionCard title="Vector Database">
            <p className="text-sm text-gray-600">Choose where processed document chunks are stored for similarity search.</p>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Provider</label>
                <SourceBadge fieldName="rag_vector_db" {...badgeProps} />
              </div>
              <select value={g.rag_vector_db || 'none'} onChange={e => setG('rag_vector_db', e.target.value)}
                className={inputCls}>
                {VECTOR_DBS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
              {g.rag_vector_db !== 'none' && (
                <p className="text-xs text-gray-400 mt-1">Install: <code className="bg-gray-100 rounded px-1 py-0.5">{INSTALL_HINTS[g.rag_vector_db]}</code></p>
              )}
            </div>

            {g.rag_vector_db !== 'none' && (
              <>
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <label className="text-sm font-medium text-gray-700">Server URL</label>
                    <SourceBadge fieldName="rag_vector_db_url" {...badgeProps} />
                  </div>
                  <input type="text" value={g.rag_vector_db_url || ''} onChange={e => setG('rag_vector_db_url', e.target.value)}
                    placeholder={g.rag_vector_db === 'qdrant' ? 'http://localhost:6333' : g.rag_vector_db === 'pinecone' ? 'https://your-index-host.pinecone.io' : 'http://localhost:8000'}
                    className={inputCls} />
                </div>
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <label className="text-sm font-medium text-gray-700">Collection / Index Name</label>
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
                        API Key {masked.rag_vector_db_api_key_masked && <span className="text-gray-400 font-normal">(current: {masked.rag_vector_db_api_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="rag_vector_db_api_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.rag_vector_db_api_key || ''} onChange={e => setG('rag_vector_db_api_key', e.target.value)}
                      placeholder="Leave empty to inherit the global key"
                      className={inputCls} autoComplete="new-password" />
                  </div>
                )}
              </>
            )}
          </SectionCard>

          <SectionCard title="Embedding Model">
            <p className="text-sm text-gray-600">Choose the model used to convert text chunks into vectors.</p>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Provider</label>
                <SourceBadge fieldName="rag_embedding_provider" {...badgeProps} />
              </div>
              <select value={g.rag_embedding_provider || 'none'} onChange={e => setG('rag_embedding_provider', e.target.value)}
                className={inputCls}>
                {EMBEDDING_PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
              {g.rag_embedding_provider !== 'none' && (
                <p className="text-xs text-gray-400 mt-1">Install: <code className="bg-gray-100 rounded px-1 py-0.5">{INSTALL_HINTS[g.rag_embedding_provider]}</code></p>
              )}
            </div>

            {g.rag_embedding_provider !== 'none' && (
              <>
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <label className="text-sm font-medium text-gray-700">Model Name</label>
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
                        API Key {masked.rag_embedding_api_key_masked && <span className="text-gray-400 font-normal">(current: {masked.rag_embedding_api_key_masked})</span>}
                      </label>
                      <SourceBadge fieldName="rag_embedding_api_key" {...badgeProps} />
                    </div>
                    <input type="password" value={g.rag_embedding_api_key || ''} onChange={e => setG('rag_embedding_api_key', e.target.value)}
                      placeholder="Leave empty to inherit the global key"
                      className={inputCls} autoComplete="new-password" />
                  </div>
                )}
                {g.rag_embedding_provider === 'ollama' && (
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <label className="text-sm font-medium text-gray-700">Ollama Base URL</label>
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

      {/* Tab: System */}
      {activeTab === 'system' && (
        <div className="space-y-5">
          <SectionCard title="Orchestrator & System" futureDev>
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Workspace Root</label>
              <input type="text" value={g.workspace_root || ''} disabled
                placeholder="/path/to/workspaces"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-400 cursor-not-allowed" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm font-medium text-gray-700">Poll Interval (s)</label>
                  <SourceBadge fieldName="orch_poll_interval" {...badgeProps} />
                </div>
                <input type="number" step="0.5" min="0.5" value={g.orch_poll_interval ?? ''} onChange={e => setG('orch_poll_interval', e.target.value)}
                  placeholder="5"
                  className={inputCls} />
              </div>
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <label className="text-sm font-medium text-gray-700">Log Level</label>
                  <SourceBadge fieldName="orch_log_level" {...badgeProps} />
                </div>
                <select value={g.orch_log_level || 'INFO'} onChange={e => setG('orch_log_level', e.target.value)}
                  className={inputCls}>
                  {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Agent Execution Mode">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Agent Mode</label>
                <SourceBadge fieldName="agent_mode" {...badgeProps} />
                {activeWorkspace && (
                  <span className="text-xs text-indigo-600 font-medium bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
                    workspace: {activeWorkspace}
                  </span>
                )}
              </div>
              <p className="text-xs text-gray-500 mb-2">
                <strong>Local</strong> — agents run as subprocesses. <strong>Docker</strong> — each agent is launched in a Docker container.
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
                    <label className="text-sm font-medium text-gray-700">Docker Image</label>
                    <SourceBadge fieldName="agent_docker_image" {...badgeProps} />
                  </div>
                  <input type="text" value={g.agent_docker_image || ''} onChange={e => setG('agent_docker_image', e.target.value)}
                    placeholder="agents-hub:latest"
                    className={inputCls} />
                </div>
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <label className="text-sm font-medium text-gray-700">Docker Network</label>
                    <SourceBadge fieldName="agent_docker_network" {...badgeProps} />
                  </div>
                  <input type="text" value={g.agent_docker_network || ''} onChange={e => setG('agent_docker_network', e.target.value)}
                    placeholder="agents_hub_default"
                    className={inputCls} />
                </div>
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <label className="text-sm font-medium text-gray-700">Extra Docker Args</label>
                    <SourceBadge fieldName="agent_docker_extra_args" {...badgeProps} />
                  </div>
                  <input type="text" value={g.agent_docker_extra_args || ''} onChange={e => setG('agent_docker_extra_args', e.target.value)}
                    placeholder="--add-host host.docker.internal:host-gateway"
                    className={inputCls} />
                </div>
              </div>
            )}
          </SectionCard>

          <SectionCard title="Task Assignment">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-sm font-medium text-gray-700">Assignment Mode</label>
                <SourceBadge fieldName="task_assignment_mode" {...badgeProps} />
              </div>
              <p className="text-xs text-gray-500 mb-2">
                <strong>Any agent</strong> — tasks assigned to any registered agent, process started on demand.<br />
                <strong>Running nodes only</strong> — tasks only assigned to agents with an active node.
              </p>
              <div className="flex gap-4">
                {[
                  { value: 'any', label: 'Any agent' },
                  { value: 'nodes_only', label: 'Running nodes only' },
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
  );
}
