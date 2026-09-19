import { useState, useEffect, useCallback } from 'react';
import {
  Cpu, BarChart3, RefreshCw, Save, Plus, Trash2, Star, Loader,
  CheckCircle, AlertCircle, ChevronDown, ChevronRight, Search, X,
  Brain,
} from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import {
  getModelsCatalog, saveModelsCatalog, discoverProviderModels, getModelsUsage,
  getWorkspaceModel, updateWorkspaceDefaultModel,
} from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import DateInput from '../components/DateInput';
const BUILTIN_PROVIDERS = ['openai', 'anthropic', 'google', 'ollama', 'lmstudio'];

const PROVIDER_CONFIG = {
  openai:    { label: 'OpenAI',    color: 'text-green-700 bg-green-50 border-green-200' },
  anthropic: { label: 'Anthropic', color: 'text-orange-700 bg-orange-50 border-orange-200' },
  google:    { label: 'Google',    color: 'text-blue-700 bg-blue-50 border-blue-200' },
  ollama:    { label: 'Ollama',    color: 'text-purple-700 bg-purple-50 border-purple-200' },
  lmstudio:  { label: 'LM Studio', color: 'text-teal-700 bg-teal-50 border-teal-200' },
};

// Custom backends (any provider id not built-in) get a neutral indigo badge and
// their id as the label, so they render without a hardcoded config entry.
const providerConfig = (p) =>
  PROVIDER_CONFIG[p] || { label: p, color: 'text-indigo-700 bg-indigo-50 border-indigo-200' };

// Providers to render, in order: built-ins first, then custom backends (sorted),
// derived from whatever the catalog actually contains.
const orderProviders = (catalog) => {
  const keys = Object.keys(catalog || {});
  const builtins = BUILTIN_PROVIDERS.filter((p) => keys.includes(p));
  const custom = keys.filter((p) => !BUILTIN_PROVIDERS.includes(p)).sort();
  return [...builtins, ...custom];
};

const inputCls = "border border-gray-300 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none";

// Providers with more than this many models start collapsed on first load, so a
// long list (e.g. OpenAI) doesn't dominate the page. Per-provider collapse state
// is then remembered across reloads in localStorage.
const AUTO_COLLAPSE_OVER = 8;
const COLLAPSED_STORAGE_KEY = 'agents_hub_models_collapsed';

const fmtInt = (n) => (n || 0).toLocaleString();
const fmtTokens = (n) => {
  n = n || 0;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
};
const fmtCost = (n) => '$' + (n || 0).toFixed(2);

// ── Catalog tab ───────────────────────────────────────────────────────────────

