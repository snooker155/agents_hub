import React, { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Wrench, Search, Box, ChevronRight, Terminal, Layers, Plug } from 'lucide-react';
import { getWorkspaceMcpTools, listMcpServers } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { capabilityLabel } from '../lib/capabilities';
import { useI18n } from '../i18n';
import { useWorkspace } from '../components/workspace';
import PageLoader from '../components/PageLoader';

const MCP_PREFIX = 'mcp:';

/**
 * MCP tools: the tools the workspace's MCP servers bring, not the hub's own
 * built-in catalog (that one is picked per agent on its Tools tab). Read-only;
 * a server is added, changed and removed on the MCP servers page.
 */
const ToolsExplorer = () => {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [tools, setTools] = useState([]);
  const [serverNames, setServerNames] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getWorkspaceMcpTools(selectedWorkspace), listMcpServers(selectedWorkspace)])
      .then(([toolsResp, serversResp]) => {
        if (cancelled) return;
        const list = toolsResp.data?.tools || [];
        const names = {};
        (serversResp.data?.servers || []).forEach((s) => { names[s.id] = s.name || s.id; });
        setTools(list);
        setServerNames(names);
        setSelectedId(list[0]?.id || null);
        setError('');
      })
      .catch((err) => {
        if (!cancelled) setError(err.response?.data?.detail || err.message);
      })
      .then(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedWorkspace]);

  const serverOf = (tool) => {
    const id = (tool.category || '').startsWith(MCP_PREFIX) ? tool.category.slice(MCP_PREFIX.length) : tool.category;
    return serverNames[id] || id || t('toolsExplorer.unknownServer');
  };

  const filteredTools = useMemo(() => {
    const q = search.toLowerCase();
    return tools.filter((tool) => [tool.name, tool.id, tool.description, tool.category]
      .some((v) => (v || '').toLowerCase().includes(q)));
  }, [tools, search]);

  const groupedTools = useMemo(() => {
    const grouped = {};
    filteredTools.forEach((tool) => {
      const server = serverOf(tool);
      if (!grouped[server]) grouped[server] = [];
      grouped[server].push(tool);
    });
    return grouped;
    // serverOf reads serverNames, which is in the list below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredTools, serverNames]);

  const selectedTool = tools.find((tool) => tool.id === selectedId) || null;
  const params = selectedTool?.parameters || [];
  const capabilities = selectedTool?.capabilities || [];

  return (
    <PageContainer fill>
      <PageHeader
        icon={Wrench}
        title={t('toolsExplorer.title')}
        description={t('toolsExplorer.description')}
        actions={
          <div className="flex items-center gap-2">
            {tools.length > 0 && (
              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder={t('toolsExplorer.searchTools')}
                  className="pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg bg-white w-64 max-w-full"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            )}
            <Link
              to="/mcp"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50"
            >
              <Plug className="w-4 h-4" />
              {t('toolsExplorer.manageServers')}
            </Link>
          </div>
        }
      />

      {loading ? (
        <PageLoader size="lg" label={t('toolsExplorer.loadingToolDefinitions')} />
      ) : error ? (
        <div className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg p-4">{error}</div>
      ) : tools.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 rounded-xl border border-dashed border-gray-200 text-gray-500 p-12 text-center">
          <Box className="w-12 h-12 mb-4 opacity-30" />
          <p className="font-medium text-gray-700">{t('toolsExplorer.emptyTitle')}</p>
          <p className="text-sm mt-1 max-w-md">{t('toolsExplorer.emptyHint')}</p>
          <Link
            to="/mcp"
            className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700"
          >
            <Plug className="w-4 h-4" />
            {t('toolsExplorer.addServer')}
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0 overflow-hidden">
          <div className="lg:col-span-1 h-full min-h-0 border border-gray-200 rounded-xl bg-white p-3 overflow-y-auto">
            {Object.keys(groupedTools).length === 0 ? (
              <div className="text-sm text-gray-500 px-2 py-6">{t('toolsExplorer.noToolsFound')}</div>
            ) : (
              Object.entries(groupedTools)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([server, serverTools]) => (
                  <div key={server} className="space-y-2 mb-4 last:mb-0">
                    <div className="flex items-center gap-2 px-1">
                      <Layers className="w-3.5 h-3.5 text-gray-400" />
                      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{server}</div>
                    </div>
                    {serverTools.map((tool) => (
                      <button
                        key={tool.id}
                        onClick={() => setSelectedId(tool.id)}
                        className={`w-full text-left p-4 rounded-xl border transition-all ${
                          selectedId === tool.id
                            ? 'bg-indigo-50 border-indigo-200 shadow-sm'
                            : 'bg-white border-gray-100 hover:border-gray-200 hover:shadow-sm'
                        }`}
                      >
                        <div className="flex justify-between items-center mb-1">
                          <span className="font-bold text-gray-800 text-xs">{tool.name || tool.id}</span>
                          <ChevronRight className={`w-4 h-4 text-gray-300 transition-transform ${selectedId === tool.id ? 'rotate-90' : ''}`} />
                        </div>
                        <p className="text-xs text-gray-500 line-clamp-2">{tool.description}</p>
                      </button>
                    ))}
                  </div>
                ))
            )}
          </div>

          <div className="lg:col-span-2 h-full min-h-0">
            {selectedTool ? (
              <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden h-full min-h-0 flex flex-col">
                <div className="p-6 border-b border-gray-50 bg-gray-50/50 shrink-0">
                  <div className="flex items-center justify-between mb-4 gap-3">
                    <h3 className="text-xl font-bold text-gray-800 flex items-center min-w-0">
                      <Terminal className="w-5 h-5 mr-2 text-indigo-500 shrink-0" />
                      <span className="truncate">{selectedTool.name || selectedTool.id}</span>
                    </h3>
                    <span className="text-[10px] bg-indigo-100 text-indigo-700 font-bold px-2 py-1 rounded uppercase tracking-wider shrink-0">
                      {serverOf(selectedTool)}
                    </span>
                  </div>
                  <p className="text-gray-600 text-sm leading-relaxed">{selectedTool.description}</p>
                  {capabilities.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-3">
                      {capabilities.map((cap) => (
                        <span key={cap} className="text-[11px] bg-amber-50 text-amber-700 border border-amber-100 px-2 py-0.5 rounded">
                          {capabilityLabel(cap, t)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="p-6 flex-1 min-h-0 overflow-y-auto">
                  <h4 className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">{t('toolsExplorer.argumentsSchema')}</h4>
                  {params.length === 0 ? (
                    <p className="text-sm text-gray-500">{t('toolsExplorer.noArguments')}</p>
                  ) : (
                    <div className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
                      {params.map((p) => (
                        <div key={p.name} className="p-3">
                          <div className="flex items-center gap-2 text-xs">
                            <code className="font-mono font-semibold text-gray-800">{p.name}</code>
                            <span className="text-gray-400">{p.type}</span>
                            {p.required && (
                              <span className="text-[10px] text-red-600 uppercase tracking-wider">{t('toolsExplorer.required')}</span>
                            )}
                          </div>
                          {p.description && <p className="text-xs text-gray-500 mt-1">{p.description}</p>}
                          {Array.isArray(p.enum) && p.enum.length > 0 && (
                            <p className="text-xs text-gray-500 mt-1 font-mono">{p.enum.join(' | ')}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center bg-gray-50 rounded-xl border border-dashed border-gray-200 text-gray-400 p-12">
                <Box className="w-12 h-12 mb-4 opacity-20" />
                <p>{t('toolsExplorer.selectAToolFromThe')}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </PageContainer>
  );
};

export default ToolsExplorer;
