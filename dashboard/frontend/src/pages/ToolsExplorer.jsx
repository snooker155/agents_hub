import React, { useState, useEffect } from 'react';
import { Wrench, Search, Box, ChevronRight, Terminal } from 'lucide-react';
import { getTools } from '../api';

const ToolsExplorer = () => {
  const [tools, setTools] = useState({ factory: [], swe: [], all: [] });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedTool, setSelectedTool] = useState(null);

  useEffect(() => {
    const fetchTools = async () => {
      try {
        const resp = await getTools();
        setTools(resp.data);
        setLoading(false);
      } catch (error) {
        console.error('Error fetching tools:', error);
        setLoading(false);
      }
    };
    fetchTools();
  }, []);

  const filteredTools = tools.all.filter(t =>
    t.name.toLowerCase().includes(search.toLowerCase()) ||
    t.description.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-800 flex items-center">
            <Wrench className="w-6 h-6 mr-2 text-indigo-600" />
            Tool Inventory
          </h2>
          <p className="text-gray-500 text-sm">Browse capabilities available to agents in the cluster.</p>
        </div>
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search tools..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 outline-none w-64"
          />
        </div>
      </div>

      {loading ? (
        <div className="text-center py-20">Loading tool definitions...</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-1 space-y-4 max-h-[70vh] overflow-y-auto pr-2">
            {filteredTools.map((tool) => (
              <button
                key={tool.name}
                onClick={() => setSelectedTool(tool)}
                className={`w-full text-left p-4 rounded-xl border transition-all ${
                  selectedTool?.name === tool.name
                    ? 'bg-indigo-50 border-indigo-200 shadow-sm'
                    : 'bg-white border-gray-100 hover:border-gray-200 hover:shadow-sm'
                }`}
              >
                <div className="flex justify-between items-center mb-1">
                  <span className="font-bold text-gray-800 font-mono text-xs">{tool.name}</span>
                  <ChevronRight className={`w-4 h-4 text-gray-300 transition-transform ${selectedTool?.name === tool.name ? 'rotate-90' : ''}`} />
                </div>
                <p className="text-xs text-gray-500 line-clamp-2">{tool.description}</p>
              </button>
            ))}
          </div>

          <div className="lg:col-span-2">
            {selectedTool ? (
              <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
                <div className="p-6 border-b border-gray-50 bg-gray-50/50">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-xl font-bold text-gray-800 flex items-center">
                      <Terminal className="w-5 h-5 mr-2 text-indigo-500" />
                      {selectedTool.name}
                    </h3>
                    <span className="text-[10px] bg-indigo-100 text-indigo-700 font-bold px-2 py-1 rounded uppercase tracking-wider">
                      Internal Tool
                    </span>
                  </div>
                  <p className="text-gray-600 text-sm leading-relaxed">
                    {selectedTool.description}
                  </p>
                </div>
                <div className="p-6">
                  <h4 className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Arguments Schema</h4>
                  <div className="bg-gray-900 rounded-lg p-4 overflow-x-auto">
                    <pre className="text-indigo-300 text-xs font-mono">
                      {JSON.stringify(selectedTool.args, null, 2)}
                    </pre>
                  </div>

                  <div className="mt-8">
                    <h4 className="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4">Usage Example</h4>
                    <div className="bg-gray-100 rounded-lg p-4">
                      <code className="text-xs text-gray-700 font-mono">
                        {`# Agent will call ${selectedTool.name} with:\n`}
                        {JSON.stringify(
                          Object.keys(selectedTool.args).reduce((acc, key) => ({ ...acc, [key]: "..." }), {}),
                          null,
                          2
                        )}
                      </code>
                    </div>
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