function CatalogTab() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [catalog, setCatalog] = useState(null);
  // Read-only global default (provider + model) sourced from .env.
  const [globalDefault, setGlobalDefault] = useState({ provider: '', model: '' });
  // Per-workspace default model (model_override on the active workspace). '' provider = inherit global.
  const [wsModel, setWsModel] = useState(null);     // raw GET /model response
  const [wsForm, setWsForm] = useState({ provider: '', model: '' });
  const [wsSaving, setWsSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discovering, setDiscovering] = useState({});
  const [probeResult, setProbeResult] = useState({});
  const [newModel, setNewModel] = useState({});
  // Per-provider UI state for taming long lists
  const [collapsed, setCollapsed] = useState(() => {   // provider -> bool (collapsed)
    try { return JSON.parse(localStorage.getItem(COLLAPSED_STORAGE_KEY)) || {}; }
    catch { return {}; }
  });
  const [filter, setFilter] = useState({});            // provider -> search text
  const [onlyEnabled, setOnlyEnabled] = useState({});  // provider -> show enabled only

  // Remember collapse choices across reloads.
  useEffect(() => {
    try { localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify(collapsed)); } catch { /* storage unavailable */ }
  }, [collapsed]);

  // Load the active workspace's default model whenever the workspace changes.
  const loadWsModel = useCallback(() => {
    if (!selectedWorkspace) return;
    getWorkspaceModel(selectedWorkspace)
      .then(({ data }) => {
        setWsModel(data);
        const wd = data.workspace_default || {};
        const p = wd.provider || '';
        setWsForm({ provider: p, model: p ? (wd.model || '') : '' });
      })
      .catch(() => { setWsModel(null); setWsForm({ provider: '', model: '' }); });
  }, [selectedWorkspace]);

  useEffect(() => { loadWsModel(); }, [loadWsModel]);

  const saveWsModel = () => {
    if (!selectedWorkspace) return;
    setWsSaving(true);
    const payload = wsForm.provider ? { provider: wsForm.provider, model: wsForm.model } : { provider: '', model: '' };
    updateWorkspaceDefaultModel(selectedWorkspace, payload)
      .then(() => loadWsModel())
      .catch((e) => console.error('Error saving workspace default model:', e))
      .finally(() => setWsSaving(false));
  };

  const load = useCallback(() => {
    getModelsCatalog()
      .then(({ data }) => {
        const providers = data.providers || {};
        setCatalog(providers);
        setGlobalDefault(data.global_default || { provider: '', model: '' });
        setDirty(false);
        // First time we see a provider, default long lists to collapsed. Providers
        // already remembered in localStorage keep the user's last choice.
        setCollapsed((prev) => {
          const next = { ...prev };
          let changed = false;
          for (const p of Object.keys(providers)) {
            if (!(p in next)) {
              next[p] = (providers[p]?.models || []).length > AUTO_COLLAPSE_OVER;
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      })
      .catch((e) => console.error('Error loading catalog:', e));
  }, []);

  useEffect(() => { load(); }, [load]);

  const updateModel = (provider, id, patch) => {
    setCatalog((c) => {
      const models = c[provider].models.map((m) => (m.id === id ? { ...m, ...patch } : m));
      return { ...c, [provider]: { ...c[provider], models } };
    });
    setDirty(true);
  };

  const removeModel = (provider, id) => {
    setCatalog((c) => {
      const models = c[provider].models.filter((m) => m.id !== id);
      const def = c[provider].default === id ? '' : c[provider].default;
      return { ...c, [provider]: { default: def, models } };
    });
    setDirty(true);
  };

  // Enable/disable every model currently visible under a provider's filter
  const setAllVisible = (provider, ids, enabled) => {
    const idSet = new Set(ids);
    setCatalog((c) => ({
      ...c,
      [provider]: {
        ...c[provider],
        models: c[provider].models.map((m) => (idSet.has(m.id) ? { ...m, enabled } : m)),
      },
    }));
    setDirty(true);
  };

  const setDefault = (provider, id) => {
    setCatalog((c) => ({ ...c, [provider]: { ...c[provider], default: c[provider].default === id ? '' : id } }));
    setDirty(true);
  };

  const addModel = (provider) => {
    const id = (newModel[provider] || '').trim();
    if (!id) return;
    if (catalog[provider].models.some((m) => m.id === id)) return;
    setCatalog((c) => ({
      ...c,
      [provider]: {
        ...c[provider],
        models: [...c[provider].models, { id, enabled: true, input_price: 0, cached_input_price: 0, output_price: 0, context_window: 0, price_source: 'auto' }],
      },
    }));
    setNewModel((s) => ({ ...s, [provider]: '' }));
    setDirty(true);
  };

  const discover = (provider) => {
    setDiscovering((s) => ({ ...s, [provider]: true }));
    setProbeResult((s) => ({ ...s, [provider]: null }));
    discoverProviderModels(provider)
      .then(({ data }) => {
        setProbeResult((s) => ({ ...s, [provider]: data }));
        if (data.ok) load(); // server merged + persisted new models
      })
      .catch((e) => setProbeResult((s) => ({ ...s, [provider]: { ok: false, error: e.message } })))
      .finally(() => setDiscovering((s) => ({ ...s, [provider]: false })));
  };

  const save = () => {
    setSaving(true);
    saveModelsCatalog(catalog)
      .then(({ data }) => {
        setCatalog(data.providers || {});
        setGlobalDefault(data.global_default || globalDefault);
        setDirty(false);
      })
      .catch((e) => console.error('Error saving catalog:', e))
      .finally(() => setSaving(false));
  };

  if (!catalog) {
    return <div className="flex items-center gap-2 text-gray-500 p-8"><Loader className="w-4 h-4 animate-spin" /> {t('models.loadingCatalog')}</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-gray-500">
          Curate which models are available per provider, star each provider's default model, and configure pricing (USD per 1M tokens) used to estimate cost.
          The catalog and pricing are global; the model a workspace actually uses is set per workspace below, falling back to the global default.
          Common cloud models are pre-priced with indicative list prices — verify and adjust to match your provider.
        </p>
        <button
          onClick={save}
          disabled={!dirty || saving}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors shrink-0 ${
            dirty ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          }`}
        >
          {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {dirty ? t('models.saveChanges') : t('common.saved')}
        </button>
      </div>

      {/* Per-workspace default model (the model this workspace actually uses) */}
      {(() => {
        const enabledProviders = orderProviders(catalog).filter((p) => (catalog[p]?.models || []).some((m) => m.enabled));
        const wsEnabledModels = wsForm.provider ? (catalog[wsForm.provider]?.models || []).filter((m) => m.enabled) : [];
        const gd = wsModel?.global_default || {};
        const wd = wsModel?.workspace_default || {};
        const savedProvider = wd.provider || '';
        const wsDirty = (wsForm.provider || '') !== savedProvider
          || (wsForm.provider && (wsForm.model || '') !== (wd.model || ''));
        const inheriting = !wsForm.provider;
        return (
          <div className="bg-indigo-50/40 border border-indigo-200 rounded-xl px-4 py-3">
            <div className="flex items-center flex-wrap gap-3">
              <span className="text-sm font-semibold text-gray-800">
                Model for workspace <span className="text-indigo-600">{selectedWorkspace || '—'}</span>
              </span>
              <select
                value={wsForm.provider}
                onChange={(e) => {
                  const p = e.target.value;
                  const enabled = p ? (catalog[p]?.models || []).filter((m) => m.enabled) : [];
                  const def = catalog[p]?.default;
                  const pick = enabled.find((m) => m.id === def)?.id || enabled[0]?.id || '';
                  setWsForm({ provider: p, model: p ? pick : '' });
                }}
                className={`${inputCls} py-1.5`}
              >
                <option value="">{t('models.inheritGlobalDefault')}</option>
                {enabledProviders.map((p) => (
                  <option key={p} value={p}>{providerConfig(p).label}</option>
                ))}
              </select>
              {!inheriting && (
                <select
                  value={wsForm.model}
                  onChange={(e) => setWsForm((f) => ({ ...f, model: e.target.value }))}
                  className={`${inputCls} py-1.5 max-w-xs`}
                >
                  {wsEnabledModels.length === 0 && <option value="">{t('models.noEnabledModels')}</option>}
                  {wsEnabledModels.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
                </select>
              )}
              <button
                onClick={saveWsModel}
                disabled={!wsDirty || wsSaving || (!inheriting && !wsForm.model)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                  wsDirty && (inheriting || wsForm.model) ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                }`}
              >
                {wsSaving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Apply
              </button>
            </div>
            <p className="text-xs text-gray-500 mt-2">
              {inheriting
                ? <>{t('models.inheritingGlobal')} <span className="font-medium text-gray-600">{providerConfig(gd.provider).label || gd.provider || '—'}{gd.model ? ` · ${gd.model}` : ''}</span>{t('models.pickAModelToSet')}</>
                : <>{t('models.workspaceDefaultIs')} <span className="font-medium text-gray-600">{providerConfig(wsForm.provider).label} · {wsForm.model || '—'}</span>{t('models.itAppearsAsWorkspaceDefault')}</>}
            </p>
          </div>
        );
      })()}

      {/* Global default — read-only, sourced from .env (the inherit fallback) */}
      <div className="bg-white border border-gray-200 rounded-xl px-4 py-3 flex items-center flex-wrap gap-3">
        <span className="text-sm font-medium text-gray-700">{t('models.globalDefault')}</span>
        <span className="px-2 py-1 rounded-lg border bg-gray-50 border-gray-200 text-sm flex items-center gap-1.5">
          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-medium ${providerConfig(globalDefault.provider).color}`}>
            {providerConfig(globalDefault.provider).label || globalDefault.provider || '—'}
          </span>
          <span className="font-mono text-gray-700">{globalDefault.model || '—'}</span>
          <span className="text-[10px] uppercase tracking-wider text-gray-400 ml-1">{t('models.global')}</span>
        </span>
        <span className="text-xs text-gray-400">
          {t('models.fromEnvBefore')} <code className="bg-gray-100 rounded px-1">.env</code> {t('models.fromEnvAfter')}
        </span>
      </div>

      {orderProviders(catalog).map((provider) => {
        const entry = catalog[provider] || { default: '', models: [] };
        const cfg = providerConfig(provider);
        const probe = probeResult[provider];
        const isCollapsed = !!collapsed[provider];
        const q = (filter[provider] || '').trim().toLowerCase();
        const enabledOnly = !!onlyEnabled[provider];
        const enabledCount = entry.models.filter((m) => m.enabled).length;
        const visible = entry.models.filter((m) =>
          (!enabledOnly || m.enabled) && (!q || m.id.toLowerCase().includes(q))
        );
        const visibleIds = visible.map((m) => m.id);
        const Chevron = isCollapsed ? ChevronRight : ChevronDown;
        return (
          <div key={provider} className="bg-white border border-gray-200 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-gray-50">
              <button
                onClick={() => setCollapsed((s) => ({ ...s, [provider]: !s[provider] }))}
                className="flex items-center gap-2 min-w-0 text-left"
              >
                <Chevron className="w-4 h-4 text-gray-400 shrink-0" />
                <span className={`px-2 py-0.5 rounded border text-xs font-semibold ${cfg.color}`}>{cfg.label}</span>
                <span className="text-xs text-gray-400 shrink-0">{t('models.enabledOfTotal', { enabled: enabledCount, total: entry.models.length })}</span>
                {entry.default && (
                  <span className="text-xs text-gray-400 truncate hidden sm:inline">{t('models.default')} <span className="font-mono text-gray-500">{entry.default}</span></span>
                )}
              </button>
              <div className="flex items-center gap-2 shrink-0">
                {probe && (
                  <span className={`text-xs flex items-center gap-1 ${probe.ok ? 'text-green-600' : 'text-red-500'}`}>
                    {probe.ok
                      ? <><CheckCircle className="w-3.5 h-3.5" /> {
                          [probe.added > 0 ? `+${probe.added} new` : null,
                           probe.priced > 0 ? `${probe.priced} priced` : null,
                           probe.repriced > 0 ? `${probe.repriced} repriced` : null,
                           probe.kept_manual > 0 ? `${probe.kept_manual} manual kept` : null]
                            .filter(Boolean).join(', ') || 'up to date'
                        }</>
                      : <><AlertCircle className="w-3.5 h-3.5" /> {probe.error}</>}
                  </span>
                )}
                <button
                  onClick={() => discover(provider)}
                  disabled={discovering[provider]}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors"
                >
                  {discovering[provider] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  Discover
                </button>
              </div>
            </div>

            {!isCollapsed && (
              <div className="p-3">
                {entry.models.length === 0 ? (
                  <p className="text-sm text-gray-400 px-1 py-2">{t('models.noModelsUseDiscoverTo')}</p>
                ) : (
                  <>
                    {/* Toolbar: search + filters + bulk actions (shown once the list is non-trivial) */}
                    {entry.models.length > 5 && (
                      <div className="flex items-center flex-wrap gap-2 mb-2 px-1">
                        <div className="relative flex-1 min-w-44 max-w-xs">
                          <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                          <input
                            type="text"
                            placeholder={t('models.filterModels')}
                            value={filter[provider] || ''}
                            onChange={(e) => setFilter((s) => ({ ...s, [provider]: e.target.value }))}
                            className={`${inputCls} w-full pl-8 pr-7`}
                          />
                          {q && (
                            <button
                              onClick={() => setFilter((s) => ({ ...s, [provider]: '' }))}
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                        <label className="flex items-center gap-1.5 text-xs text-gray-500">
                          <input
                            type="checkbox"
                            checked={enabledOnly}
                            onChange={(e) => setOnlyEnabled((s) => ({ ...s, [provider]: e.target.checked }))}
                            className="w-3.5 h-3.5 accent-indigo-600"
                          />
                          {t('models.enabledOnly')}
                        </label>
                        <span className="text-xs text-gray-300">|</span>
                        <button onClick={() => setAllVisible(provider, visibleIds, true)} className="text-xs text-indigo-600 hover:underline">{q || enabledOnly ? t('models.enableShown') : t('models.enableAll')}</button>
                        <button onClick={() => setAllVisible(provider, visibleIds, false)} className="text-xs text-gray-500 hover:underline">{q || enabledOnly ? t('models.disableShown') : t('models.disableAll')}</button>
                        <span className="text-xs text-gray-400 ml-auto">{t('models.visibleOfTotal', { visible: visible.length, total: entry.models.length })}</span>
                      </div>
                    )}

                    <div className="max-h-80 overflow-y-auto border border-gray-100 rounded-lg">
                      <table className="w-full text-sm">
                        <thead className="sticky top-0 bg-white z-10">
                          <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100">
                            <th className="px-2 py-1.5 font-medium w-10">{t('models.on')}</th>
                            <th className="px-2 py-1.5 font-medium">{t('models.model')}</th>
                            <th className="px-2 py-1.5 font-medium w-12">{t('models.default2')}</th>
                            <th className="px-2 py-1.5 font-medium w-28">{t('models.input1m')}</th>
                            <th className="px-2 py-1.5 font-medium w-28" title={t('models.cached1mHint')}>{t('models.cached1m')}</th>
                            <th className="px-2 py-1.5 font-medium w-28">{t('models.output1m')}</th>
                            <th className="px-2 py-1.5 font-medium w-28" title={t('models.contextWindowMaxInputTokens')}>{t('models.context')}</th>
                            <th className="px-2 py-1.5 font-medium w-10"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {visible.length === 0 ? (
                            <tr><td colSpan={8} className="px-2 py-4 text-center text-gray-400 text-xs">{t('models.noModelsMatch', { query: q })}</td></tr>
                          ) : visible.map((m) => (
                            <tr key={m.id} className="border-t border-gray-50 hover:bg-gray-50">
                              <td className="px-2 py-1.5">
                                <input
                                  type="checkbox"
                                  checked={m.enabled}
                                  onChange={(e) => updateModel(provider, m.id, { enabled: e.target.checked })}
                                  className="w-4 h-4 accent-indigo-600"
                                />
                              </td>
                              <td className="px-2 py-1.5 font-mono text-gray-700">
                                {m.id}
                                {provider === globalDefault.provider && m.id === globalDefault.model && (
                                  <span className="ml-2 align-middle px-1.5 py-0.5 rounded border border-gray-300 bg-gray-50 text-gray-500 text-[10px] font-sans uppercase tracking-wider" title={t('models.systemWideDefaultFromEnv')}>{t('models.global')}</span>
                                )}
                              </td>
                              <td className="px-2 py-1.5">
                                <button onClick={() => setDefault(provider, m.id)} title={t('models.setAsProviderDefault')}>
                                  <Star className={`w-4 h-4 ${entry.default === m.id ? 'fill-amber-400 text-amber-400' : 'text-gray-300 hover:text-amber-400'}`} />
                                </button>
                              </td>
                              <td className="px-2 py-1.5">
                                <div className="flex items-center gap-1">
                                  <input
                                    type="number" min="0" step="0.01" value={m.input_price}
                                    onChange={(e) => updateModel(provider, m.id, { input_price: parseFloat(e.target.value) || 0 })}
                                    className={`${inputCls} w-24`}
                                  />
                                  {m.price_source === 'manual' && (
                                    <span className="text-[10px] text-amber-600 font-medium" title={t('models.manualPriceHint')}>
                                      {t('models.manualPrice')}
                                    </span>
                                  )}
                                </div>
                              </td>
                              <td className="px-2 py-1.5">
                                {/* Cache reads are billed apart from fresh input, and an
                                    agent loop re-sends its prompt every step, so this is
                                    most of a long run's input bill. */}
                                <input
                                  type="number" min="0" step="0.01" value={m.cached_input_price ?? 0}
                                  onChange={(e) => updateModel(provider, m.id, { cached_input_price: parseFloat(e.target.value) || 0 })}
                                  className={`${inputCls} w-24`}
                                  title={t('models.cached1mHint')}
                                />
                              </td>
                              <td className="px-2 py-1.5">
                                <input
                                  type="number" min="0" step="0.01" value={m.output_price}
                                  onChange={(e) => updateModel(provider, m.id, { output_price: parseFloat(e.target.value) || 0 })}
                                  className={`${inputCls} w-24`}
                                />
                              </td>
                              <td className="px-2 py-1.5">
                                <input
                                  type="number" min="0" step="1000" value={m.context_window || 0}
                                  onChange={(e) => updateModel(provider, m.id, { context_window: parseInt(e.target.value, 10) || 0 })}
                                  className={`${inputCls} w-24`}
                                  title={t('models.contextWindowInTokensMax')}
                                />
                              </td>
                              <td className="px-2 py-1.5">
                                <button onClick={() => removeModel(provider, m.id)} className="text-gray-300 hover:text-red-500">
                                  <Trash2 className="w-4 h-4" />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}

                <div className="flex items-center gap-2 mt-3 px-1">
                  <input
                    type="text"
                    placeholder={t('models.addModelIdManually')}
                    value={newModel[provider] || ''}
                    onChange={(e) => setNewModel((s) => ({ ...s, [provider]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === 'Enter') addModel(provider); }}
                    className={`${inputCls} flex-1 max-w-xs`}
                  />
                  <button
                    onClick={() => addModel(provider)}
                    className="flex items-center gap-1 px-3 py-1 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium"
                  >
                    <Plus className="w-3.5 h-3.5" /> {t('models.add')}
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Usage tab ─────────────────────────────────────────────────────────────────

function UsageTab() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);   // the first load starts immediately
  const [scopeWorkspace, setScopeWorkspace] = useState(true);
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const load = useCallback(() => {
    const params = {};
    if (scopeWorkspace && selectedWorkspace && selectedWorkspace !== 'default') params.workspace = selectedWorkspace;
    if (since) params.since = since;
    if (until) params.until = until + 'T23:59:59';
    getModelsUsage(params)
      .then(({ data }) => setData(data))
      .catch((e) => console.error('Error loading usage:', e))
      .finally(() => setLoading(false));
  }, [scopeWorkspace, selectedWorkspace, since, until]);

  useEffect(() => { load(); }, [load]);

  const rows = data?.rows || [];
  const totals = data?.totals || { runs: 0, inbound_tokens: 0, cached_tokens: 0, outbound_tokens: 0, total_tokens: 0, cost: 0 };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4 bg-white border border-gray-200 rounded-xl p-4">
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox" checked={scopeWorkspace} onChange={(e) => setScopeWorkspace(e.target.checked)} className="w-4 h-4 accent-indigo-600" />
          Limit to current workspace {selectedWorkspace && selectedWorkspace !== 'default' ? `(${selectedWorkspace})` : '(all)'}
        </label>
        <div className="flex flex-col">
          <span className="text-xs text-gray-400 mb-1">{t('models.from')}</span>
          <DateInput value={since} onChange={setSince} className={inputCls} />
        </div>
        <div className="flex flex-col">
          <span className="text-xs text-gray-400 mb-1">{t('models.to')}</span>
          <DateInput value={until} onChange={setUntil} className={inputCls} />
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-sm font-medium"
        >
          {loading ? <Loader className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: t('models.totals.runs'), value: fmtInt(totals.runs) },
          { label: t('models.totals.inputTokens'), value: fmtTokens(totals.inbound_tokens) },
          { label: t('models.totals.outputTokens'), value: fmtTokens(totals.outbound_tokens) },
          { label: t('models.totals.estCost'), value: fmtCost(totals.cost) },
        ].map((c) => (
          <div key={c.label} className="bg-white border border-gray-200 rounded-xl p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wider">{c.label}</p>
            <p className="text-2xl font-bold text-gray-800 mt-1">{c.value}</p>
          </div>
        ))}
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-400 uppercase tracking-wider bg-gray-50">
              <th className="px-4 py-2 font-medium">{t('models.provider')}</th>
              <th className="px-4 py-2 font-medium">{t('models.model')}</th>
              <th className="px-4 py-2 font-medium text-right">{t('models.runs')}</th>
              <th className="px-4 py-2 font-medium text-right">{t('models.input')}</th>
              <th className="px-4 py-2 font-medium text-right" title={t('models.cachedHint')}>{t('models.cached')}</th>
              <th className="px-4 py-2 font-medium text-right">{t('models.output')}</th>
              <th className="px-4 py-2 font-medium text-right">{t('models.total')}</th>
              <th className="px-4 py-2 font-medium text-right">{t('models.avgMs')}</th>
              <th className="px-4 py-2 font-medium text-right">{t('models.estCost')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-gray-400">{t('models.noUsageRecordedForThis')}</td></tr>
            ) : rows.map((r) => {
              const cfg = providerConfig(r.provider);
              return (
                <tr key={`${r.provider}:${r.model}`} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded border text-xs font-medium ${cfg.color}`}>{cfg.label}</span>
                  </td>
                  <td className="px-4 py-2 font-mono text-gray-700">{r.model}</td>
                  <td className="px-4 py-2 text-right">{fmtInt(r.runs)}</td>
                  <td className="px-4 py-2 text-right">{fmtTokens(r.inbound_tokens)}</td>
                  <td className="px-4 py-2 text-right text-gray-500" title={t('models.cachedHint')}>
                    {r.cached_tokens > 0 ? fmtTokens(r.cached_tokens) : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-2 text-right">{fmtTokens(r.outbound_tokens)}</td>
                  <td className="px-4 py-2 text-right font-medium">{fmtTokens(r.total_tokens)}</td>
                  <td className="px-4 py-2 text-right text-gray-500">{fmtInt(r.avg_duration_ms)}</td>
                  <td className="px-4 py-2 text-right">{r.cost > 0 ? fmtCost(r.cost) : <span className="text-gray-300">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
          {rows.length > 0 && (
            <tfoot>
              <tr className="border-t-2 border-gray-200 font-semibold text-gray-700 bg-gray-50">
                <td className="px-4 py-2" colSpan={2}>{t('models.total')}</td>
                <td className="px-4 py-2 text-right">{fmtInt(totals.runs)}</td>
                <td className="px-4 py-2 text-right">{fmtTokens(totals.inbound_tokens)}</td>
                <td className="px-4 py-2 text-right">{fmtTokens(totals.cached_tokens)}</td>
                <td className="px-4 py-2 text-right">{fmtTokens(totals.outbound_tokens)}</td>
                <td className="px-4 py-2 text-right">{fmtTokens(totals.total_tokens)}</td>
                <td className="px-4 py-2"></td>
                <td className="px-4 py-2 text-right">{fmtCost(totals.cost)}</td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <p className="text-xs text-gray-400">
        {t('models.usageFootnote')}
      </p>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'catalog', label: 'Catalog', icon: Cpu },
  { id: 'usage', label: 'Usage', icon: BarChart3 },
];

export default function Models() {
  const { t } = useI18n();
  const [tab, setTab] = useState('catalog');
  return (
    <PageContainer>
      <PageHeader
        icon={Brain}
        title={t('models.models')}
        description={t('models.theCatalogOfProvidersAnd')}
      />
      <div className="flex items-center gap-1 border-b border-gray-200 mb-4">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                active ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <Icon className="w-4 h-4" /> {t.label}
            </button>
          );
        })}
      </div>
      {tab === 'catalog' ? <CatalogTab /> : <UsageTab />}
    </PageContainer>
  );
}
