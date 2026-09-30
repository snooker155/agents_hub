import { useState, useEffect, useCallback } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import axios from 'axios';
import {
  RefreshCw, Key, Cpu, Activity, Wrench, Database, CheckCircle, AlertCircle, Wifi, Lock, Save, Trash2, Server, X, ScrollText, Settings as SettingsIcon, Link2, Sparkles, Globe, MonitorSmartphone,
} from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import { MULTI, TOKEN, useAuth } from '../components/auth';
import { getApiToken, setApiToken, API_ORIGIN } from '../api';
import { getDemo, setDemo } from '../api/demo';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { SectionCard, inputCls } from '../components/settingsUi';
import { useI18n } from '../i18n';
import {
  ProviderStatusBadge, SaveWorkspaceSettingsButton, WorkspaceSettingsStatus,
  ProvidersSection, LocalServersSection, ExecutionSection, RagSection, ObservabilitySection, LoggingSection, WebSearchSection,
} from '../components/settings/WorkspaceSettingsSections';
import { useWorkspaceSettings } from '../components/settings/useWorkspaceSettings';
import PageLoader from '../components/PageLoader';
const api = axios.create({ baseURL: 'http://localhost:8000' });


// Sections, grouped and laid out the way the Docs page lays out its chapters:
// a left-hand menu and one content column. The grouping is the point — these
// nine panels used to be nine tabs in a single row that ran off the page, with
// model settings and connectors sitting side by side for no reason.
//
// `workspaceScoped` marks the sections whose fields are workspace overrides
// (written by the page's Save button). They are drawn by
// components/settings/WorkspaceSettingsSections.jsx, which the Settings tab of
// a workspace renders too.
//
// Telegram, Git and Blender used to be a group here. They are connectors —
// things you attach — and they now live under Connect → Connectors, which is
// where someone looks for them. A pointer at the foot of this page's sidebar
// takes anyone who looks here first.
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
    key: 'system',
    items: [
      { id: 'execution',     key: 'execution',     icon: Wrench,     workspaceScoped: true },
      { id: 'webSearch',     key: 'webSearch',     icon: Globe },
      { id: 'rag',           key: 'rag',           icon: Database,   workspaceScoped: true },
      { id: 'observability', key: 'observability', icon: Activity,   workspaceScoped: true },
      { id: 'logging',       key: 'logging',       icon: ScrollText, workspaceScoped: true },
      { id: 'apiAccess',     key: 'apiAccess',     icon: Lock },
      { id: 'demo',          key: 'demo',          icon: Sparkles },
    ],
  },
];

const SECTIONS = GROUPS.flatMap((group) => group.items);

// Sections that used to live here and are now their own page. Links and
// bookmarks to them are out in the world, and falling back to the first section
// would answer them by quietly showing something else.
const MOVED_TO_CONNECTORS = ['telegram', 'git', 'blender'];


// ── Custom backends tab ───────────────────────────────────────────────────────

const BLANK_BACKEND = { id: '', label: '', adapter: 'openai', base_url: '', api_key: '', default_model: '', headers: '' };

// The demo workspace: a workspace named `demo` seeded with recorded data so a
// new install has something to look at. Not workspace scoped: it adds or
// removes a whole workspace, through its own endpoint.
const DEMO_COUNT_KEYS = ['agents', 'projects', 'tasks', 'flows', 'teams', 'scenarios', 'views', 'runs'];

