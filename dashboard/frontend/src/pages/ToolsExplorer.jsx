import React, { useState, useEffect, useMemo } from 'react';
import { Wrench, Search, Box, ChevronRight, Terminal, Save, FileCode2, Layers } from 'lucide-react';
import { getTools, getToolSource, updateToolSource } from '../api';

const ToolsExplorer = () => {
  const [tools, setTools] = useState({ factory: [], swe: [], all: [] });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedTool, setSelectedTool] = useState(null);
  const [loadingSource, setLoadingSource] = useState(false);
  const [sourceCode, setSourceCode] = useState('');
  const [sourceMeta, setSourceMeta] = useState(null);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveMessage, setSaveMessage] = useState('');

  useEffect(() => {
    const fetchTools = async () => {
      try {
        const resp = await getTools();
        setTools(resp.data);
        if (resp.data?.all?.length) {
          setSelectedTool(resp.data.all[0]);
        }
        setLoading(false);
      } catch (error) {
        console.error('Error fetching tools:', error);
        setLoading(false);
      }
    };
    fetchTools();
  }, []);

  useEffect(() => {
    const loadSource = async () => {
      if (!selectedTool?.id && !selectedTool?.name) {
        setSourceCode('');
        setSourceMeta(null);
        return;
      }
      setLoadingSource(true);
      setSaveMessage('');
      try {
        const toolId = selectedTool.id || selectedTool.name;
        const resp = await getToolSource(toolId);
        setSourceCode(resp.data?.source_code || '');
        setSourceMeta(resp.data || null);
      } catch (error) {
        setSourceMeta(null);
        setSourceCode('');
        setSaveMessage(error?.response?.data?.detail || 'Failed to load tool source');
      } finally {
        setLoadingSource(false);
      }
    };
    loadSource();
  }, [selectedTool]);

  const filteredTools = useMemo(
    () =>
      (tools.all || []).filter((t) => {
        const label = (t.display_name || t.name || '').toLowerCase();
        const desc = (t.description || '').toLowerCase();
        const cat = (t.category || '').toLowerCase();
        const q = search.toLowerCase();
        return label.includes(q) || desc.includes(q) || cat.includes(q);
      }),
    [tools.all, search]
  );

  const groupedTools = useMemo(() => {
    const grouped = {};
    filteredTools.forEach((tool) => {
      const category = tool.category || 'other';
      if (!grouped[category]) grouped[category] = [];
      grouped[category].push(tool);
    });
    return grouped;
  }, [filteredTools]);

  const selectedArgs = selectedTool?.args || {};
  const isDirty = selectedTool && sourceMeta && sourceCode !== (sourceMeta.source_code || '');

  const handleSave = async () => {
    if (!selectedTool) return;
    setSaveBusy(true);
    setSaveMessage('');
    try {
      const toolId = selectedTool.id || selectedTool.name;
      await updateToolSource(toolId, { source_code: sourceCode });
      const refreshed = await getToolSource(toolId);
      setSourceMeta(refreshed.data || null);
      setSourceCode(refreshed.data?.source_code || '');
      setSaveMessage('Saved');
    } catch (error) {
      setSaveMessage(error?.response?.data?.detail || 'Failed to save source');
    } finally {
      setSaveBusy(false);
    }
  };

  return (
    <div className="h-full min-h-0 flex flex-col gap-6">
      <div className="flex flex-wrap gap-3 justify-between items-center shrink-0">
        <div>
          <h2 className="text-2xl font-bold text-gray-800 flex items-center">
            <Wrench className="w-6 h-6 mr-2 text-indigo-600" />
            Tool Inventory
          </h2>
          <p className="text-gray-500 text-sm">Browse tools available to agents in the cluster.</p>
        </div>
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search tools..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 outline-none w-64 max-w-full"
          />
        </div>
      </div>

      {loading ? (
        <div className="text-center py-20 flex-1 min-h-0">Loading tool definitions...</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0 overflow-hidden">
          <div className="lg:col-span-1 h-full min-h-0 border border-gray-200 rounded-xl bg-white p-3 overflow-y-auto">
            {Object.keys(groupedTools).length === 0 ? (
              <div className="text-sm text-gray-500 px-2 py-6">No tools found.</div>
            ) : (
              Object.entries(groupedTools)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([category, categoryTools]) => (
                  <div key={category} className="space-y-2 mb-4 last:mb-0">
                    <div className="flex items-center gap-2 px-1">
                      <Layers className="w-3.5 h-3.5 text-gray-400" />
                      <div className="text-[11px] font-bold uppercase tracking-wider text-gray-500">{category}</div>
                    </div>
                    {categoryTools.map((tool) => (
                      <button
                        key={tool.id || tool.name}
                        onClick={() => setSelectedTool(tool)}
                        className={`w-full text-left p-4 rounded-xl border transition-all ${
                          (selectedTool?.id || selectedTool?.name) === (tool.id || tool.name)
                            ? 'bg-indigo-50 border-indigo-200 shadow-sm'
                            : 'bg-white border-gray-100 hover:border-gray-200 hover:shadow-sm'
                        }`}
                      >
                        <div className="flex justify-between items-center mb-1">
                          <span className="font-bold text-gray-800 text-xs">{tool.display_name || tool.name}</span>
                          <ChevronRight className={`w-4 h-4 text-gray-300 transition-transform ${(selectedTool?.id || selectedTool?.name) === (tool.id || tool.name) ? 'rotate-90' : ''}`} />
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
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-xl font-bold text-gray-800 flex items-center">
                      <Terminal className="w-5 h-5 mr-2 text-indigo-500" />
                      {selectedTool.display_name || selectedTool.name}
                    </h3>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] bg-indigo-100 text-indigo-700 font-bold px-2 py-1 rounded uppercase tracking-wider">
                        {selectedTool.category || 'tool'}
                      </span>
                      {selectedTool.requires_workspace && (
                        <span className="text-[10px] bg-amber-100 text-amber-700 font-bold px-2 py-1 rounded uppercase tracking-wider">
                          workspace
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="text-gray-600 text-sm leading-relaxed">
                    {selectedTool.description}
                  </p>
                </div>
                <div className="p-6 flex-1 min-h-0 overflow-y-auto">
                  <h4 className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Arguments Schema</h4>
                  <div className="bg-gray-900 rounded-lg p-4 overflow-x-auto">
                    <pre className="text-indigo-300 text-xs">
                      {JSON.stringify(selectedArgs, null, 2)}
                    </pre>
                  </div>

                  <div className="mt-8 flex flex-col min-h-[360px] h-[calc(100%-10rem)]">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Source Code</h4>
                      <div className="flex items-center gap-2">
                        {sourceMeta?.path && (
                          <span className="text-[11px] text-gray-500 inline-flex items-center gap-1">
                            <FileCode2 className="w-3.5 h-3.5" />
                            {sourceMeta.path}:{sourceMeta.line || 1}
                          </span>
                        )}
                        <button
                          type="button"
                          disabled={!sourceMeta || !selectedTool || !isDirty || saveBusy}
                          onClick={handleSave}
                          className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-40"
                        >
                          {saveBusy ? <span className="animate-pulse">Saving</span> : <Save className="w-3.5 h-3.5" />}
                          Save
                        </button>
                      </div>
                    </div>
                    <div className="border border-gray-200 rounded-lg overflow-hidden flex-1 min-h-[280px]">
                      {loadingSource ? (
                        <div className="p-6 text-sm text-gray-500">Loading source…</div>
                      ) : (
                        <textarea
                          value={sourceCode}
                          onChange={(e) => setSourceCode(e.target.value)}
                          spellCheck={false}
                          className="w-full h-full p-3 bg-gray-950 text-emerald-300 text-xs outline-none"
                          placeholder="No source available for this tool."
                          disabled={!sourceMeta}
                        />
                      )}
                    </div>
                    {saveMessage && (
                      <div className={`mt-2 text-xs ${saveMessage === 'Saved' ? 'text-emerald-600' : 'text-red-600'}`}>
                        {saveMessage}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center bg-gray-50 rounded-xl border border-dashed border-gray-200 text-gray-400 p-12">
                <Box className="w-12 h-12 mb-4 opacity-20" />
                <p>Select a tool from the inventory to view its technical specification.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default ToolsExplorer;
