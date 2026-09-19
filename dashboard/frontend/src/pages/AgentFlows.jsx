import React, { useEffect, useCallback, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronUp, Download, Factory, GitBranch, Globe, Loader2, Plus, RefreshCw, Sparkles, Trash2, Upload, X } from 'lucide-react';
import { createFlow, deleteFlow, exportFlow, generateFlow, importFlow, listFlows, testLocalModel, updateFlowSharing } from '../api';
import { useWorkspace } from '../components/workspace';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const PROVIDERS = [
  { value: '', labelKey: 'agentFlows.providers.inherit' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
  { value: 'ollama', labelKey: 'agentFlows.providers.ollama' },
  { value: 'lmstudio', labelKey: 'agentFlows.providers.lmstudio' },
];

// Built-in brand names stay as they are; only the descriptive rows carry a key.
const providerLabel = (p, t) => (p.labelKey ? t(p.labelKey) : p.label);

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

function formatDate(value, t) {
  if (!value) return t('agentFlows.noActivityYet');
  return new Date(value).toLocaleString();
}

const AgentFlows = () => {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace, workspaceFilter } = useWorkspace();
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
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

  // Import state
  const fileInputRef = useRef(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importYaml, setImportYaml] = useState('');
  const [importName, setImportName] = useState('');
  const [importFileName, setImportFileName] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');

  const loadFlows = useCallback(async () => {
    setLoading(true);
    try {
      const response = await listFlows(workspaceFilter);
      setFlows(response.data || []);
    } catch (error) {
      console.error('Failed to load flows', error);
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter]);

  useEffect(() => {
    loadFlows();
  }, [loadFlows, selectedWorkspace]);

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
    if (!confirm(t('agentFlows.confirmDelete'))) return;
    try {
      await deleteFlow(flowId);
      await loadFlows();
    } catch (error) {
      alert(`${t('agentFlows.deleteFailed')}: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleShare = async (flow) => {
    const next = !flow.shared;
    const prompt = next
      ? t('agentFlows.confirmPublish')
      : t('agentFlows.confirmUnpublish');
    if (!confirm(prompt)) return;
    try {
      await updateFlowSharing(flow.id, next);
      await loadFlows();
    } catch (error) {
      alert(`Failed to update sharing: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleExport = async (flow) => {
    try {
      const response = await exportFlow(flow.id);
      const blob = new Blob([response.data], { type: 'application/x-yaml' });
      const url = URL.createObjectURL(blob);
      const slug = (flow.name || flow.id).replace(/[^a-zA-Z0-9-_]+/g, '-').replace(/^-+|-+$/g, '') || 'flow';
      const link = document.createElement('a');
      link.href = url;
      link.download = `${slug}.yaml`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      alert(`Failed to export flow: ${error.response?.data?.detail || error.message}`);
    }
  };

  const handleFilePicked = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = ''; // allow re-picking the same file later
    if (!file) return;
    try {
      const text = await file.text();
      setImportYaml(text);
      setImportFileName(file.name);
      setImportName(file.name.replace(/\.ya?ml$/i, ''));
      setImportError('');
      setImportOpen(true);
    } catch (error) {
      alert(`${t('agentFlows.readFileFailed')}: ${error.message}`);
    }
  };

  const handleImport = async () => {
    if (!importYaml.trim()) {
      setImportError(t('agentFlows.noYamlToImport'));
      return;
    }
    setImporting(true);
    setImportError('');
    try {
      const response = await importFlow({
        yaml: importYaml,
        name: importName.trim() || undefined,
      });
      closeImport();
      navigate(`/flows/${response.data.id}`);
    } catch (error) {
      setImportError(error.response?.data?.detail || error.message || t('agentFlows.importFailed'));
    } finally {
      setImporting(false);
    }
  };

  const closeImport = () => {
    setImportOpen(false);
    setImportYaml('');
    setImportName('');
    setImportFileName('');
    setImportError('');
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
      setWizardError(error.response?.data?.detail || error.message || t('agentFlows.generationFailed'));
    } finally {
      setWizardLoading(false);
    }
  };

  const handleApplyGeneratedFlow = async () => {
    if (!wizardResult || wizardResult.type !== 'flow') return;
    // The flow_creator agent already persisted the flow — just open it.
    if (wizardResult.flow_id) {
      const flowId = wizardResult.flow_id;
      setWizardOpen(false);
      setWizardResult(null);
      setWizardReq('');
      navigate(`/flows/${flowId}`);
      return;
    }
    setApplyingFlow(true);
    try {
      const response = await createFlow({
        name: wizardResult.name || t('agentFlows.aiGeneratedFlow'),
        description: wizardResult.description || '',
      });
      const flowId = response.data.id;

      // Build ReactFlow-compatible nodes and edges with positions
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
        setFetchModelsError(response.data.error || t('agentFlows.couldNotConnect'));
      }
    } catch (e) {
      setFetchModelsError(e.response?.data?.detail || e.message || t('agentFlows.fetchModelsFailed'));
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
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Factory}
        title={t('agentFlows.agentFlows')}
        description={t('agentFlows.buildReusableDeliveryPipelinesAnd')}
        actions={<>
          <input
            ref={fileInputRef}
            type="file"
            accept=".yaml,.yml,application/x-yaml,text/yaml"
            onChange={handleFilePicked}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-all hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700"
          >
            <Upload className="w-4 h-4" />
            {t('agentFlows.import')}
          </button>
          <button
            onClick={() => setWizardOpen(true)}
            className="flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-medium text-violet-700 transition-all hover:bg-violet-100"
          >
            <Sparkles className="w-4 h-4" />
            {t('agentFlows.generateWithAi')}
          </button>
          <button
            onClick={() => setCreateOpen(true)}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" />
            {t('agentFlows.createFlow2')}
          </button>
        </>}
      />

      <section>
        <div>

          {loading ? (
            <div className="flex min-h-[280px] items-center justify-center rounded-3xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
              {t('agentFlows.loadingFlows')}
            </div>
          ) : visibleFlows.length === 0 ? (
            <div className="flex min-h-[280px] flex-col items-center justify-center gap-3 rounded-3xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center">
              <GitBranch className="h-9 w-9 text-slate-300" />
              <div className="space-y-1">
                <div className="text-sm font-semibold text-slate-900">{t('agentFlows.noFlowsYet')}</div>
                <div className="text-sm text-slate-500">{t('agentFlows.useTheCreateFlowButton')}</div>
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
                          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-cyan-500" title={t('agentFlows.running')} />
                        )}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[11px] text-gray-500">
                        {flow.description || 'No description provided.'}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <span className="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-gray-600">
                        {flow.workspace || 'Shared'}
                      </span>
                      {flow.shared && (
                        <span className="inline-flex items-center gap-1 rounded bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold uppercase text-indigo-600" title={t('agentFlows.publishedToTheMarketplace')}>
                          <Globe className="h-2.5 w-2.5" /> {t('agentFlows.market')}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="mb-3 grid grid-cols-2 gap-2">
                    <div className="rounded-md border border-blue-100 bg-blue-50 px-2 py-1.5 text-center">
                      <div className="text-[9px] font-semibold uppercase tracking-wide text-blue-500">{t('agentFlows.nodes')}</div>
                      <div className="text-xs font-semibold text-blue-900">{flow.nodes?.length || 0}</div>
                    </div>
                    <div className="rounded-md border border-amber-100 bg-amber-50 px-2 py-1.5 text-center">
                      <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">{t('agentFlows.updated')}</div>
                      <div className="truncate text-xs font-semibold text-amber-900">{formatDate(flow.updated_at, t)}</div>
                    </div>
                  </div>

                  <div className="flex justify-end gap-1">
                    <button
                      onClick={() => handleShare(flow)}
                      className={`rounded-lg border border-transparent p-1.5 transition ${
                        flow.shared
                          ? 'text-indigo-500 hover:border-indigo-200 hover:bg-indigo-50'
                          : 'text-gray-400 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-600'
                      }`}
                      title={flow.shared ? 'Remove from marketplace' : 'Publish to marketplace (also publishes its agents)'}
                    >
                      <Globe className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleExport(flow)}
                      className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-600"
                      title={t('agentFlows.exportFlowAsYaml')}
                    >
                      <Download className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleDelete(flow.id)}
                      className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
                      title={t('agentFlows.deleteFlow')}
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

      {/* Create Flow Modal */}
      {createOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
          <div className="relative flex w-full max-w-lg flex-col rounded-[28px] border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div className="flex items-center gap-2">
                <Plus className="h-5 w-5 text-cyan-600" />
                <h2 className="text-lg font-bold text-slate-900">{t('agentFlows.createFlow')}</h2>
              </div>
              <button
                onClick={() => setCreateOpen(false)}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleCreate} className="space-y-5 p-6">
              <p className="text-sm text-slate-500">{t('agentFlows.startWithABlankCanvas')}</p>
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{t('agentFlows.name')}</label>
                <input
                  autoFocus
                  value={newFlow.name}
                  onChange={(event) => setNewFlow((current) => ({ ...current, name: event.target.value }))}
                  placeholder={t('agentFlows.customerOnboardingPipeline')}
                  className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{t('agentFlows.description')}</label>
                <textarea
                  value={newFlow.description}
                  onChange={(event) => setNewFlow((current) => ({ ...current, description: event.target.value }))}
                  rows={4}
                  placeholder={t('agentFlows.whatThisFlowIsResponsible')}
                  className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                />
              </div>
              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setCreateOpen(false)}
                  disabled={creating}
                  className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                >
                  {t('agentFlows.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={creating || !newFlow.name.trim()}
                  className="flex items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
                >
                  <Plus className="h-4 w-4" />
                  {creating ? 'Creating...' : 'Create flow'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Import Modal — review parsed YAML before validating + storing it */}
      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
          <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col rounded-[28px] border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div className="flex items-center gap-2">
                <Upload className="h-5 w-5 text-cyan-600" />
                <h2 className="text-lg font-bold text-slate-900">{t('agentFlows.importFlowFromYaml')}</h2>
              </div>
              <button
                onClick={closeImport}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
              {importFileName && (
                <div className="text-xs text-slate-500">
                  From file: <span className="font-semibold text-slate-700">{importFileName}</span>
                </div>
              )}

              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{t('agentFlows.flowName')}</label>
                <input
                  value={importName}
                  onChange={(e) => setImportName(e.target.value)}
                  placeholder={t('agentFlows.importedFlow')}
                  className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  disabled={importing}
                />
              </div>

              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">YAML</label>
                <textarea
                  value={importYaml}
                  onChange={(e) => setImportYaml(e.target.value)}
                  rows={14}
                  spellCheck={false}
                  placeholder={t('agentFlows.pasteFlowYamlHereOr')}
                  className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 font-mono text-xs text-slate-900 outline-none transition focus:border-cyan-400 focus:bg-white"
                  disabled={importing}
                />
              </div>

              {importError && (
                <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
                  <p className="text-sm text-rose-700">{importError}</p>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-3 border-t border-slate-100 px-6 py-4">
              <button
                onClick={closeImport}
                disabled={importing}
                className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
              >
                {t('agentFlows.cancel')}
              </button>
              <button
                onClick={handleImport}
                disabled={importing || !importYaml.trim()}
                className="flex items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
              >
                {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {importing ? 'Validating…' : 'Validate & import'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* AI Wizard Modal */}
      {wizardOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-2xl rounded-[28px] border border-violet-200 bg-white shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-violet-600" />
                <h2 className="text-lg font-bold text-slate-900">{t('agentFlows.generateFlowWithAi')}</h2>
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
                  {t('agentFlows.describeTheFunctionalityYouNeed')}
                </label>
                <textarea
                  value={wizardReq}
                  onChange={(e) => setWizardReq(e.target.value)}
                  rows={5}
                  placeholder={t('agentFlows.eGINeedA')}
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
                  <span>{t('agentFlows.modelSettings')}{wizardProvider
                    ? ` · ${providerLabel(PROVIDERS.find((p) => p.value === wizardProvider) || {}, t)}${wizardModel ? ` / ${wizardModel}` : ''}`
                    : ` · ${t('agentFlows.globalDefaults')}`}</span>
                  {modelOpen ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
                </button>

                {modelOpen && (
                  <div className="space-y-3 border-t border-slate-200 px-4 pb-4 pt-3">
                    {/* Provider */}
                    <div className="space-y-1.5">
                      <label className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">{t('agentFlows.provider')}</label>
                      <select
                        value={wizardProvider}
                        onChange={(e) => handleProviderChange(e.target.value)}
                        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400"
                        disabled={wizardLoading}
                      >
                        {PROVIDERS.map((p) => (
                          <option key={p.value} value={p.value}>{providerLabel(p, t)}</option>
                        ))}
                      </select>
                    </div>

                    {/* Base URL for local providers */}
                    {(wizardProvider === 'ollama' || wizardProvider === 'lmstudio') && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">{t('agentFlows.baseUrl')}</label>
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
                        <label className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">{t('agentFlows.model')}</label>
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
                              placeholder={t('agentFlows.typeModelNameOrFetch')}
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
                    <span className="text-sm font-semibold text-amber-800">{t('agentFlows.cannotFullyImplementWithAvailable')}</span>
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
                      <div className="text-xs font-semibold uppercase tracking-wide text-emerald-600">{t('agentFlows.reasoning')}</div>
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
                    {applyingFlow ? 'Creating...' : wizardResult.flow_id ? 'Open flow' : 'Create this flow'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
};

export default AgentFlows;
