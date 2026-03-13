import React, { useState, useEffect, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import { ChevronLeft, Activity, History, Server, Wrench, Cpu, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2, FileCode, Play, Square, Loader, X, FileText, BrainCircuit, Eye, EyeOff, Link2, Layers, Hash, Copy, FileSearch, Zap, BarChart2, Wifi } from 'lucide-react';
import { getAgent, getAgentHistory, getAgentHealth, getLogs, updateAgentMemory, eraseAgentMemory, updateAgentTools, getAgentModel, updateAgentModel, getNodes, getAgentDefinition, getTasks, getTools, startNode, stopNode, deleteNode, getWorkspaces, getNodeLogs, getSharedMemories, getSharedMemory, testLocalModel } from '../api';

const NODE_STATUS = {
  running: { dot: 'bg-green-500 animate-pulse', badge: 'bg-green-100 text-green-800', label: 'Running' },
  starting: { dot: 'bg-yellow-400 animate-pulse', badge: 'bg-yellow-100 text-yellow-800', label: 'Starting' },
  stopping: { dot: 'bg-orange-400 animate-pulse', badge: 'bg-orange-100 text-orange-800', label: 'Stopping' },
  stopped: { dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600', label: 'Stopped' },
  failed: { dot: 'bg-red-500', badge: 'bg-red-100 text-red-700', label: 'Failed' },
  completed: { dot: 'bg-blue-400', badge: 'bg-blue-100 text-blue-700', label: 'Completed' },
};

function NodeStatusBadge({ status }) {
  const s = NODE_STATUS[status] || NODE_STATUS.stopped;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold ${s.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.dot}`} />
      {s.label}
    </span>
  );
}

function fmtNodeDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function nodeUptime(startedAt, finishedAt) {
  if (!startedAt) return '—';
  const end = finishedAt ? new Date(finishedAt) : new Date();
  const secs = Math.max(0, Math.floor((end - new Date(startedAt)) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

const EMPTY_MODEL = { provider: 'inherit', model: '', api_key: '', base_url: '', temperature: '', max_tokens: '' };

// ── Memory pool helpers ───────────────────────────────────────────────────────

const fmtBytes = (b) => {
  if (!b) return '0 B';
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
};

const RAG_STATUS = {
  raw:     { label: 'Raw text',  color: 'bg-gray-100 text-gray-600',    dot: 'bg-gray-400' },
  indexed: { label: 'Indexed',   color: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
  failed:  { label: 'Failed',    color: 'bg-red-100 text-red-700',      dot: 'bg-red-500' },
};

function RagBadge({ status, vectorized }) {
  const cfg = RAG_STATUS[status] || RAG_STATUS.raw;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {vectorized ? 'Vectorized' : cfg.label}
    </span>
  );
}

function MemoryFileCard({ file, poolId }) {
  const [showPreview, setShowPreview] = useState(false);
  const [copied, setCopied] = useState(false);

  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const toolArgs = JSON.stringify({ memory_id: poolId, file_name: file.name }, null, 2);

  const copyTool = () => {
    navigator.clipboard.writeText(toolArgs).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden bg-white">
      {/* Header */}
      <div className="px-4 py-3 flex items-start gap-3 border-b border-gray-100">
        <div className="p-2 bg-indigo-50 rounded-lg shrink-0">
          <FileText className="w-4 h-4 text-indigo-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-semibold text-gray-900 text-sm">{file.name}</p>
            {ext && <span className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded font-mono uppercase">{ext}</span>}
            <RagBadge status={file.rag_status || 'raw'} vectorized={file.vectorized} />
          </div>
          <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
            {file.size_bytes > 0 && <span>{fmtBytes(file.size_bytes)}</span>}
            {file.rag_chunks > 0 && <span>{file.rag_chunks} chunks</span>}
            {file.embedding_dims > 0 && <span>{file.embedding_dims}d</span>}
          </div>
        </div>
        <button onClick={() => setShowPreview(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-700 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50 shrink-0">
          <Eye className="w-3 h-3" /> {showPreview ? 'Hide' : 'Preview'}
        </button>
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* read_memory tool call */}
        <div>
          <p className="text-xs font-medium text-gray-500 mb-1 flex items-center gap-1">
            <Hash className="w-3 h-3" /> Retrieval — <code className="text-indigo-600">read_memory</code> tool
          </p>
          <div className="bg-gray-900 rounded-lg px-3 py-2 flex items-start justify-between gap-2">
            <pre className="text-xs text-green-300 font-mono overflow-x-auto flex-1">{toolArgs}</pre>
            <button onClick={copyTool}
              className="text-gray-400 hover:text-white shrink-0 mt-0.5 transition-colors" title="Copy">
              {copied ? <CheckCircle className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>

        {/* Vector metadata */}
        {file.vectorized && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs bg-indigo-50 rounded-lg px-3 py-2.5">
            <div className="flex items-center gap-1.5 col-span-2">
              <Zap className="w-3 h-3 text-indigo-500" />
              <span className="font-medium text-indigo-800">Vector Search Available</span>
            </div>
            {file.vector_db && <div><span className="text-gray-500">Vector DB:</span> <span className="font-medium text-gray-800">{file.vector_db}</span></div>}
            {file.vector_db_collection && <div><span className="text-gray-500">Collection:</span> <span className="font-medium text-gray-800 font-mono">{file.vector_db_collection}</span></div>}
            {file.embedding_model && <div><span className="text-gray-500">Model:</span> <span className="font-medium text-gray-800 font-mono">{file.embedding_model}</span></div>}
            {file.embedding_dims > 0 && <div><span className="text-gray-500">Dims:</span> <span className="font-medium text-gray-800">{file.embedding_dims}</span></div>}
            {file.rag_chunk_size && <div><span className="text-gray-500">Chunk size:</span> <span className="font-medium text-gray-800">{file.rag_chunk_size} chars</span></div>}
          </div>
        )}

        {/* RAG only (no vector) */}
        {file.rag_status === 'indexed' && !file.vectorized && (
          <div className="text-xs bg-green-50 rounded-lg px-3 py-2 text-green-700 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5" />
            Text chunked into {file.rag_chunks} chunks ({file.rag_chunk_size} chars each).
            Configure a vector DB in <strong>Settings → RAG & Vectors</strong> to enable semantic search.
          </div>
        )}

        {/* Content preview */}
        {showPreview && (
          <div>
            <p className="text-xs font-medium text-gray-500 mb-1 flex items-center gap-1"><Eye className="w-3 h-3" /> Content preview</p>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 font-mono text-xs text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
              {file.content
                ? (file.content.length > 800 ? file.content.slice(0, 800) + '\n…' : file.content)
                : <span className="italic text-gray-400">No content</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MemoryPoolDetails({ pool }) {
  const files = pool.files || [];
  const rawFiles      = files.filter(f => !f.rag_status || f.rag_status === 'raw');
  const indexedFiles  = files.filter(f => f.rag_status === 'indexed');
  const vectorized    = files.filter(f => f.vectorized);
  const totalChunks   = files.reduce((s, f) => s + (f.rag_chunks || 0), 0);

  const [filter, setFilter] = useState('all'); // all | raw | indexed | vectorized

  const visible = filter === 'all' ? files
    : filter === 'raw'       ? rawFiles
    : filter === 'indexed'   ? indexedFiles
    :                          vectorized;

  return (
    <div className="space-y-4">
      {/* Pool header */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <Link2 className="w-4 h-4 text-indigo-500 shrink-0" />
              <h3 className="font-bold text-gray-900">{pool.name}</h3>
              <span className="inline-flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">
                <CheckCircle className="w-3 h-3" /> Connected
              </span>
            </div>
            {pool.description && <p className="text-sm text-gray-500 mt-1 ml-6">{pool.description}</p>}
            <p className="text-xs text-gray-400 mt-1 ml-6 font-mono">{pool.id}</p>
          </div>
          <a href="/memory" className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50 whitespace-nowrap flex items-center gap-1">
            <ExternalLink className="w-3 h-3" /> Manage
          </a>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-3 mt-4">
          {[
            { label: 'Total Files', value: files.length, icon: FileText, color: 'text-gray-600 bg-gray-50' },
            { label: 'Raw Text',    value: rawFiles.length, icon: FileSearch, color: 'text-gray-500 bg-gray-50' },
            { label: 'Indexed',     value: indexedFiles.length, icon: Layers, color: 'text-green-700 bg-green-50' },
            { label: 'Vectorized',  value: vectorized.length, icon: Zap, color: 'text-indigo-700 bg-indigo-50' },
          ].map(({ label, value, icon: Icon, color }) => (
            <div key={label} className={`rounded-lg px-3 py-2.5 flex items-center gap-2.5 ${color}`}>
              <Icon className="w-4 h-4 shrink-0" />
              <div>
                <p className="text-lg font-bold leading-none">{value}</p>
                <p className="text-xs mt-0.5 opacity-70">{label}</p>
              </div>
            </div>
          ))}
        </div>

        {totalChunks > 0 && (
          <p className="text-xs text-gray-400 mt-3 flex items-center gap-1">
            <BarChart2 className="w-3 h-3" /> {totalChunks} total chunks across indexed files
          </p>
        )}
      </div>

      {/* File catalog */}
      {files.length === 0 ? (
        <div className="bg-white rounded-xl border border-dashed border-gray-200 p-10 text-center text-gray-400">
          <FileText className="w-10 h-10 mx-auto mb-3 opacity-20" />
          <p className="text-sm">No files in this pool yet. Add files in the <strong>Shared Memory</strong> page.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Filter bar */}
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-gray-700">Data Sources</p>
            <div className="flex gap-1 ml-auto">
              {[
                { id: 'all',        label: `All (${files.length})` },
                { id: 'raw',        label: `Raw (${rawFiles.length})` },
                { id: 'indexed',    label: `Indexed (${indexedFiles.length})` },
                { id: 'vectorized', label: `Vectorized (${vectorized.length})` },
              ].map(btn => (
                <button key={btn.id} onClick={() => setFilter(btn.id)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                    filter === btn.id ? 'bg-indigo-600 text-white' : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50'
                  }`}>
                  {btn.label}
                </button>
              ))}
            </div>
          </div>

          {visible.length === 0 ? (
            <p className="text-center text-sm text-gray-400 py-6">No files in this category.</p>
          ) : (
            <div className="space-y-3">
              {visible.map((f, i) => (
                <MemoryFileCard key={i} file={f} poolId={pool.id} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

const AgentDetails = () => {
  const { id } = useParams();
  const { workspaceFilter, liveUpdates } = useWorkspace();
  const [agent, setAgent] = useState(null);
  const [history, setHistory] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState(null);
  const [selectedLog, setSelectedLog] = useState(null);
  const [logs, setLogs] = useState('');
  const [agentDefinition, setAgentDefinition] = useState({ system_prompt: '', yaml: '', source: '', yaml_path: '' });

  const [memoryType, setMemoryType] = useState('none');
  const [memoryData, setMemoryData] = useState('');
  const memoryDraftDirty = useRef(false);
  const [isUpdatingMemory, setIsUpdatingMemory] = useState(false);
  const [sharedMemories, setSharedMemories] = useState([]);
  const [connectedPool, setConnectedPool] = useState(null);
  const [loadingPool, setLoadingPool] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const [showStartNodeModal, setShowStartNodeModal] = useState(false);
  const [startWorkspace, setStartWorkspace] = useState('');
  const [startLabel, setStartLabel] = useState('');
  const [startingNode, setStartingNode] = useState(false);
  const [nodeBusy, setNodeBusy] = useState({});
  const [logsNode, setLogsNode] = useState(null);
  const [logsNodeText, setLogsNodeText] = useState('');
  const [logsNodeLoading, setLogsNodeLoading] = useState(false);
  const [availableTools, setAvailableTools] = useState([]);
  const [selectedTools, setSelectedTools] = useState([]);
  const [toolsDraftDirty, setToolsDraftDirty] = useState(false);
  const [toolsSaving, setToolsSaving] = useState(false);
  const [toolsMessage, setToolsMessage] = useState('');

  // Model config tab state
  const [modelForm, setModelForm] = useState(EMPTY_MODEL);
  const [modelHasApiKey, setModelHasApiKey] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [modelMessage, setModelMessage] = useState('');
  const [modelShowKey, setModelShowKey] = useState(false);
  const [localModels, setLocalModels] = useState([]);   // fetched model list
  const [localModelsFetching, setLocalModelsFetching] = useState(false);
  const [localModelsError, setLocalModelsError] = useState('');

  const fetchData = async () => {
    try {
      const [agentResp, historyResp, tasksResp, workspacesResp] = await Promise.all([
        getAgent(id),
        getAgentHistory(id),
        getTasks(workspaceFilter),
        getWorkspaces(),
      ]);
      setAgent(agentResp.data);
      setHistory(historyResp.data);
      setTasks(tasksResp.data || []);
      setWorkspaces(workspacesResp.data || []);
      const savedTools = Array.isArray(agentResp.data?.tools)
        ? agentResp.data.tools
        : (Array.isArray(agentResp.data?.capabilities) ? agentResp.data.capabilities : []);
      const paramsTools = Array.isArray(agentResp.data?.default_params?.tools) ? agentResp.data.default_params.tools : [];
      if (!toolsDraftDirty) {
        setSelectedTools([...new Set([...savedTools, ...paramsTools])]);
      }
      if (!memoryDraftDirty.current) {
        setMemoryType(agentResp.data.memory_type || 'none');
        setMemoryData(typeof agentResp.data.memory_data === 'string' ? agentResp.data.memory_data : JSON.stringify(agentResp.data.memory_data || '', null, 2));
      }
      try {
        const nodesResp = await getNodes();
        setNodes((nodesResp.data || []).filter((n) => n.agent_id === id));
      } catch {
        setNodes([]);
      }
      try {
        const definitionResp = await getAgentDefinition(id);
        setAgentDefinition(definitionResp.data || { system_prompt: '', yaml: '', source: '', yaml_path: '' });
      } catch {
        setAgentDefinition({ system_prompt: '', yaml: '', source: '', yaml_path: '' });
      }

      if (agentResp.data.is_remote) {
        try {
          const healthResp = await getAgentHealth(id);
          setHealth(healthResp.data);
        } catch {
          setHealth({ status: 'offline' });
        }
      }
      setLoading(false);
    } catch (error) {
      console.error('Error fetching agent details:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    if (!liveUpdates) return;
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [id, liveUpdates]);

  useEffect(() => {
    setToolsDraftDirty(false);
    setToolsMessage('');
    memoryDraftDirty.current = false;
    setModelForm(EMPTY_MODEL);
    setModelMessage('');
  }, [id]);

  useEffect(() => {
    getAgentModel(id)
      .then(r => {
        const d = r.data;
        setModelHasApiKey(!!d.has_api_key);
        setModelForm({
          provider: d.provider || 'inherit',
          model: d.model || '',
          api_key: '',
          base_url: d.base_url || '',
          temperature: d.temperature != null ? String(d.temperature) : '',
          max_tokens: d.max_tokens != null ? String(d.max_tokens) : '',
        });
      })
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    const fetchTools = async () => {
      try {
        const resp = await getTools();
        const ids = (resp.data?.all || [])
          .map((t) => t.id || t.name)
          .filter(Boolean);
        setAvailableTools([...new Set(ids)].sort());
      } catch {
        setAvailableTools([]);
      }
    };
    fetchTools();
  }, []);

  const handleUpdateMemory = async () => {
    setIsUpdatingMemory(true);
    try {
      let data = memoryData;
      try {
        data = JSON.parse(memoryData);
      } catch {
        // keep as string
      }
      await updateAgentMemory(id, { memory_type: memoryType, memory_data: data });
      memoryDraftDirty.current = false;
      fetchData();
    } catch (error) {
      console.error('Error updating memory:', error);
    } finally {
      setIsUpdatingMemory(false);
    }
  };

  const handleEraseMemory = async () => {
    if (!window.confirm('Are you sure you want to erase agent memory?')) return;
    setIsUpdatingMemory(true);
    try {
      await eraseAgentMemory(id);
      memoryDraftDirty.current = false;
      fetchData();
    } catch (error) {
      console.error('Error erasing memory:', error);
    } finally {
      setIsUpdatingMemory(false);
    }
  };

  // Load shared memory pools when the memory tab opens
  useEffect(() => {
    if (activeTab !== 'memory') return;
    getSharedMemories().then(r => setSharedMemories(r.data || [])).catch(() => {});
  }, [activeTab]);

  // Load full pool details whenever the selected pool ID changes
  useEffect(() => {
    if (memoryType !== 'shared' || !memoryData) { setConnectedPool(null); return; }
    const poolId = memoryData.trim();
    if (!poolId) { setConnectedPool(null); return; }
    setLoadingPool(true);
    getSharedMemory(poolId)
      .then(r => setConnectedPool(r.data))
      .catch(() => setConnectedPool(null))
      .finally(() => setLoadingPool(false));
  }, [memoryType, memoryData]);

  const viewLogs = async (runId) => {
    try {
      setSelectedLog(runId);
      setLogs('Loading logs...');
      const resp = await getLogs(runId);
      setLogs(resp.data.logs);
    } catch {
      setLogs('Could not fetch logs for this run.');
    }
  };

  const handleStartNode = async () => {
    if (agent?.is_remote) return;
    setStartingNode(true);
    try {
      await startNode({ agent_id: id, workspace: startWorkspace || null, label: startLabel || null });
      setShowStartNodeModal(false);
      setStartWorkspace('');
      setStartLabel('');
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || 'Failed to start node');
    } finally {
      setStartingNode(false);
    }
  };

  const handleStopNode = async (nodeId) => {
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'stopping' }));
    try {
      await stopNode(nodeId);
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || 'Failed to stop node');
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const handleDeleteNode = async (nodeId) => {
    if (!window.confirm('Remove this stopped/failed node record?')) return;
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'deleting' }));
    try {
      await deleteNode(nodeId);
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || 'Failed to remove node');
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const openNodeLogs = async (node) => {
    const nodeId = node.node_id || node.id;
    setLogsNode(node);
    setLogsNodeLoading(true);
    setLogsNodeText('');
    try {
      const resp = await getNodeLogs(nodeId);
      setLogsNodeText(resp.data?.logs || '(empty)');
    } catch (error) {
      setLogsNodeText(error.response?.data?.detail || 'Failed to load node logs.');
    } finally {
      setLogsNodeLoading(false);
    }
  };

  const toggleTool = (toolId) => {
    setSelectedTools((prev) => (
      prev.includes(toolId)
        ? prev.filter((t) => t !== toolId)
        : [...prev, toolId]
    ));
    setToolsDraftDirty(true);
    setToolsMessage('');
  };

  const handleSaveTools = async () => {
    setToolsSaving(true);
    setToolsMessage('');
    try {
      await updateAgentTools(id, { tools: selectedTools });
      setToolsMessage('Tools updated');
      setToolsDraftDirty(false);
      await fetchData();
    } catch (error) {
      setToolsMessage(error.response?.data?.detail || 'Failed to update tools');
    } finally {
      setToolsSaving(false);
    }
  };

  const handleFetchLocalModels = async () => {
    const provider = modelForm.provider; // 'ollama' | 'lmstudio'
    const baseUrl = modelForm.base_url ||
      (provider === 'ollama' ? 'http://localhost:11434' : 'http://localhost:1234');
    setLocalModelsFetching(true);
    setLocalModelsError('');
    setLocalModels([]);
    try {
      const { data } = await testLocalModel(provider, baseUrl);
      if (data.ok) {
        setLocalModels(data.models || []);
        if (!data.models?.length) setLocalModelsError('Connected but no models found.');
      } else {
        setLocalModelsError(data.error || 'Connection failed.');
      }
    } catch (e) {
      setLocalModelsError(e.message);
    } finally {
      setLocalModelsFetching(false);
    }
  };

  const handleSaveModel = async () => {
    setModelSaving(true);
    setModelMessage('');
    try {
      const payload = {
        provider: modelForm.provider,
        model: modelForm.model,
        base_url: modelForm.base_url,
      };
      if (modelForm.api_key.trim()) {
        payload.api_key = modelForm.api_key.trim();
      }
      const tempVal = parseFloat(modelForm.temperature);
      if (modelForm.temperature.trim() === '') {
        payload.clear_temperature = true;
      } else if (!isNaN(tempVal)) {
        payload.temperature = tempVal;
      }
      const tokVal = parseInt(modelForm.max_tokens, 10);
      if (modelForm.max_tokens.trim() === '') {
        payload.clear_max_tokens = true;
      } else if (!isNaN(tokVal)) {
        payload.max_tokens = tokVal;
      }
      const resp = await updateAgentModel(id, payload);
      setModelHasApiKey(!!resp.data.has_api_key);
      setModelForm(f => ({ ...f, api_key: '' }));
      setModelMessage('Model settings saved');
      setTimeout(() => setModelMessage(''), 3000);
    } catch (error) {
      setModelMessage(error.response?.data?.detail || 'Failed to save');
    } finally {
      setModelSaving(false);
    }
  };

  if (loading) return <div className="text-center py-10">Loading agent details...</div>;
  if (!agent) return <div className="text-center py-10">Agent not found</div>;

  const activeTask = history.find(r => r.status === 'running');
  const configuredTools = Array.isArray(agent?.default_params?.tools) ? agent.default_params.tools : [];
  const agentTools = Array.isArray(agent?.tools) ? agent.tools : (Array.isArray(agent?.capabilities) ? agent.capabilities : []);
  const mergedTools = [...new Set([...configuredTools, ...agentTools])];
  const visibleToolIds = [...new Set([...availableTools, ...mergedTools, ...selectedTools])];
  const toolsDirty = toolsDraftDirty || ([...selectedTools].sort().join('|') !== [...mergedTools].sort().join('|'));
  const runningNodesCount = nodes.filter((n) => n.status === 'running' || n.status === 'starting').length;
  const agentTasks = (tasks || []).filter((t) => t.assigned_agent_type === id);
  const runningTasks = agentTasks.filter((t) => t.agent_state === 'running');

  return (
    <div>
      <Link to="/agents" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Agents
      </Link>

      <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-indigo-600 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">{agent.name}</h2>
        <p className="text-sm text-gray-500 font-mono mb-4">{agent.id}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-indigo-500 font-semibold">Current Capacity</div>
            <div className="text-sm font-semibold text-indigo-900">{runningTasks.length} / {agent.capacity || 1}</div>
          </div>
          <div className="bg-green-50 border border-green-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-green-500 font-semibold">Running Nodes</div>
            <div className="text-sm font-semibold text-green-900">{runningNodesCount}</div>
          </div>
          <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-amber-500 font-semibold">Running Tasks</div>
            <div className="text-sm font-semibold text-amber-900">{runningTasks.length}</div>
          </div>
        </div>
      </div>

      <div className="mb-6 border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'overview', label: 'Overview', icon: Activity },
            { id: 'history', label: 'Sessions', icon: History },
            { id: 'model', label: 'Model', icon: BrainCircuit },
            { id: 'memory', label: 'Memory', icon: Database },
            { id: 'tools', label: 'Tools', icon: Wrench },
            { id: 'nodes', label: 'Nodes', icon: Server },
            { id: 'tasks', label: 'Tasks', icon: Clock },
            { id: 'config', label: 'Config', icon: FileCode },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
                  isActive
                    ? 'border-indigo-600 text-indigo-700'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {activeTab === 'overview' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="space-y-4">
              <div className="flex items-center justify-between py-2 border-b border-gray-50">
                <span className="text-gray-500 flex items-center"><Server className="w-4 h-4 mr-2" /> Type</span>
                <span className="font-medium capitalize">{agent.is_remote ? 'Remote' : 'Local'}</span>
              </div>

              {agent.is_remote && (
                <div className="py-2 border-b border-gray-50">
                  <span className="text-gray-500 flex items-center mb-1"><ExternalLink className="w-4 h-4 mr-2" /> URL</span>
                  <p className="text-xs font-mono text-indigo-600 break-all">{agent.agent_url}</p>
                </div>
              )}

              <div className="flex items-center justify-between py-2 border-b border-gray-50">
                <span className="text-gray-500 flex items-center"><Activity className="w-4 h-4 mr-2" /> Capacity</span>
                <span className="font-medium">{agent.capacity} concurrent runs</span>
              </div>

              {agent.original_id && (
                <div className="flex items-center justify-between py-2 border-b border-gray-50">
                  <span className="text-gray-500 flex items-center"><History className="w-4 h-4 mr-2" /> Cloned From</span>
                  <Link to={`/agents/${agent.original_id}`} className="text-indigo-600 hover:underline font-medium">{agent.original_id}</Link>
                </div>
              )}

              <div className="py-2">
                <span className="text-gray-500 flex items-center mb-2"><Wrench className="w-4 h-4 mr-2" /> Tools</span>
                <div className="flex flex-wrap gap-2">
                  {agentTools.length > 0 ? agentTools.map(tool => (
                    <span key={tool} className="text-xs bg-gray-100 px-2 py-1 rounded text-gray-600">{tool}</span>
                  )) : <span className="text-xs text-gray-400 italic">None configured</span>}
                </div>
              </div>
            </div>
          </div>

          {/* Model & Parameters card */}
          {(() => {
            const dp = agent.default_params || {};
            const provider = dp.provider && dp.provider !== 'inherit' ? dp.provider : null;
            const hasModelConfig = provider || dp.model || dp.base_url || dp.temperature != null || dp.max_tokens != null || dp.api_key;
            if (!hasModelConfig) return null;
            const providerColors = {
              openai:    'bg-green-100 text-green-700',
              anthropic: 'bg-orange-100 text-orange-700',
              google:    'bg-blue-100 text-blue-700',
              ollama:    'bg-purple-100 text-purple-700',
              lmstudio:  'bg-pink-100 text-pink-700',
            };
            return (
              <div className="bg-white p-6 shadow-md rounded-lg">
                <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
                  <BrainCircuit className="w-4 h-4 text-indigo-500" /> Model Configuration
                  <button
                    type="button"
                    onClick={() => setActiveTab('model')}
                    className="ml-auto text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50"
                  >
                    Edit
                  </button>
                </h3>
                <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                  {provider && (
                    <div className="col-span-2 flex items-center gap-2">
                      <span className="text-gray-500 w-28 shrink-0">Provider</span>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold capitalize ${providerColors[provider] || 'bg-gray-100 text-gray-700'}`}>
                        {provider}
                      </span>
                    </div>
                  )}
                  {dp.model && (
                    <div className="col-span-2 flex items-center gap-2">
                      <span className="text-gray-500 w-28 shrink-0">Model</span>
                      <span className="font-mono text-xs bg-gray-100 px-2 py-0.5 rounded">{dp.model}</span>
                    </div>
                  )}
                  {dp.base_url && (
                    <div className="col-span-2 flex items-center gap-2 min-w-0">
                      <span className="text-gray-500 w-28 shrink-0">Base URL</span>
                      <span className="font-mono text-xs text-indigo-600 truncate">{dp.base_url}</span>
                    </div>
                  )}
                  {dp.temperature != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500">Temperature</span>
                      <span className="font-mono text-xs bg-gray-100 px-2 py-0.5 rounded">{dp.temperature}</span>
                    </div>
                  )}
                  {dp.max_tokens != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500">Max Tokens</span>
                      <span className="font-mono text-xs bg-gray-100 px-2 py-0.5 rounded">{dp.max_tokens.toLocaleString()}</span>
                    </div>
                  )}
                  {dp.api_key && (
                    <div className="col-span-2 flex items-center gap-2">
                      <span className="text-gray-500 w-28 shrink-0">API Key</span>
                      <span className="flex items-center gap-1 text-xs text-green-700">
                        <CheckCircle className="w-3 h-3" /> Custom key stored
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          })()}

          {agent.is_remote && health && (
            <div className={`p-4 rounded-lg shadow-sm border ${health.status === 'up' ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
              <h3 className={`font-bold flex items-center ${health.status === 'up' ? 'text-green-800' : 'text-red-800'}`}>
                <Activity className="w-4 h-4 mr-2" /> Health Status: {health.status.toUpperCase()}
              </h3>
              {health.details && <pre className="text-[10px] mt-2 overflow-auto max-h-20">{JSON.stringify(health.details, null, 2)}</pre>}
              {health.error && <p className="text-xs text-red-600 mt-1">{health.error}</p>}
            </div>
          )}

          {activeTask && (
            <div className="bg-indigo-50 p-4 rounded-lg border border-indigo-100">
              <h3 className="text-indigo-800 font-bold flex items-center mb-2">
                <Clock className="w-4 h-4 mr-2" /> Currently Active
              </h3>
              <p className="text-sm text-indigo-900 font-medium truncate mb-2">Task ID: {activeTask.task_id}</p>
              <Link to={`/tasks/${activeTask.task_id}`} className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 inline-block">
                View Task Details
              </Link>
            </div>
          )}
        </div>
      )}

      {activeTab === 'history' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <History className="w-5 h-5 mr-2" /> Execution History
            </h3>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Task ID</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Started At</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {history.length === 0 ? (
                    <tr>
                      <td colSpan="4" className="px-4 py-8 text-center text-gray-500 italic">No execution history found for this agent.</td>
                    </tr>
                  ) : (
                    history.map((run) => (
                      <tr key={run.run_id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 whitespace-nowrap">
                          <div className="flex items-center">
                            {run.status === 'completed' && <CheckCircle className="w-4 h-4 text-green-500 mr-2" />}
                            {run.status === 'failed' && <AlertCircle className="w-4 h-4 text-red-500 mr-2" />}
                            {run.status === 'running' && <Clock className="w-4 h-4 text-blue-500 mr-2 animate-spin" />}
                            <span className="text-sm capitalize">{run.status}</span>
                          </div>
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-sm font-mono text-gray-600">
                          {String(run.task_id || '').slice(0, 8)}...
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs text-gray-500">
                          {new Date(run.started_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-right">
                          <button
                            onClick={() => viewLogs(run.run_id)}
                            className="text-indigo-600 hover:text-indigo-900 text-xs font-medium flex items-center justify-end"
                          >
                            <Terminal className="w-3 h-3 mr-1" /> Logs
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {selectedLog && (
            <div className="bg-gray-900 rounded-lg shadow-md overflow-hidden flex flex-col h-[500px]">
              <div className="bg-gray-800 px-4 py-2 flex items-center justify-between border-b border-gray-700">
                <div className="flex items-center text-gray-300 text-sm font-medium">
                  <Terminal className="w-4 h-4 mr-2" /> Run Logs: {selectedLog.slice(0, 8)}
                </div>
                <button onClick={() => setSelectedLog(null)} className="text-gray-400 hover:text-white">&times;</button>
              </div>
              <div className="p-4 flex-1 overflow-auto font-mono text-xs text-green-400 bg-black">
                <pre className="whitespace-pre-wrap">{logs}</pre>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'memory' && (
        <div className="space-y-5">
          {/* ── Configuration card ── */}
          <div className="bg-white rounded-xl border border-t-4 border-t-amber-500 border-gray-200 p-6 shadow-sm">
            <h3 className="text-base font-bold text-gray-900 mb-4 flex items-center gap-2">
              <Database className="w-5 h-5 text-amber-500" /> Memory Configuration
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Memory Type</label>
                <select
                  value={memoryType}
                  onChange={(e) => { setMemoryType(e.target.value); memoryDraftDirty.current = true; if (e.target.value !== 'shared') setConnectedPool(null); }}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="none">None</option>
                  <option value="local">Local (Agent-specific)</option>
                  <option value="shared">Shared Memory Pool</option>
                </select>
              </div>

              {memoryType === 'shared' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Connected Pool</label>
                  {sharedMemories.length > 0 ? (
                    <select
                      value={memoryData}
                      onChange={(e) => { setMemoryData(e.target.value); memoryDraftDirty.current = true; }}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    >
                      <option value="">— Select a memory pool —</option>
                      {sharedMemories.map(m => (
                        <option key={m.id} value={m.id}>
                          {m.name}  ({(m.files || []).length} files)
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={memoryData}
                      onChange={(e) => { setMemoryData(e.target.value); memoryDraftDirty.current = true; }}
                      placeholder="Shared Memory Pool ID (UUID)"
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  )}
                  {memoryData && (
                    <p className="text-xs text-gray-400 mt-1 font-mono truncate">ID: {memoryData}</p>
                  )}
                </div>
              )}

              {memoryType === 'local' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Memory Content</label>
                  <textarea
                    value={memoryData}
                    onChange={(e) => { setMemoryData(e.target.value); memoryDraftDirty.current = true; }}
                    placeholder="Enter memory content or configuration"
                    rows={5}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              )}

              <div className="flex gap-2 pt-1">
                <button
                  onClick={handleUpdateMemory}
                  disabled={isUpdatingMemory}
                  className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center justify-center gap-2 disabled:opacity-50"
                >
                  {isUpdatingMemory ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {isUpdatingMemory ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={handleEraseMemory}
                  disabled={isUpdatingMemory || agent.memory_type === 'none'}
                  className="bg-red-50 text-red-600 px-4 py-2 rounded-lg text-sm font-medium hover:bg-red-100 flex items-center justify-center gap-2 disabled:opacity-50 border border-red-200"
                >
                  <Trash2 className="w-4 h-4" /> Erase
                </button>
              </div>
            </div>
          </div>

          {/* ── Connected pool details ── */}
          {memoryType === 'shared' && (
            loadingPool ? (
              <div className="flex items-center justify-center h-32 bg-white rounded-xl border border-gray-200">
                <Loader className="w-5 h-5 animate-spin text-indigo-400 mr-2" />
                <span className="text-sm text-gray-500">Loading pool…</span>
              </div>
            ) : connectedPool ? (
              <MemoryPoolDetails pool={connectedPool} />
            ) : memoryData ? (
              <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 shrink-0" />
                Pool not found. Check the ID or create a pool in <strong>Shared Memory</strong>.
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center bg-white rounded-xl border border-dashed border-gray-200 p-10 text-center text-gray-400">
                <Database className="w-10 h-10 mb-3 opacity-20" />
                <p className="text-sm">Select a shared memory pool above to browse its data sources.</p>
              </div>
            )
          )}
        </div>
      )}

      {activeTab === 'tools' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold flex items-center">
                <Wrench className="w-5 h-5 mr-2 text-indigo-600" />
                Tools
              </h3>
              <button
                type="button"
                onClick={handleSaveTools}
                disabled={toolsSaving || !toolsDirty}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
              >
                {toolsSaving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
                Save Tools
              </button>
            </div>
            {toolsMessage && (
              <div className={`text-xs mb-3 ${toolsMessage === 'Tools updated' ? 'text-green-600' : 'text-red-600'}`}>
                {toolsMessage}
              </div>
            )}
            {visibleToolIds.length ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {visibleToolIds.map((tool) => {
                  const enabled = selectedTools.includes(tool);
                  return (
                    <div key={tool} className="p-3 border border-gray-100 rounded-lg bg-gray-50 flex items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-gray-800">{tool}</div>
                        <div className="text-xs text-gray-500 mt-1 font-mono">source: {configuredTools.includes(tool) ? 'default_params.tools' : 'tools'}</div>
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleTool(tool)}
                        className={`px-2.5 py-1 rounded-full text-xs font-semibold border ${
                          enabled ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'
                        }`}
                      >
                        {enabled ? 'On' : 'Off'}
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-gray-500 italic">No tools configured for this agent.</p>
            )}
            <p className="text-xs text-gray-500 mt-3">
              Toggle tools on/off, then click <span className="font-semibold">Save Tools</span> to apply changes.
            </p>
          </div>

          {agent.default_params && Object.keys(agent.default_params).length > 0 && (
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h4 className="text-md font-bold mb-3 text-gray-800">Default Params</h4>
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto">
                {JSON.stringify(agent.default_params, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {activeTab === 'nodes' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" />
              Agent Nodes
            </h3>
            {!agent.is_remote && (
              <button
                type="button"
                onClick={() => setShowStartNodeModal(true)}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700"
              >
                <Play className="w-3.5 h-3.5 mr-1" />
                Start Node
              </button>
            )}
          </div>
          {agent.is_remote && (
            <p className="text-xs text-gray-500 mb-3">Remote agents do not support local node start/stop controls.</p>
          )}
          {nodes.length === 0 ? (
            <p className="text-sm text-gray-500 italic">No nodes found for this agent.</p>
          ) : (
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Node ID</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Label</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Workspace</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Started</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Uptime</th>
                    <th className="text-right px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {nodes.map((node) => {
                    const nodeId = node.node_id || node.id;
                    const isActive = node.status === 'running' || node.status === 'starting';
                    const busy = nodeBusy[nodeId];
                    return (
                      <tr key={nodeId} className="hover:bg-gray-50 transition-colors">
                        <td className="px-5 py-3">
                          <NodeStatusBadge status={node.status} />
                        </td>
                        <td className="px-5 py-3">
                          <span className="font-mono text-xs text-gray-600">
                            {String(nodeId).slice(0, 8)}
                            <span className="text-gray-400">…</span>
                          </span>
                        </td>
                        <td className="px-5 py-3 text-gray-600 text-xs">{node.label || '—'}</td>
                        <td className="px-5 py-3">
                          {node.workspace
                            ? <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded font-mono">{node.workspace}</span>
                            : <span className="text-gray-400 text-xs">—</span>}
                        </td>
                        <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{fmtNodeDate(node.started_at)}</td>
                        <td className="px-5 py-3 text-gray-600 text-xs font-mono whitespace-nowrap">
                          {nodeUptime(node.started_at, node.finished_at)}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => openNodeLogs(node)}
                              title="View logs"
                              className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                            >
                              <FileText className="w-4 h-4" />
                            </button>
                            {isActive ? (
                              <button
                                type="button"
                                onClick={() => handleStopNode(nodeId)}
                                disabled={!!busy}
                                title="Stop node"
                                className="p-1.5 rounded text-gray-400 hover:text-orange-600 hover:bg-orange-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'stopping' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                              </button>
                            ) : (
                              <button
                                type="button"
                                onClick={() => handleDeleteNode(nodeId)}
                                disabled={!!busy}
                                title="Remove record"
                                className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'deleting' ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === 'tasks' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4 flex items-center">
            <Clock className="w-5 h-5 mr-2 text-indigo-600" />
            Tasks Assigned To This Agent
          </h3>
          {agentTasks.length === 0 ? (
            <p className="text-sm text-gray-500 italic">No tasks assigned to this agent yet.</p>
          ) : (
            <div className="space-y-3">
              {agentTasks
                .slice()
                .sort((a, b) => {
                  const aRunning = a.agent_state === 'running' ? 1 : 0;
                  const bRunning = b.agent_state === 'running' ? 1 : 0;
                  if (aRunning !== bRunning) return bRunning - aRunning;
                  const aTs = new Date(a.created_at || 0).getTime() || 0;
                  const bTs = new Date(b.created_at || 0).getTime() || 0;
                  return bTs - aTs;
                })
                .map((task) => (
                  <div key={task.id} className="border border-gray-100 rounded-lg p-3 bg-gray-50">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link to={`/tasks/${task.id}`} className="text-sm font-semibold text-indigo-700 hover:underline truncate block">
                          {task.title || task.id}
                        </Link>
                        <div className="text-xs text-gray-500 font-mono mt-1 truncate">{task.id}</div>
                        <div className="text-xs text-gray-500 mt-1">Workspace: <span className="font-mono">{task.workspace || '—'}</span></div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold capitalize ${
                          task.agent_state === 'running'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-200 text-gray-700'
                        }`}>
                          agent: {task.agent_state || 'unknown'}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold capitalize ${
                          task.status === 'done'
                            ? 'bg-green-100 text-green-700'
                            : task.status === 'blocked'
                              ? 'bg-red-100 text-red-700'
                              : 'bg-gray-200 text-gray-700'
                        }`}>
                          task: {task.status || 'unknown'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'config' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Activity className="w-5 h-5 mr-2 text-indigo-600" />
              Agent Metadata
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div><span className="text-gray-500">ID:</span> <span className="font-mono">{agent.id}</span></div>
              <div><span className="text-gray-500">Name:</span> <span className="font-medium">{agent.name}</span></div>
              <div><span className="text-gray-500">Type:</span> <span className="font-mono">{agent.type}</span></div>
              <div><span className="text-gray-500">Domain:</span> <span className="font-mono">{agent.domain || 'general'}</span></div>
              <div><span className="text-gray-500">Entrypoint:</span> <span className="font-mono break-all">{agent.entrypoint}</span></div>
              <div><span className="text-gray-500">Capacity:</span> <span className="font-mono">{agent.capacity}</span></div>
              <div><span className="text-gray-500">Memory Type:</span> <span className="font-mono">{agent.memory_type || 'none'}</span></div>
              <div><span className="text-gray-500">Definition Source:</span> <span className="font-mono">{agentDefinition.source || '—'}</span></div>
            </div>
            {agentDefinition.yaml_path && (
              <p className="text-xs text-gray-500 mt-3 font-mono break-all">YAML path: {agentDefinition.yaml_path}</p>
            )}
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Terminal className="w-5 h-5 mr-2 text-indigo-600" />
              System Prompt
            </h3>
            {agentDefinition.system_prompt ? (
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap">
                {agentDefinition.system_prompt}
              </pre>
            ) : (
              <p className="text-sm text-gray-500 italic">No system prompt available for this agent.</p>
            )}
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <FileCode className="w-5 h-5 mr-2 text-indigo-600" />
              YAML Definition
            </h3>
            {agentDefinition.yaml ? (
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap">
                {agentDefinition.yaml}
              </pre>
            ) : (
              <p className="text-sm text-gray-500 italic">No YAML definition available.</p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'model' && (
        <div className="space-y-6">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                <BrainCircuit className="w-5 h-5 text-indigo-600" /> Model & Access Settings
              </h3>
              <p className="text-sm text-gray-500 mt-1">
                Override the global model settings for this agent. Leave fields blank to inherit from global settings.
              </p>
            </div>
            <button
              onClick={handleSaveModel}
              disabled={modelSaving}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
            >
              {modelSaving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {modelSaving ? 'Saving…' : 'Save'}
            </button>
          </div>

          {modelMessage && (
            <div className={`rounded-lg px-4 py-2 text-sm ${modelMessage.includes('saved') ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
              {modelMessage}
            </div>
          )}

          {/* Provider */}
          <div className="bg-white p-6 shadow-md rounded-lg space-y-5">
            <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">Provider</h4>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {[
                { value: 'inherit',   label: 'Inherit',    sub: 'global' },
                { value: 'openai',    label: 'OpenAI',     sub: 'Cloud' },
                { value: 'anthropic', label: 'Anthropic',  sub: 'Claude' },
                { value: 'google',    label: 'Google',     sub: 'Gemini' },
                { value: 'ollama',    label: 'Ollama',     sub: 'Local' },
                { value: 'lmstudio', label: 'LM Studio',  sub: 'Local' },
              ].map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => { setModelForm(f => ({ ...f, provider: opt.value })); setLocalModels([]); setLocalModelsError(''); }}
                  className={`flex flex-col items-center gap-0.5 px-3 py-3 rounded-xl border-2 text-sm font-semibold transition-colors ${
                    modelForm.provider === opt.value
                      ? 'border-indigo-600 bg-indigo-50 text-indigo-700'
                      : 'border-gray-200 text-gray-600 hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  {opt.label}
                  <span className="text-[10px] font-normal text-gray-400">{opt.sub}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Model name */}
          <div className="bg-white p-6 shadow-md rounded-lg space-y-5">
            <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">Model</h4>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model name</label>
              <p className="text-xs text-gray-500 mb-2">
                {modelForm.provider === 'inherit' && 'Inheriting model from global settings.'}
                {modelForm.provider === 'openai' && 'e.g. gpt-4o, gpt-4o-mini, gpt-4-turbo'}
                {modelForm.provider === 'anthropic' && 'e.g. claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5-20251001'}
                {modelForm.provider === 'google' && 'e.g. gemini-2.0-flash, gemini-1.5-pro'}
                {modelForm.provider === 'ollama' && 'e.g. llama3, mistral, phi3 (must be pulled via ollama pull)'}
                {modelForm.provider === 'lmstudio' && 'Model identifier shown in LM Studio'}
              </p>
              {(modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && localModels.length > 0 ? (
                <select
                  value={modelForm.model}
                  onChange={e => setModelForm(f => ({ ...f, model: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  <option value="">— select a model —</option>
                  {localModels.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input
                  type="text"
                  value={modelForm.model}
                  onChange={e => setModelForm(f => ({ ...f, model: e.target.value }))}
                  placeholder={modelForm.provider === 'inherit' ? '(inheriting from global settings)' : 'Enter model name…'}
                  disabled={modelForm.provider === 'inherit'}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none disabled:opacity-50 disabled:bg-gray-50"
                />
              )}
              {localModelsError && (
                <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{localModelsError}</p>
              )}
            </div>

            {/* Base URL */}
            {(modelForm.provider === 'openai' || modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Base URL override</label>
                <p className="text-xs text-gray-500 mb-2">
                  {modelForm.provider === 'openai' && 'Leave blank for api.openai.com. Use for Azure or compatible proxies.'}
                  {modelForm.provider === 'ollama' && 'Ollama server address (default: http://localhost:11434)'}
                  {modelForm.provider === 'lmstudio' && 'LM Studio server address (default: http://localhost:1234)'}
                </p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={modelForm.base_url}
                    onChange={e => { setModelForm(f => ({ ...f, base_url: e.target.value })); setLocalModels([]); setLocalModelsError(''); }}
                    placeholder={
                      modelForm.provider === 'ollama' ? 'http://localhost:11434' :
                      modelForm.provider === 'lmstudio' ? 'http://localhost:1234' :
                      'https://api.openai.com/v1'
                    }
                    className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                  />
                  {(modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && (
                    <button
                      type="button"
                      onClick={handleFetchLocalModels}
                      disabled={localModelsFetching}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap"
                    >
                      {localModelsFetching
                        ? <Loader className="w-4 h-4 animate-spin" />
                        : <Wifi className="w-4 h-4" />}
                      Fetch models
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Temperature override</label>
                <input
                  type="number"
                  value={modelForm.temperature}
                  onChange={e => setModelForm(f => ({ ...f, temperature: e.target.value }))}
                  placeholder="(inherit global)"
                  min="0" max="2" step="0.05"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <p className="text-xs text-gray-400 mt-1">Clear to inherit from global settings</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Max tokens override</label>
                <input
                  type="number"
                  value={modelForm.max_tokens}
                  onChange={e => setModelForm(f => ({ ...f, max_tokens: e.target.value }))}
                  placeholder="(inherit global)"
                  min="256" step="256"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <p className="text-xs text-gray-400 mt-1">Clear to inherit from global settings</p>
              </div>
            </div>
          </div>

          {/* API Key override */}
          {(modelForm.provider !== 'inherit' && modelForm.provider !== 'ollama' && modelForm.provider !== 'lmstudio') && (
            <div className="bg-white p-6 shadow-md rounded-lg space-y-4">
              <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">API Key Override</h4>
              <p className="text-sm text-gray-500">
                Optionally store a per-agent API key. This overrides the key from global settings for this agent only.
                {modelHasApiKey && <span className="ml-1 text-green-600 font-medium">A key is currently stored.</span>}
              </p>
              <div className="relative">
                <input
                  type={modelShowKey ? 'text' : 'password'}
                  value={modelForm.api_key}
                  onChange={e => setModelForm(f => ({ ...f, api_key: e.target.value }))}
                  placeholder={modelHasApiKey ? '(key stored — enter new to replace)' : 'Enter API key…'}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setModelShowKey(s => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {modelShowKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {modelHasApiKey && (
                <button
                  type="button"
                  onClick={() => {
                    updateAgentModel(id, { clear_api_key: true })
                      .then(r => { setModelHasApiKey(!!r.data.has_api_key); setModelMessage('API key removed'); setTimeout(() => setModelMessage(''), 3000); })
                      .catch(() => setModelMessage('Failed to remove key'));
                  }}
                  className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1"
                >
                  <Trash2 className="w-3.5 h-3.5" /> Remove stored key
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {showStartNodeModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
            <div className="flex items-center justify-between p-5 border-b">
              <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                <Play className="w-4 h-4 text-indigo-600" />
                Start Node
              </h2>
              <button onClick={() => setShowStartNodeModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  Workspace
                </label>
                <select
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  value={startWorkspace}
                  onChange={(e) => setStartWorkspace(e.target.value)}
                >
                  <option value="">— None —</option>
                  {workspaces.map((ws) => (
                    <option key={ws.name} value={ws.name}>{ws.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  Label <span className="text-gray-400 font-normal normal-case">(optional)</span>
                </label>
                <input
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder="e.g. dev-worker"
                  value={startLabel}
                  onChange={(e) => setStartLabel(e.target.value)}
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowStartNodeModal(false)}
                  className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200"
                  disabled={startingNode}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={startingNode}
                  onClick={handleStartNode}
                  className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  {startingNode ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  {startingNode ? 'Starting…' : 'Start Node'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {logsNode && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <div className="flex items-center gap-3">
                <NodeStatusBadge status={logsNode.status} />
                <span className="text-gray-200 font-mono text-sm font-semibold">
                  {logsNode.agent_name || logsNode.agent_id || agent?.name || id}
                </span>
                <span className="text-gray-500 font-mono text-xs">
                  {(logsNode.node_id || logsNode.id || '').toString().slice(0, 12)}…
                </span>
              </div>
              <button onClick={() => setLogsNode(null)} className="text-gray-500 hover:text-gray-300 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              {logsNodeLoading ? (
                <div className="flex justify-center py-12">
                  <Loader className="w-5 h-5 animate-spin text-indigo-400" />
                </div>
              ) : (
                <pre className="text-xs font-mono text-green-400 whitespace-pre-wrap break-words leading-5">{logsNodeText}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentDetails;
