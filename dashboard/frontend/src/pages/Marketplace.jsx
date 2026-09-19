import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import {
  Store,
  RefreshCw,
  Shield,
  Plus,
  Check,
  Search,
  Folder,
  Cpu,
  BookOpen,
  Terminal,
  GitBranch,
  Bot,
} from 'lucide-react';
import { getMarketplaceAgents, getMarketplaceFlows, addAgentToWorkspace, addFlowToWorkspace } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const Marketplace = () => {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [tab, setTab] = useState('agents');
  const [agents, setAgents] = useState([]);
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [addingId, setAddingId] = useState(null);
  const [message, setMessage] = useState('');

  const isDefaultWs = !selectedWorkspace || selectedWorkspace === 'default';

  const fetchData = useCallback(async () => {
    try {
      const [agentsResp, flowsResp] = await Promise.all([
        getMarketplaceAgents(selectedWorkspace || 'default'),
        getMarketplaceFlows(selectedWorkspace || 'default'),
      ]);
      setAgents(agentsResp.data || []);
      setFlows(flowsResp.data || []);
    } catch (error) {
      console.error('Error fetching marketplace catalog:', error);
    } finally {
      setLoading(false);
    }
  }, [selectedWorkspace]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  const handleAdd = async (agentId) => {
    if (isDefaultWs) return;
    setAddingId(agentId);
    setMessage('');
    try {
      await addAgentToWorkspace(selectedWorkspace, agentId);
      setAgents(prev => prev.map(a => (a.id === agentId ? { ...a, in_workspace: true } : a)));
      setMessage(t('marketplace.agentAdded', { agent: agentId, workspace: selectedWorkspace }));
      setTimeout(() => setMessage(''), 4000);
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setAddingId(null);
    }
  };

  const handleAddFlow = async (flow) => {
    const ws = selectedWorkspace || 'default';
    setAddingId(flow.id);
    setMessage('');
    try {
      const resp = await addFlowToWorkspace(ws, flow.id);
      setFlows(prev => prev.map(f => (f.id === flow.id ? { ...f, in_workspace: true } : f)));
      // Adding a flow also adds its agents to the workspace allow-list.
      const added = resp.data?.added_agents || [];
      setAgents(prev => prev.map(a => (added.includes(a.id) ? { ...a, in_workspace: true } : a)));
      setMessage(added.length
        ? t('marketplace.flowAddedWithAgents', { flow: flow.name, workspace: ws, count: added.length })
        : t('marketplace.flowAdded', { flow: flow.name, workspace: ws }));
      setTimeout(() => setMessage(''), 5000);
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setAddingId(null);
    }
  };

  const visible = agents.filter(a => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      a.id.toLowerCase().includes(q) ||
      (a.name || '').toLowerCase().includes(q) ||
      (a.description || '').toLowerCase().includes(q) ||
      (a.domain || '').toLowerCase().includes(q)
    );
  });

  const visibleFlows = flows.filter(f => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      (f.name || '').toLowerCase().includes(q) ||
      (f.description || '').toLowerCase().includes(q) ||
      (f.agents || []).some(a => a.id.toLowerCase().includes(q) || (a.name || '').toLowerCase().includes(q))
    );
  });

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Store}
        title={t('marketplace.marketplace')}
        description={t('marketplace.agentsAndFlowsPublishedBy')}
        actions={<>
          <div className="flex rounded-lg border border-gray-200 overflow-hidden">
            <button
              onClick={() => setTab('agents')}
              className={`inline-flex items-center px-3 py-2 text-xs font-semibold transition-colors ${
                tab === 'agents' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              <Bot className="w-3.5 h-3.5 mr-1.5" /> Agents ({agents.length})
            </button>
            <button
              onClick={() => setTab('flows')}
              className={`inline-flex items-center px-3 py-2 text-xs font-semibold transition-colors ${
                tab === 'flows' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              <GitBranch className="w-3.5 h-3.5 mr-1.5" /> Flows ({flows.length})
            </button>
          </div>
          <div className="relative">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder={tab === 'flows' ? t('marketplace.searchFlows') : t('marketplace.searchAgents')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none w-64"
            />
          </div>
        </>}
      />

      {message && (
        <div className="bg-green-50 border border-green-200 text-green-700 text-sm rounded-xl px-4 py-3 flex items-center gap-2">
          <Check className="w-4 h-4" /> {message}
        </div>
      )}

      {tab === 'agents' && isDefaultWs && (
        <div className="bg-indigo-50 border border-indigo-100 text-indigo-700 text-xs rounded-xl px-4 py-3">
          You are viewing the default workspace — marketplace agents are already visible here.
          Switch to a workspace to add agents to it.
        </div>
      )}

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
          <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
          <p className="text-gray-500 font-medium">{t('marketplace.loadingMarketplace')}</p>
        </div>
      ) : tab === 'flows' ? (
        visibleFlows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
            <GitBranch className="w-10 h-10 text-gray-300 mb-3" />
            <p className="text-gray-500 font-medium">
              {query ? t('marketplace.noFlowsMatch') : t('marketplace.noFlowsPublished')}
            </p>
            {!query && (
              <p className="text-gray-400 text-sm mt-1 max-w-md text-center">
                {t('marketplace.publishFlowHint')}
              </p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {visibleFlows.map((flow) => (
              <div key={flow.id} className="bg-white rounded-lg border border-gray-100 p-4 shadow-sm hover:shadow-md transition-all flex flex-col">
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-start gap-2 min-w-0 flex-1">
                    <div className="p-1.5 rounded bg-cyan-50 text-cyan-600">
                      <GitBranch className="w-4 h-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-gray-900 truncate">{flow.name}</div>
                      <div className="text-[11px] text-gray-400">{t('marketplace.nodeCount', { count: flow.nodes_count })}</div>
                    </div>
                  </div>
                  <span className="text-[10px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded uppercase font-semibold shrink-0">
                    {t('marketplace.flow')}
                  </span>
                </div>

                <p className="text-xs text-gray-500 leading-relaxed mb-3 line-clamp-3 min-h-[3em]">
                  {flow.description || t('marketplace.noDescription')}
                </p>

                <div className="mb-3">
                  <div className="text-[10px] uppercase font-semibold text-gray-400 mb-1">
                    {t('marketplace.agentsIncluded')} ({(flow.agents || []).length})
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(flow.agents || []).slice(0, 4).map((a) => (
                      <span key={a.id} className="text-[10px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600" title={a.id}>
                        {a.name || a.id}
                      </span>
                    ))}
                    {(flow.agents || []).length > 4 && (
                      <span className="text-[10px] text-gray-400">+{(flow.agents || []).length - 4}</span>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-400 mb-4">
                  {flow.owner_workspace && (
                    <span className="inline-flex items-center gap-1" title={t('marketplace.publishedFromThisWorkspace')}>
                      <Folder className="w-3 h-3" /> {flow.owner_workspace}
                    </span>
                  )}
                </div>

                <div className="mt-auto flex items-center gap-2">
                  {flow.in_workspace ? (
                    <span className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-green-50 text-green-700 border border-green-200 rounded">
                      <Check className="w-3.5 h-3.5 mr-1" />
                      {isDefaultWs ? t('marketplace.available') : t('marketplace.inWorkspace')}
                    </span>
                  ) : (
                    <button
                      onClick={() => handleAddFlow(flow)}
                      disabled={addingId === flow.id}
                      title={t('marketplace.addThisFlowAndAll')}
                      className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                    >
                      {addingId === flow.id
                        ? <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" />
                        : <Plus className="w-3.5 h-3.5 mr-1" />}
                      Add with agents
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )
      ) : visible.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
          <Store className="w-10 h-10 text-gray-300 mb-3" />
          <p className="text-gray-500 font-medium">
            {query ? t('marketplace.noAgentsMatch') : t('marketplace.noAgentsPublished')}
          </p>
          {!query && (
            <p className="text-gray-400 text-sm mt-1 max-w-md text-center">
              {t('marketplace.publishAWorkspaceAgentFrom')}
            </p>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {visible.map((agent) => (
            <div key={agent.id} className="bg-white rounded-lg border border-gray-100 p-4 shadow-sm hover:shadow-md transition-all flex flex-col">
              <div className="flex items-start justify-between gap-2 mb-3">
                <div className="flex items-start gap-2 min-w-0 flex-1">
                  <div className="p-1.5 rounded bg-indigo-50 text-indigo-600">
                    <Shield className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <Link to={`/marketplace/${agent.id}`} className="text-sm font-semibold text-gray-900 hover:text-indigo-600 truncate block">
                      {agent.name}
                    </Link>
                    <div className="text-[11px] text-gray-400 truncate">{agent.id}</div>
                  </div>
                </div>
                <span className="text-[10px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded uppercase font-semibold shrink-0">
                  {agent.domain || 'general'}
                </span>
              </div>

              <p className="text-xs text-gray-500 leading-relaxed mb-3 line-clamp-3 min-h-[3em]">
                {agent.description || t('marketplace.noDescription')}
              </p>

              <div className="flex flex-wrap gap-1 mb-3 min-h-[22px]">
                {(agent.tools || []).slice(0, 3).map((tool) => (
                  <span key={tool} className="text-[10px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600">
                    {tool}
                  </span>
                ))}
                {(agent.tools || []).length > 3 && (
                  <span className="text-[10px] text-gray-400">+{(agent.tools || []).length - 3}</span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-400 mb-4">
                {agent.owner_workspace && (
                  <span className="inline-flex items-center gap-1" title={t('marketplace.publishedFromThisWorkspace')}>
                    <Folder className="w-3 h-3" /> {agent.owner_workspace}
                  </span>
                )}
                <span className="inline-flex items-center gap-1" title={t('marketplace.model')}>
                  <Cpu className="w-3 h-3" />
                  {agent.model?.provider && agent.model.provider !== 'inherit'
                    ? `${agent.model.provider}${agent.model.model ? ` · ${agent.model.model}` : ''}`
                    : t('marketplace.inheritsModel')}
                </span>
                {agent.commands_count > 0 && (
                  <span className="inline-flex items-center gap-1" title={t('marketplace.slashCommands')}>
                    <Terminal className="w-3 h-3" /> {agent.commands_count}
                  </span>
                )}
                {agent.skills_enabled && (
                  <span className="inline-flex items-center gap-1" title={t('marketplace.skillsEnabled')}>
                    <BookOpen className="w-3 h-3" /> {t('marketplace.skills')}
                  </span>
                )}
              </div>

              <div className="mt-auto flex items-center gap-2">
                <Link
                  to={`/marketplace/${agent.id}`}
                  className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold border border-gray-200 text-gray-600 rounded hover:bg-gray-50 transition-colors"
                >
                  {t('marketplace.details')}
                </Link>
                {agent.in_workspace ? (
                  <span className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-green-50 text-green-700 border border-green-200 rounded">
                    <Check className="w-3.5 h-3.5 mr-1" />
                    {isDefaultWs ? t('marketplace.available') : t('marketplace.inWorkspace')}
                  </span>
                ) : (
                  <button
                    onClick={() => handleAdd(agent.id)}
                    disabled={addingId === agent.id}
                    className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                  >
                    {addingId === agent.id
                      ? <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" />
                      : <Plus className="w-3.5 h-3.5 mr-1" />}
                    Add
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </PageContainer>
  );
};

export default Marketplace;
