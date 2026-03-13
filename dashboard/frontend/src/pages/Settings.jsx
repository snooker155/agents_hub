import { useState, useEffect } from 'react';
import axios from 'axios';
import { Eye, EyeOff, Save, RefreshCw, Key, Cpu, Activity, Wrench, Database, CheckCircle, AlertCircle, Wifi } from 'lucide-react';

const api = axios.create({ baseURL: 'http://localhost:8000' });

const OPENAI_MODELS = [
  'gpt-4o', 'gpt-4o-mini', 'gpt-5', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo',
];
const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

const TABS = [
  { id: 'apikeys',    label: 'API Keys & Models', icon: Key },
  { id: 'local',      label: 'Local Models',      icon: Cpu },
  { id: 'observability', label: 'Observability',  icon: Activity },
  { id: 'rag',        label: 'RAG & Vectors',     icon: Database },
  { id: 'system',     label: 'System',            icon: Wrench },
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

const OPENAI_EMBEDDING_MODELS = [
  'text-embedding-3-small',
  'text-embedding-3-large',
  'text-embedding-ada-002',
];

const GOOGLE_EMBEDDING_MODELS = [
  'models/text-embedding-004',
  'models/embedding-001',
];

const INSTALL_HINTS = {
  chroma:   'pip install chromadb',
  pinecone: 'pip install pinecone-client',
  qdrant:   'pip install qdrant-client',
  openai:   'pip install openai  (already installed if you use OpenAI for LLM)',
  'sentence-transformers': 'pip install sentence-transformers',
  ollama:   'No extra package — uses Ollama HTTP API',
  google:   'pip install google-generativeai',
};

function PasswordField({ label, name, value, onChange, placeholder, hint }) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {hint && <p className="text-xs text-gray-500 mb-1">{hint}</p>}
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          name={name}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
        />
        <button
          type="button"
          onClick={() => setShow(s => !s)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
        >
          {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

function Field({ label, name, value, onChange, placeholder, type = 'text', hint }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {hint && <p className="text-xs text-gray-500 mb-1">{hint}</p>}
      <input
        type={type}
        name={name}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
      />
    </div>
  );
}

function SectionCard({ title, children }) {
  return (
    <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
      <h2 className="text-base font-semibold text-gray-800">{title}</h2>
      {children}
    </section>
  );
}

export default function Settings() {
  const [activeTab, setActiveTab] = useState('apikeys');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [connTest, setConnTest] = useState({ ollama: null, lmstudio: null }); // null | {ok, models?, error}
  const [connTesting, setConnTesting] = useState({ ollama: false, lmstudio: false });

  const [masked, setMasked] = useState({
    openai_api_key_masked: '',
    anthropic_api_key_masked: '',
    google_api_key_masked: '',
    langfuse_secret_key_masked: '',
    langfuse_public_key_masked: '',
    rag_vector_db_api_key_masked: '',
    rag_embedding_api_key_masked: '',
  });

  const [form, setForm] = useState({
    // API keys (cloud)
    openai_api_key: '',
    anthropic_api_key: '',
    google_api_key: '',
    // Model settings
    model: 'gpt-4o',
    openai_base_url: '',
    temperature: '0.0',
    max_tokens: '15000',
    // Local models
    ollama_base_url: 'http://localhost:11434',
    ollama_model: '',
    lmstudio_base_url: 'http://localhost:1234',
    lmstudio_model: '',
    // Observability
    langfuse_secret_key: '',
    langfuse_public_key: '',
    langfuse_base_url: '',
    // System
    workspace_root: './out',
    orch_poll_interval: '5.0',
    orch_log_level: 'INFO',
    // RAG — vector store
    rag_vector_db: 'none',
    rag_vector_db_url: '',
    rag_vector_db_api_key: '',
    rag_vector_db_collection: 'agents_hub_rag',
    // RAG — embedding
    rag_embedding_provider: 'none',
    rag_embedding_model: 'text-embedding-3-small',
    rag_embedding_api_key: '',
    rag_embedding_base_url: 'http://localhost:11434',
  });

  const SECRET_FIELDS = [
    'openai_api_key', 'anthropic_api_key', 'google_api_key',
    'langfuse_secret_key', 'langfuse_public_key',
    'rag_vector_db_api_key', 'rag_embedding_api_key',
  ];

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await api.get('/api/settings');
      setMasked({
        openai_api_key_masked: data.openai_api_key_masked,
        anthropic_api_key_masked: data.anthropic_api_key_masked,
        google_api_key_masked: data.google_api_key_masked,
        langfuse_secret_key_masked: data.langfuse_secret_key_masked,
        langfuse_public_key_masked: data.langfuse_public_key_masked,
        rag_vector_db_api_key_masked: data.rag_vector_db_api_key_masked,
        rag_embedding_api_key_masked: data.rag_embedding_api_key_masked,
      });
      setForm(f => ({
        ...f,
        model: data.model,
        openai_base_url: data.openai_base_url,
        temperature: String(data.temperature),
        max_tokens: String(data.max_tokens),
        ollama_base_url: data.ollama_base_url,
        ollama_model: data.ollama_model,
        lmstudio_base_url: data.lmstudio_base_url,
        lmstudio_model: data.lmstudio_model,
        langfuse_base_url: data.langfuse_base_url,
        workspace_root: data.workspace_root,
        orch_poll_interval: String(data.orch_poll_interval),
        orch_log_level: data.orch_log_level,
        // RAG
        rag_vector_db: data.rag_vector_db,
        rag_vector_db_url: data.rag_vector_db_url,
        rag_vector_db_collection: data.rag_vector_db_collection,
        rag_embedding_provider: data.rag_embedding_provider,
        rag_embedding_model: data.rag_embedding_model,
        rag_embedding_base_url: data.rag_embedding_base_url,
      }));
    } catch (e) {
      setError('Failed to load settings: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleChange = e => {
    const { name, value } = e.target;
    setForm(f => ({ ...f, [name]: value }));
  };

  const handleTestConnection = async (provider) => {
    const baseUrl = provider === 'ollama' ? form.ollama_base_url : form.lmstudio_base_url;
    setConnTesting(s => ({ ...s, [provider]: true }));
    setConnTest(s => ({ ...s, [provider]: null }));
    try {
      const { data } = await api.post('/api/settings/test-local-model', { provider, base_url: baseUrl });
      setConnTest(s => ({ ...s, [provider]: data }));
    } catch (e) {
      setConnTest(s => ({ ...s, [provider]: { ok: false, error: e.message } }));
    } finally {
      setConnTesting(s => ({ ...s, [provider]: false }));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const payload = {};
      for (const [k, v] of Object.entries(form)) {
        if (SECRET_FIELDS.includes(k)) {
          if (v.trim()) payload[k] = v.trim();
        } else {
          payload[k] = v;
        }
      }
      await api.put('/api/settings', payload);
      setSaved(true);
      await load();
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError('Failed to save: ' + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
        >
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>

      {saved && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          Settings saved. Restart the backend for changes to take effect.
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? 'bg-white text-indigo-600 shadow-sm'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
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
            <PasswordField
              label={`API Key (current: ${masked.openai_api_key_masked})`}
              name="openai_api_key"
              value={form.openai_api_key}
              onChange={handleChange}
              placeholder="sk-… (leave blank to keep existing)"
            />
            <Field
              label="Base URL override"
              name="openai_base_url"
              value={form.openai_base_url}
              onChange={handleChange}
              placeholder="https://api.openai.com/v1  (leave blank for default)"
              hint="Use this to point to an OpenAI-compatible proxy or Azure endpoint."
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Default Model</label>
              <div className="flex gap-2">
                <select
                  name="model"
                  value={OPENAI_MODELS.includes(form.model) ? form.model : '__custom__'}
                  onChange={e => {
                    if (e.target.value !== '__custom__') {
                      setForm(f => ({ ...f, model: e.target.value }));
                    }
                  }}
                  className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  {OPENAI_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                  {!OPENAI_MODELS.includes(form.model) && (
                    <option value="__custom__">{form.model}</option>
                  )}
                </select>
                <input
                  type="text"
                  name="model"
                  value={form.model}
                  onChange={handleChange}
                  placeholder="or type custom model name"
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Temperature</label>
                <input
                  type="number"
                  name="temperature"
                  value={form.temperature}
                  onChange={handleChange}
                  min="0" max="2" step="0.05"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                <input
                  type="number"
                  name="max_tokens"
                  value={form.max_tokens}
                  onChange={handleChange}
                  min="256" step="256"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Anthropic (Claude)">
            <PasswordField
              label={`API Key (current: ${masked.anthropic_api_key_masked})`}
              name="anthropic_api_key"
              value={form.anthropic_api_key}
              onChange={handleChange}
              placeholder="sk-ant-… (leave blank to keep existing)"
              hint="Required when using claude-* models."
            />
          </SectionCard>

          <SectionCard title="Google (Gemini)">
            <PasswordField
              label={`API Key (current: ${masked.google_api_key_masked})`}
              name="google_api_key"
              value={form.google_api_key}
              onChange={handleChange}
              placeholder="AIza… (leave blank to keep existing)"
              hint="Required when using gemini-* models."
            />
          </SectionCard>
        </div>
      )}

      {/* Tab: Local Models */}
      {activeTab === 'local' && (
        <div className="space-y-5">
          <SectionCard title="Ollama">
            <p className="text-sm text-gray-600">
              Run models locally with{' '}
              <span className="font-medium text-gray-800">Ollama</span>. Start Ollama, then set the
              base URL and the model name you have pulled (e.g.{' '}
              <code className="text-xs bg-gray-100 rounded px-1 py-0.5">llama3</code>,{' '}
              <code className="text-xs bg-gray-100 rounded px-1 py-0.5">mistral</code>).
            </p>
            <div className="flex gap-2 items-end">
              <div className="flex-1">
                <Field
                  label="Base URL"
                  name="ollama_base_url"
                  value={form.ollama_base_url}
                  onChange={handleChange}
                  placeholder="http://localhost:11434"
                  hint="Default Ollama server address."
                />
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('ollama')}
                disabled={connTesting.ollama}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap"
              >
                {connTesting.ollama
                  ? <RefreshCw className="w-4 h-4 animate-spin" />
                  : <Wifi className="w-4 h-4" />}
                Test
              </button>
            </div>
            {connTest.ollama && (
              connTest.ollama.ok ? (
                <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm bg-green-50 text-green-800 border border-green-200">
                  <CheckCircle className="w-4 h-4 shrink-0" />
                  <span>Connected — {connTest.ollama.models?.length ?? 0} model(s) available</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm bg-red-50 text-red-800 border border-red-200">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{connTest.ollama.error}</span>
                </div>
              )
            )}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
              <p className="text-xs text-gray-500 mb-1">The model name as shown by <code className="bg-gray-100 rounded px-1">ollama list</code>.</p>
              {connTest.ollama?.ok && connTest.ollama.models?.length > 0 ? (
                <select
                  name="ollama_model"
                  value={form.ollama_model}
                  onChange={handleChange}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  <option value="">— select a model —</option>
                  {connTest.ollama.models.map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  name="ollama_model"
                  value={form.ollama_model}
                  onChange={handleChange}
                  placeholder="llama3"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              )}
            </div>
          </SectionCard>

          <SectionCard title="LM Studio">
            <p className="text-sm text-gray-600">
              <span className="font-medium text-gray-800">LM Studio</span> exposes an
              OpenAI-compatible server. Enable it under{' '}
              <span className="italic">Local Server</span> in the LM Studio app, then set the URL
              and the loaded model identifier.
            </p>
            <div className="flex gap-2 items-end">
              <div className="flex-1">
                <Field
                  label="Base URL"
                  name="lmstudio_base_url"
                  value={form.lmstudio_base_url}
                  onChange={handleChange}
                  placeholder="http://localhost:1234"
                  hint="Default LM Studio server address."
                />
              </div>
              <button
                type="button"
                onClick={() => handleTestConnection('lmstudio')}
                disabled={connTesting.lmstudio}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap"
              >
                {connTesting.lmstudio
                  ? <RefreshCw className="w-4 h-4 animate-spin" />
                  : <Wifi className="w-4 h-4" />}
                Test
              </button>
            </div>
            {connTest.lmstudio && (
              connTest.lmstudio.ok ? (
                <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm bg-green-50 text-green-800 border border-green-200">
                  <CheckCircle className="w-4 h-4 shrink-0" />
                  <span>Connected — {connTest.lmstudio.models?.length ?? 0} model(s) available</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm bg-red-50 text-red-800 border border-red-200">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{connTest.lmstudio.error}</span>
                </div>
              )
            )}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
              <p className="text-xs text-gray-500 mb-1">The model identifier shown in LM Studio.</p>
              {connTest.lmstudio?.ok && connTest.lmstudio.models?.length > 0 ? (
                <select
                  name="lmstudio_model"
                  value={form.lmstudio_model}
                  onChange={handleChange}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  <option value="">— select a model —</option>
                  {connTest.lmstudio.models.map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  name="lmstudio_model"
                  value={form.lmstudio_model}
                  onChange={handleChange}
                  placeholder="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              )}
            </div>
          </SectionCard>
        </div>
      )}

      {/* Tab: Observability */}
      {activeTab === 'observability' && (
        <div className="space-y-5">
          <SectionCard title="Langfuse">
            <p className="text-sm text-gray-600">
              Connect to a{' '}
              <span className="font-medium text-gray-800">Langfuse</span> instance to trace and
              observe LLM calls. Leave keys blank to disable tracing.
            </p>
            <PasswordField
              label={`Secret Key (current: ${masked.langfuse_secret_key_masked})`}
              name="langfuse_secret_key"
              value={form.langfuse_secret_key}
              onChange={handleChange}
              placeholder="sk-lf-… (leave blank to keep existing)"
            />
            <PasswordField
              label={`Public Key (current: ${masked.langfuse_public_key_masked})`}
              name="langfuse_public_key"
              value={form.langfuse_public_key}
              onChange={handleChange}
              placeholder="pk-lf-… (leave blank to keep existing)"
            />
            <Field
              label="Base URL"
              name="langfuse_base_url"
              value={form.langfuse_base_url}
              onChange={handleChange}
              placeholder="https://cloud.langfuse.com  (or self-hosted URL)"
            />
          </SectionCard>
        </div>
      )}

      {/* Tab: RAG & Vectors */}
      {activeTab === 'rag' && (
        <div className="space-y-5">
          {/* Status banner */}
          {(form.rag_vector_db !== 'none' || form.rag_embedding_provider !== 'none') && (
            <div className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm ${
              form.rag_vector_db !== 'none' && form.rag_embedding_provider !== 'none'
                ? 'bg-green-50 border-green-200 text-green-800'
                : 'bg-yellow-50 border-yellow-200 text-yellow-800'
            }`}>
              {form.rag_vector_db !== 'none' && form.rag_embedding_provider !== 'none'
                ? <CheckCircle className="w-4 h-4 mt-0.5 shrink-0" />
                : <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />}
              <div>
                {form.rag_vector_db !== 'none' && form.rag_embedding_provider !== 'none'
                  ? <><strong>RAG pipeline active</strong> — files processed in the Memory Manager will be embedded with <strong>{form.rag_embedding_provider}</strong> and stored in <strong>{form.rag_vector_db}</strong>.</>
                  : <>RAG is partially configured. Set both a <strong>Vector Database</strong> and an <strong>Embedding Provider</strong> to enable full vector search.</>}
              </div>
            </div>
          )}

          {/* Vector Database */}
          <SectionCard title="Vector Database">
            <p className="text-sm text-gray-600">
              Choose where processed document chunks are stored for similarity search.
              Changes take effect immediately — no backend restart needed.
            </p>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
              <select name="rag_vector_db" value={form.rag_vector_db} onChange={handleChange}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                {VECTOR_DBS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
              {form.rag_vector_db !== 'none' && (
                <p className="text-xs text-gray-400 mt-1">Install: <code className="bg-gray-100 rounded px-1 py-0.5">{INSTALL_HINTS[form.rag_vector_db]}</code></p>
              )}
            </div>

            {form.rag_vector_db !== 'none' && (
              <>
                <Field
                  label={form.rag_vector_db === 'chroma' ? 'ChromaDB URL (leave blank for local ./chroma_db)' : 'Server URL'}
                  name="rag_vector_db_url"
                  value={form.rag_vector_db_url}
                  onChange={handleChange}
                  placeholder={
                    form.rag_vector_db === 'chroma'   ? 'http://localhost:8000  (blank = local persistent)' :
                    form.rag_vector_db === 'qdrant'   ? 'http://localhost:6333' :
                    'https://…'
                  }
                  hint={form.rag_vector_db === 'chroma' ? 'Leave blank to use a local persistent directory (./chroma_db).' : undefined}
                />
                <Field
                  label="Collection / Index Name"
                  name="rag_vector_db_collection"
                  value={form.rag_vector_db_collection}
                  onChange={handleChange}
                  placeholder="agents_hub_rag"
                />
                {(form.rag_vector_db === 'pinecone' || form.rag_vector_db === 'qdrant') && (
                  <PasswordField
                    label={`API Key (current: ${masked.rag_vector_db_api_key_masked})`}
                    name="rag_vector_db_api_key"
                    value={form.rag_vector_db_api_key}
                    onChange={handleChange}
                    placeholder="Leave blank to keep existing"
                  />
                )}
              </>
            )}
          </SectionCard>

          {/* Embedding Model */}
          <SectionCard title="Embedding Model">
            <p className="text-sm text-gray-600">
              Choose the model used to convert text chunks into vectors.
            </p>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
              <select name="rag_embedding_provider" value={form.rag_embedding_provider} onChange={handleChange}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                {EMBEDDING_PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
              {form.rag_embedding_provider !== 'none' && (
                <p className="text-xs text-gray-400 mt-1">Install: <code className="bg-gray-100 rounded px-1 py-0.5">{INSTALL_HINTS[form.rag_embedding_provider]}</code></p>
              )}
            </div>

            {form.rag_embedding_provider !== 'none' && (
              <>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Model Name</label>
                  {form.rag_embedding_provider === 'openai' ? (
                    <div className="flex gap-2">
                      <select name="rag_embedding_model"
                        value={OPENAI_EMBEDDING_MODELS.includes(form.rag_embedding_model) ? form.rag_embedding_model : '__custom__'}
                        onChange={e => { if (e.target.value !== '__custom__') setForm(f => ({ ...f, rag_embedding_model: e.target.value })); }}
                        className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                        {OPENAI_EMBEDDING_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                        {!OPENAI_EMBEDDING_MODELS.includes(form.rag_embedding_model) && (
                          <option value="__custom__">{form.rag_embedding_model}</option>
                        )}
                      </select>
                      <input type="text" name="rag_embedding_model" value={form.rag_embedding_model} onChange={handleChange}
                        placeholder="custom model" className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                    </div>
                  ) : form.rag_embedding_provider === 'google' ? (
                    <div className="flex gap-2">
                      <select name="rag_embedding_model"
                        value={GOOGLE_EMBEDDING_MODELS.includes(form.rag_embedding_model) ? form.rag_embedding_model : '__custom__'}
                        onChange={e => { if (e.target.value !== '__custom__') setForm(f => ({ ...f, rag_embedding_model: e.target.value })); }}
                        className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                        {GOOGLE_EMBEDDING_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                        {!GOOGLE_EMBEDDING_MODELS.includes(form.rag_embedding_model) && (
                          <option value="__custom__">{form.rag_embedding_model}</option>
                        )}
                      </select>
                      <input type="text" name="rag_embedding_model" value={form.rag_embedding_model} onChange={handleChange}
                        placeholder="custom model" className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                    </div>
                  ) : (
                    <input type="text" name="rag_embedding_model" value={form.rag_embedding_model} onChange={handleChange}
                      placeholder={
                        form.rag_embedding_provider === 'sentence-transformers' ? 'all-MiniLM-L6-v2' :
                        form.rag_embedding_provider === 'ollama' ? 'nomic-embed-text' :
                        'model name'
                      }
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                  )}
                </div>

                {(form.rag_embedding_provider === 'openai' || form.rag_embedding_provider === 'google') && (
                  <PasswordField
                    label={`API Key (current: ${masked.rag_embedding_api_key_masked}) — leave blank to reuse the provider key above`}
                    name="rag_embedding_api_key"
                    value={form.rag_embedding_api_key}
                    onChange={handleChange}
                    placeholder="Leave blank to inherit from API Keys tab"
                  />
                )}

                {form.rag_embedding_provider === 'ollama' && (
                  <Field
                    label="Ollama Base URL"
                    name="rag_embedding_base_url"
                    value={form.rag_embedding_base_url}
                    onChange={handleChange}
                    placeholder="http://localhost:11434"
                  />
                )}
              </>
            )}
          </SectionCard>
        </div>
      )}

      {/* Tab: System */}
      {activeTab === 'system' && (
        <div className="space-y-5">
          <SectionCard title="Orchestrator & System">
            <Field
              label="Workspace Root"
              name="workspace_root"
              value={form.workspace_root}
              onChange={handleChange}
              hint="Directory where workspaces are created."
            />
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Poll Interval (s)</label>
                <input
                  type="number"
                  name="orch_poll_interval"
                  value={form.orch_poll_interval}
                  onChange={handleChange}
                  min="1" step="1"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Log Level</label>
                <select
                  name="orch_log_level"
                  value={form.orch_log_level}
                  onChange={handleChange}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  {LOG_LEVELS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
            </div>
          </SectionCard>
        </div>
      )}
    </div>
  );
}
