import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronUp, Factory, GitBranch, Loader2, Plus, RefreshCw, Sparkles, Trash2, X } from 'lucide-react';
import { createFlow, deleteFlow, generateFlow, listFlows, testLocalModel } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

const PROVIDERS = [
  { value: '', label: 'Inherit global settings' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
  { value: 'ollama', label: 'Ollama (local)' },
  { value: 'lmstudio', label: 'LM Studio (local)' },
];

const CLOUD_MODELS = {
  openai: [
    'gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-4',
    'o1', 'o1-mini', 'o3', 'o3-mini', 'o4-mini',
  ],
  anthropic: [
    'claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001',
    'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229',
  ],
  google: [
    'gemini-2.5-pro', 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash',
  ],
};

const DEFAULT_BASE_URLS = {
  ollama: 'http://localhost:11434',
  lmstudio: 'http://localhost:1234',
};

function formatDate(value) {
  if (!value) return 'No activity yet';
  return new Date(value).toLocaleString();
}

const AgentFlows = () => {
  const navigate = useNavigate();
  const { selectedWorkspace, workspaceFilter } = useWorkspace();
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newFlow, setNewFlow] = useState({ name: '', description: '' });

  // AI wizard state
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardReq, setWizardReq] = useState('');
  const [wizardLoading, setWizardLoading] = useState(false);
  const [wizardResult, setWizardResult] = useState(null);
  const [wizardError, setWizardError] = useState('');
  const [applyingFlow, setApplyingFlow] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [wizardProvider, setWizardProvider] = useState('');
  const [wizardModel, setWizardModel] = useState('');
  const [wizardBaseUrl, setWizardBaseUrl] = useState('');
  const [availableModels, setAvailableModels] = useState([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchModelsError, setFetchModelsError] = useState('');

  const loadFlows = async () => {
    setLoading(true);
    try {
      const response = await listFlows(workspaceFilter);
      setFlows(response.data || []);
    } catch (error) {
      console.error('Failed to load flows', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFlows();
  }, [selectedWorkspace]);

  const visibleFlows = useMemo(() => flows, [flows]);

  const handleCreate = async (event) => {
    event.preventDefault();
    if (!newFlow.name.trim()) return;

    setCreating(true);
    try {
      const response = await createFlow({
        name: newFlow.name.trim(),
        description: newFlow.description.trim(),
      });
      navigate(`/flows/${response.data.id}`);
    } catch (error) {
      alert(`Failed to create flow: ${error.response?.data?.detail || error.message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (flowId) => {
    if (!confirm('Delete this flow?')) return;
    try {
      await deleteFlow(flowId);
      await loadFlows();
    } catch (error) {
      alert(`Failed to delete flow: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleWizardGenerate = async () => {
    if (!wizardReq.trim()) return;
    setWizardLoading(true);
    setWizardResult(null);
    setWizardError('');
    try {
      const response = await generateFlow({
        requirement: wizardReq.trim(),
        workspace: workspaceFilter || undefined,
        provider: wizardProvider || undefined,
        model: wizardModel.trim() || undefined,
        base_url: wizardBaseUrl.trim() || undefined,
      });
      setWizardResult(response.data);
    } catch (error) {
      setWizardError(error.response?.data?.detail || error.message || 'Generation failed');
    } finally {
      setWizardLoading(false);
    }
  };

  const handleApplyGeneratedFlow = async () => {
    if (!wizardResult || wizardResult.type !== 'flow') return;
    setApplyingFlow(true);
    try {
      const response = await createFlow({
        name: wizardResult.name || 'AI Generated Flow',
        description: wizardResult.description || '',
      });
      const flowId = response.data.id;

      // Build ReactFlow-compatible nodes and edges with positions
      const nodeCount = wizardResult.nodes?.length || 0;
      const nodes = (wizardResult.nodes || []).map((n, i) => ({
        id: n.id,
        agent_id: n.agent_id,
        label: n.label || n.agent_id,
        description: n.description || '',
        position: { x: 120 + i * 220, y: 160 },
        data: { label: n.label || n.agent_id, agent_id: n.agent_id, description: n.description || '' },
      }));
      const edges = (wizardResult.edges || []).map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
      }));

      const { updateFlow } = await import('../api');
      await updateFlow(flowId, { nodes, edges });

      setWizardOpen(false);
      setWizardResult(null);
      setWizardReq('');
      navigate(`/flows/${flowId}`);
    } catch (error) {
      alert(`Failed to create flow: ${error.response?.data?.detail || error.message}`);
    } finally {
      setApplyingFlow(false);
    }
  };

  const handleFetchModels = async () => {
    setFetchingModels(true);
    setFetchModelsError('');
    try {
      const url = wizardBaseUrl.trim() || DEFAULT_BASE_URLS[wizardProvider] || '';
      const response = await testLocalModel(wizardProvider, url);
      if (response.data.ok) {
        const models = response.data.models || [];
        setAvailableModels(models);
        if (models.length > 0 && !models.includes(wizardModel)) {
          setWizardModel(models[0]);
        }
      } else {
        setFetchModelsError(response.data.error || 'Could not connect to server');
      }
    } catch (e) {
      setFetchModelsError(e.response?.data?.detail || e.message || 'Failed to fetch models');
    } finally {
      setFetchingModels(false);
    }
  };

  const handleProviderChange = (provider) => {
    setWizardProvider(provider);
    setAvailableModels([]);
    setFetchModelsError('');
    setWizardBaseUrl('');
    const cloudModels = CLOUD_MODELS[provider];
    setWizardModel(cloudModels ? cloudModels[0] : '');
  };

  const closeWizard = () => {
    setWizardOpen(false);
    setWizardResult(null);
    setWizardError('');
    setWizardReq('');
    setModelOpen(false);
    setWizardProvider('');
    setWizardModel('');
    setWizardBaseUrl('');
    setAvailableModels([]);
    setFetchModelsError('');
  };

  return (
    <div className="space-y-8">
      <section className="rounded-[28px] border border-slate-200 bg-[radial-gradient(circle_at_top_left,_rgba(14,116,144,0.16),_transparent_32%),linear-gradient(135deg,#f8fafc_0%,#ecfeff_45%,#fefce8_100%)] p-8 shadow-sm">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl space-y-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-200 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.24em] text-cyan-700">
              <Factory className="h-3.5 w-3.5" />
              Agent Flows
            </div>
            <h1 className="text-4xl font-black tracking-tight text-slate-900">Agents as visual flows</h1>
            <p className="max-w-xl text-sm leading-6 text-slate-600">
              Build reusable delivery pipelines, attach a shared task context, and run either the whole flow or one agent at a time.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
              <div className="text-xs uppercase tracking-[0.2em] text-slate-400">Visible</div>
              <div className="mt-2 text-3xl font-black text-slate-900">{visibleFlows.length}</div>
            </div>
            <div className="rounded-2xl border border-white/70 bg-white/80 p-4 shadow-sm backdrop-blur">
              <div className="text-xs uppercase tracking-[0.2em] text-slate-400">Workspace</div>
              <div className="mt-2 text-sm font-bold text-slate-900">{selectedWorkspace || 'All'}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <form onSubmit={handleCreate} className="space-y-5 rounded-[24px] border border-slate-200 bg-white p-6 shadow-sm">
            <div className="space-y-2">
              <h2 className="text-lg font-bold text-slate-900">New flow</h2>
              <p className="text-sm text-slate-500">Start with a blank canvas, then drag agents into the flow.</p>
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Name</label>
              <input
                value={newFlow.name}
                onChange={(event) => setNewFlow((current) => ({ ...current, name: event.target.value }))}
                placeholder="Customer onboarding pipeline"
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Description</label>
              <textarea
                value={newFlow.description}
                onChange={(event) => setNewFlow((current) => ({ ...current, description: event.target.value }))}
                rows={4}
                placeholder="What this flow is responsible for."
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
              />
            </div>
            <button
              type="submit"
              disabled={creating}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
            >
              <Plus className="h-4 w-4" />
              {creating ? 'Creating...' : 'Create flow'}
            </button>
          </form>

          {/* AI Wizard button */}
          <button
            onClick={() => setWizardOpen(true)}
            className="flex w-full items-center justify-center gap-2 rounded-[24px] border border-violet-200 bg-gradient-to-br from-violet-50 to-fuchsia-50 px-4 py-4 text-sm font-semibold text-violet-700 shadow-sm transition hover:border-violet-400 hover:from-violet-100 hover:to-fuchsia-100"
          >
            <Sparkles className="h-4 w-4" />
            Generate flow with AI
          </button>
        </div>

        <div className="p-2">
          <div className="mb-5 flex items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Flows</h2>
              <p className="text-sm text-slate-500">Flows are scoped to the current workspace when assigned.</p>
            </div>
          </div>

          {loading ? (
            <div className="flex min-h-[280px] items-center justify-center rounded-3xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
              Loading flows...
            </div>
          ) : visibleFlows.length === 0 ? (
            <div className="flex min-h-[280px] flex-col items-center justify-center gap-3 rounded-3xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center">
              <GitBranch className="h-9 w-9 text-slate-300" />
              <div className="space-y-1">
                <div className="text-sm font-semibold text-slate-900">No flows yet</div>
                <div className="text-sm text-slate-500">Create one on the left, then open it to design the flow.</div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
              {visibleFlows.map((flow) => (
                <div key={flow.id} className="rounded-lg border border-gray-100 bg-white p-4 shadow-sm transition-shadow hover:shadow-md">
                  <div className="mb-3 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <Link
                          to={`/flows/${flow.id}`}
                          className="block truncate text-sm font-semibold text-gray-900 hover:text-cyan-600"
                        >
                          {flow.name}
                        </Link>
                        {flow.running && (
                          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-cyan-500" title="Running" />
                        )}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[11px] text-gray-500">
                        {flow.description || 'No description provided.'}
                      </div>
                    </div>
                    <span className="shrink-0 rounded bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-gray-600">
                      {flow.workspace || 'Shared'}
                    </span>
                  </div>

                  <div className="mb-3 grid grid-cols-2 gap-2">
                    <div className="rounded-md border border-blue-100 bg-blue-50 px-2 py-1.5 text-center">
                      <div className="text-[9px] font-semibold uppercase tracking-wide text-blue-500">Nodes</div>
                      <div className="text-xs font-semibold text-blue-900">{flow.nodes?.length || 0}</div>
                    </div>
                    <div className="rounded-md border border-amber-100 bg-amber-50 px-2 py-1.5 text-center">
                      <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">Updated</div>
                      <div className="truncate text-xs font-semibold text-amber-900">{formatDate(flow.updated_at)}</div>
                    </div>
                  </div>

                  <div className="flex justify-end">
                    <button
                      onClick={() => handleDelete(flow.id)}
                      className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
                      title="Delete flow"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* AI Wizard Modal */}
      {wizardOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-2xl rounded-[28px] border border-violet-200 bg-white shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-violet-600" />
                <h2 className="text-lg font-bold text-slate-900">Generate flow with AI</h2>
              </div>
              <button
                onClick={closeWizard}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-5 p-6">
              {/* Requirement input */}
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                  Describe the functionality you need
                </label>
                <textarea
                  value={wizardReq}
                  onChange={(e) => setWizardReq(e.target.value)}
                  rows={5}
                  placeholder="e.g. I need a flow that takes requirements from a product manager, creates a technical spec, splits it into tasks, then has a backend and frontend developer implement them in parallel, followed by QA testing."
                  className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-violet-400 focus:bg-white"
                  disabled={wizardLoading}
                />
              </div>

              {/* Model settings collapsible */}
              <div className="rounded-2xl border border-slate-200 bg-slate-50">
                <button
                  type="button"
                  onClick={() => setModelOpen((v) => !v)}
                  className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-slate-700"
                >
                  <span>Model settings{wizardProvider ? ` · ${PROVIDERS.find((p) => p.value === wizardProvider)?.label}${wizardModel ? ` / ${wizardModel}` : ''}` : ' · global defaults'}</span>
                  {modelOpen ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
                </button>

                {modelOpen && (
                  <div className="space-y-3 border-t border-slate-200 px-4 pb-4 pt-3">
                    {/* Provider */}
                    <div className="space-y-1.5">
                      <label className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Provider</label>
                      <select
                        value={wizardProvider}
                        onChange={(e) => handleProviderChange(e.target.value)}
                        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400"
                        disabled={wizardLoading}
                      >
                        {PROVIDERS.map((p) => (
                          <option key={p.value} value={p.value}>{p.label}</option>
                        ))}
                      </select>
                    </div>

                    {/* Base URL for local providers */}
                    {(wizardProvider === 'ollama' || wizardProvider === 'lmstudio') && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Base URL</label>
                        <div className="flex gap-2">
                          <input
                            value={wizardBaseUrl}
                            onChange={(e) => { setWizardBaseUrl(e.target.value); setAvailableModels([]); }}
                            placeholder={DEFAULT_BASE_URLS[wizardProvider]}
                            className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400"
                            disabled={wizardLoading || fetchingModels}
                          />
                          <button
                            type="button"
                            onClick={handleFetchModels}
                            disabled={wizardLoading || fetchingModels}
                            className="flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 transition hover:border-violet-300 hover:text-violet-700 disabled:opacity-50"
                          >
                            <RefreshCw className={`h-3.5 w-3.5 ${fetchingModels ? 'animate-spin' : ''}`} />
                            {fetchingModels ? 'Fetching…' : 'Fetch models'}
                          </button>
                        </div>
                        {fetchModelsError && (
                          <p className="text-xs text-rose-600">{fetchModelsError}</p>
                        )}
                      </div>
                    )}

                    {/* Model selector */}
                    {wizardProvider && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Model</label>
                        {(() => {
                          const list = CLOUD_MODELS[wizardProvider] || availableModels;
                          return list.length > 0 ? (
                            <select
                              value={wizardModel}
                              onChange={(e) => setWizardModel(e.target.value)}
                              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400"
                              disabled={wizardLoading}
                            >
                              {list.map((m) => (
                                <option key={m} value={m}>{m}</option>
                              ))}
                            </select>
                          ) : (
                            <input
                              value={wizardModel}
                              onChange={(e) => setWizardModel(e.target.value)}
                              placeholder="Type model name or fetch from server above"
                              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400"
                              disabled={wizardLoading}
                            />
                          );
                        })()}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <button
                onClick={handleWizardGenerate}
                disabled={wizardLoading || !wizardReq.trim()}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-violet-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:bg-violet-300"
              >
                <Sparkles className="h-4 w-4" />
                {wizardLoading ? 'Generating...' : 'Generate flow'}
              </button>

              {/* Error */}
              {wizardError && (
                <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
                  <p className="text-sm text-rose-700">{wizardError}</p>
                </div>
              )}

              {/* Result: limitations */}
              {wizardResult?.type === 'limitations' && (
                <div className="space-y-3 rounded-2xl border border-amber-200 bg-amber-50 p-4">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-amber-600" />
                    <span className="text-sm font-semibold text-amber-800">Cannot fully implement with available agents</span>
                  </div>
                  <p className="text-sm leading-6 text-amber-700">{wizardResult.message}</p>
                </div>
              )}

              {/* Result: flow */}
              {wizardResult?.type === 'flow' && (
                <div className="space-y-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                  <div>
                    <div className="text-sm font-bold text-emerald-900">{wizardResult.name}</div>
                    <div className="mt-1 text-sm text-emerald-700">{wizardResult.description}</div>
                  </div>

                  {wizardResult.reasoning && (
                    <div className="rounded-xl border border-emerald-200 bg-white/60 px-3 py-2">
                      <div className="text-xs font-semibold uppercase tracking-wide text-emerald-600">Reasoning</div>
                      <p className="mt-1 text-xs leading-5 text-slate-600">{wizardResult.reasoning}</p>
                    </div>
                  )}

                  <div className="space-y-2">
                    <div className="text-xs font-semibold uppercase tracking-wide text-emerald-600">
                      Nodes ({wizardResult.nodes?.length || 0})
                    </div>
                    <div className="space-y-1.5">
                      {(wizardResult.nodes || []).map((node, i) => (
                        <div key={node.id} className="flex items-start gap-2 rounded-xl border border-emerald-100 bg-white/80 px-3 py-2">
                          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-[10px] font-bold text-emerald-700">
                            {i + 1}
                          </span>
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-slate-800">{node.label || node.agent_id}</div>
                            <div className="text-[11px] text-slate-500">{node.agent_id}</div>
                            {node.description && (
                              <div className="mt-0.5 text-[11px] text-slate-500">{node.description}</div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  <button
                    onClick={handleApplyGeneratedFlow}
                    disabled={applyingFlow}
                    className="flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-emerald-300"
                  >
                    <Plus className="h-4 w-4" />
                    {applyingFlow ? 'Creating...' : 'Create this flow'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentFlows;