function DemoWorkspaceTab() {
  const { t } = useI18n();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await getDemo();
      setState(data || {});
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('settings.demo.loadFailed'));
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const toggle = async () => {
    const present = !!state?.present;
    if (present && !window.confirm(t('settings.demo.confirmRemove', { workspace: state?.workspace || 'demo' }))) return;
    setBusy(true);
    try {
      const { data } = await setDemo(!present);
      setState(data && typeof data === 'object' ? { ...state, ...data, present: data.present ?? !present } : { ...state, present: !present });
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('settings.demo.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const present = !!state?.present;
  const counts = state?.counts || {};
  const shown = DEMO_COUNT_KEYS.filter((k) => typeof counts[k] === 'number');

  return (
    <SectionCard
      title={t('settings.demo.title')}
      actions={state && (
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${present ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
          {present ? t('settings.demo.present') : t('settings.demo.absent')}
        </span>
      )}
    >
      <p className="text-sm text-gray-600">{t('settings.demo.intro')}</p>
      {state && state.enabled === false && (
        <p className="text-sm text-amber-700">{t('settings.demo.disabled')}</p>
      )}
      {present && shown.length > 0 && (
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {shown.map((k) => (
            <div key={k} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t(`settings.demo.counts.${k}`)}</dt>
              <dd className="text-lg font-semibold text-gray-800">{counts[k]}</dd>
            </div>
          ))}
        </dl>
      )}
      {error && (
        <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4" />{error}</p>
      )}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={toggle}
          disabled={busy || !state || state.enabled === false}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50 ${
            present
              ? 'border border-red-200 text-red-600 hover:bg-red-50'
              : 'bg-indigo-600 text-white hover:bg-indigo-700'
          }`}
        >
          {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : present ? <Trash2 className="w-4 h-4" /> : <Sparkles className="w-4 h-4" />}
          {present ? t('settings.demo.remove') : t('settings.demo.add')}
        </button>
        {present && state?.workspace && (
          <span className="text-xs text-gray-500">{t('settings.demo.workspaceName', { workspace: state.workspace })}</span>
        )}
      </div>
    </SectionCard>
  );
}

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
    return <PageLoader size="sm" />;
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

// ── API access tab ────────────────────────────────────────────────────────────
// The browser's own token (see src/api/index.js getApiToken). It lives in
// localStorage, not in a workspace override or the global .env: it belongs to
// this browser, not to a workspace, and it is only ever read by this frontend
// to attach an Authorization header to its own requests.

function ApiAccessTab() {
  const { t } = useI18n();
  const { mode } = useAuth();
  const [token, setToken] = useState(() => getApiToken());
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);

  const handleSave = () => {
    setApiToken(token);
    setSaved(true);
    setResult(null);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleClear = () => {
    setToken('');
    setApiToken('');
    setSaved(false);
    setResult(null);
  };

  const handleTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const resp = await fetch(`${API_ORIGIN}/api/health`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      setResult({ ok: resp.ok, status: resp.status });
    } catch (e) {
      setResult({ ok: false, status: null, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-xs text-gray-500">
        {t('settings.apiAccess.intro')}
      </div>
      {/* The posture the backend is actually running in. Read-only on purpose:
          the mode is a deployment decision, not a setting a signed-in user can
          flip out from under everybody else. */}
      <SectionCard title={t('settings.apiAccess.modeTitle')}>
        <p className="text-sm text-gray-700">
          <span className="font-semibold">{t(`settings.apiAccess.modes.${mode}.name`)}</span>
          {': '}
          {t(`settings.apiAccess.modes.${mode}.summary`)}
        </p>
        <p className="text-sm text-gray-600">{t('settings.apiAccess.modeExplainer')}</p>
        <p className="text-sm text-gray-600">
          {t('settings.apiAccess.modeWhereBefore')} <code className="bg-gray-100 rounded px-1">AUTH_MODE</code>{' '}
          {t('settings.apiAccess.modeWhereAfter')}
        </p>
      </SectionCard>
      {/* The browser's own copy of the shared token. Only `token` mode uses
          one: `single` needs no credential and `multi` issues a session. */}
      {mode === TOKEN && (
      <SectionCard title={t('settings.apiAccess.title')}>
        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.apiAccess.tokenLabel')}</label>
          <p className="text-xs text-gray-500 mb-1">{t('settings.apiAccess.tokenHint')}</p>
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)}
            placeholder={t('settings.apiAccess.tokenPlaceholder')} autoComplete="new-password"
            className={inputCls} />
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button type="button" onClick={handleSave}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium">
            <Save className="w-3.5 h-3.5" /> {t('common.save')}
          </button>
          <button type="button" onClick={handleClear}
            className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700">
            <Trash2 className="w-3.5 h-3.5" /> {t('settings.apiAccess.clear')}
          </button>
          <button type="button" onClick={handleTest} disabled={testing}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-gray-300 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50">
            {testing ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />} {t('settings.test')}
          </button>
          {saved && <span className="text-xs text-green-700">{t('settings.apiAccess.saved')}</span>}
          {result && (result.ok
            ? <span className="flex items-center gap-1 text-xs text-green-700"><CheckCircle className="w-3 h-3" /> {t('settings.apiAccess.ok')}</span>
            : <span className="flex items-center gap-1 text-xs text-red-700"><AlertCircle className="w-3 h-3" /> {result.status === 401 ? t('settings.apiAccess.unauthorized') : t('settings.apiAccess.testFailed')}</span>
          )}
        </div>
      </SectionCard>
      )}
      {/* multi mode issues a session instead of a browser token, so there is
          nothing to paste here for that case — except a personal API key
          (common/api_keys.py, docs/api-keys.md), which this same field
          happens to double as: getAuthToken() falls back to it whenever
          there is no session, so an `ahk_...` key pasted in as the "browser
          token" authenticates every request exactly the way a session would. */}
      {mode === MULTI && (
      <SectionCard title={t('settings.apiAccess.personalKeysTitle')}>
        <p className="text-sm text-gray-600">{t('settings.apiAccess.personalKeysHint')}</p>
      </SectionCard>
      )}
      <SectionCard title={t('settings.apiAccess.serverTitle')}>
        <p className="text-sm text-gray-600">
          {t('settings.apiAccess.serverHintBefore')} <code className="bg-gray-100 rounded px-1">AGENTS_HUB_API_TOKEN</code>{' '}
          {t('settings.apiAccess.serverHintAfter')} <code className="bg-gray-100 rounded px-1">.env</code>{t('settings.apiAccess.serverHintRestart')}
        </p>
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

  useEffect(() => {
    if (section && MOVED_TO_CONNECTORS.includes(section)) {
      navigate('/connectors', { replace: true });
    }
  }, [section, navigate]);

  const ws = useWorkspaceSettings(activeWorkspace);

  if (ws.loading) {
    return (
      <PageLoader />
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
            {t('settings.storedIn')} <code className="text-xs bg-gray-100 rounded px-1">{t('settings.settings2')}</code> {t('settings.insideThisWorkspaces')} <code className="text-xs bg-gray-100 rounded px-1">.agents_hub/workspaces.json</code>{t('settings.clearingAFieldMakesIt')}
          </p>
          <p className="mt-2">
            {t('settings.fieldsMarkedBefore')} <span className="inline-flex items-center gap-0.5 text-amber-600"><Lock className="w-3 h-3" /> {t('settings.fromEnv')}</span> {t('settings.fieldsMarkedAfter')}
          </p>
        </>}
        actions={active.workspaceScoped && <SaveWorkspaceSettingsButton s={ws} />}
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
            {/* Where the connector tabs went. Someone who has configured
                Telegram here before will come back here first, and a dead end
                is a worse answer than a pointer. */}
            <Link
              to="/connectors"
              className="mx-1 mt-2 block rounded-lg border border-dashed border-gray-200 px-3 py-2 text-[11px] leading-snug text-gray-500 hover:border-indigo-200 hover:text-indigo-600"
            >
              <Link2 className="mb-1 h-3.5 w-3.5" />
              {t('settings.connectorsMoved')}
            </Link>
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

            <WorkspaceSettingsStatus s={ws} />

            {active.id === 'providers' && <ProvidersSection s={ws} />}
            {active.id === 'local' && <LocalServersSection s={ws} />}
            {active.id === 'observability' && <ObservabilitySection s={ws} />}
            {active.id === 'rag' && <RagSection s={ws} />}
            {active.id === 'custom' && <CustomBackendsTab />}
            {active.id === 'demo' && <DemoWorkspaceTab />}
            {active.id === 'logging' && <LoggingSection s={ws} />}
            {active.id === 'apiAccess' && <ApiAccessTab />}
            {active.id === 'execution' && <ExecutionSection s={ws} showGlobal />}
            {active.id === 'webSearch' && <WebSearchSection s={ws} />}
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
