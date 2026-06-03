import { useState, useEffect, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import { ChevronLeft, Activity, History, Server, Wrench, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2, FileCode, Play, Square, Loader, X, FileText, BrainCircuit, Eye, EyeOff, Link2, Layers, Hash, Copy, FileSearch, Zap, BarChart2, Wifi, MessageSquare, BookOpen, Plus, ChevronDown, ChevronUp, Tag, Globe, Lock } from 'lucide-react';
import { getAgent, getAgentHistory, getAgentHealth, getAgentLogs, updateAgentMemory, eraseAgentMemory, updateAgentTools, getAgentModel, updateAgentModel, getAgentReasoning, updateAgentReasoning, getNodes, getAgentDefinition, updateAgentDefinition, getTasks, getTools, startNode, stopNode, deleteNode, getWorkspaces, getNodeLogs, getSharedMemories, getSharedMemory, testLocalModel, getAgentWorkspaceCapacities, setWorkspaceAgentCapacity, removeWorkspaceAgentCapacity, getDockerfile, buildBaseImage, buildAgentImage, getContainerImages, getContainers, getContainerLogs, stopContainerByName, removeContainer, setDefaultChatAgent, clearDefaultChatAgent, updateAgentSkillsConfig, getAgentSkills, createAgentSkill, deleteAgentSkill, updateAgentSharing } from '../api';

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
            {ext && <span className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded uppercase">{ext}</span>}
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
            <pre className="text-xs text-green-300 overflow-x-auto flex-1">{toolArgs}</pre>
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
            {file.vector_db_collection && <div><span className="text-gray-500">Collection:</span> <span className="font-medium text-gray-800">{file.vector_db_collection}</span></div>}
            {file.embedding_model && <div><span className="text-gray-500">Model:</span> <span className="font-medium text-gray-800">{file.embedding_model}</span></div>}
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
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
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
            <p className="text-xs text-gray-400 mt-1 ml-6">{pool.id}</p>
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
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [agent, setAgent] = useState(null);
  const [history, setHistory] = useState([]);
  const [agentLogsData, setAgentLogsData] = useState({ runs: [], nodes: [] });
  const [tasks, setTasks] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState(null);

  const [agentDefinition, setAgentDefinition] = useState({ system_prompt: '', instructions: '', capabilities: '', usage: '', source: '', definition_dir: '' });
  const [defDraft, setDefDraft] = useState({ instructions: '', capabilities: '', usage: '' });
  const defDraftDirty = useRef({ instructions: false, capabilities: false, usage: false });
  const [defSaving, setDefSaving] = useState({ instructions: false, capabilities: false, usage: false });
  const [defError, setDefError] = useState({ instructions: '', capabilities: '', usage: '' });

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
  const toolsDraftDirty = useRef(false);
  const [toolsSaving, setToolsSaving] = useState(false);
  const [toolsMessage, setToolsMessage] = useState('');

  // Reasoning capability settings (think / plan) — persisted to localStorage per agent
  const REASONING_TOOLS = ['think', 'plan'];
  const MEMORY_TOOLS = ['read_memory', 'write_memory'];
  const THINK_MODES = [
    { value: 'standard', label: 'Standard', desc: 'Reason before and after key actions' },
    { value: 'deep', label: 'Deep', desc: 'Reason extensively at every step' },
    { value: 'analytical', label: 'Analytical', desc: 'Focus on error diagnosis and logic checking' },
  ];
  const PLAN_FORMATS = [
    { value: 'structured', label: 'Structured', desc: 'Sections with headers and sub-steps' },
    { value: 'bullet', label: 'Bullet List', desc: 'Flat list of action items' },
    { value: 'numbered', label: 'Numbered Steps', desc: 'Ordered numbered checklist' },
    { value: 'freeform', label: 'Free-form', desc: 'Unstructured narrative plan' },
  ];
  const defaultReasoningSettings = { thinkEnabled: false, thinkMode: 'standard', planEnabled: false, planFormat: 'structured' };
  const [reasoningSettings, setReasoningSettings] = useState(defaultReasoningSettings);

  // Default chat agent state
  const [isDefaultChat, setIsDefaultChat] = useState(false);
  const [defaultChatSaving, setDefaultChatSaving] = useState(false);
  const [defaultChatMessage, setDefaultChatMessage] = useState('');

  // Workspace sharing (exposure) state
  const [shared, setShared] = useState(false);
  const [sharingSaving, setSharingSaving] = useState(false);
  const [sharingMessage, setSharingMessage] = useState('');

  // Workspace capacity overrides state
  const [wsCapacities, setWsCapacities] = useState({});
  const [wsCapacityEdits, setWsCapacityEdits] = useState({});
  const [wsCapacitySaving, setWsCapacitySaving] = useState(null);

  // Model config tab state
  const [modelForm, setModelForm] = useState(EMPTY_MODEL);
  const [modelHasApiKey, setModelHasApiKey] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [modelMessage, setModelMessage] = useState('');
  const [modelShowKey, setModelShowKey] = useState(false);
  const [localModels, setLocalModels] = useState([]);   // fetched model list
  const [localModelsFetching, setLocalModelsFetching] = useState(false);
  const [localModelsError, setLocalModelsError] = useState('');

  // Docker tab state
  const [dockerfileContent, setDockerfileContent] = useState('');
  const [dockerfileLoading, setDockerfileLoading] = useState(false);
  const [dockerImages, setDockerImages] = useState([]);
  const [dockerContainers, setDockerContainers] = useState([]);
  const [dockerLoading, setDockerLoading] = useState(false);
  const [buildingBase, setBuildingBase] = useState(false);
  const [buildingAgent, setBuildingAgent] = useState(false);
  const [buildLog, setBuildLog] = useState('');
  const [buildError, setBuildError] = useState('');
  const [containerLogsName, setContainerLogsName] = useState(null);
  const [containerLogsText, setContainerLogsText] = useState('');
  const [containerLogsLoading, setContainerLogsLoading] = useState(false);
  const [dockerActionBusy, setDockerActionBusy] = useState({});

  // ── Skills state ────────────────────────────────────────────────────────────
  const [skills, setSkills] = useState([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsEnabled, setSkillsEnabled] = useState(false);
  const [skillsConfigSaving, setSkillsConfigSaving] = useState(false);
  const [showAddSkill, setShowAddSkill] = useState(false);
  const [skillForm, setSkillForm] = useState({ name: '', description: '', steps: '', tags: '' });
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillDeleteBusy, setSkillDeleteBusy] = useState({});
  const [skillsMessage, setSkillsMessage] = useState('');

  const fetchSkills = async (wsName) => {
    if (!wsName) return;
    setSkillsLoading(true);
    try {
      const resp = await getAgentSkills(id, wsName);
      setSkills(resp.data || []);
    } catch (_) {
      setSkills([]);
    }
    setSkillsLoading(false);
  };

  const handleToggleSkillsEnabled = async (enabled) => {
    setSkillsConfigSaving(true);
    try {
      await updateAgentSkillsConfig(id, { skills_enabled: enabled });
      setSkillsEnabled(enabled);
    } catch (_) {}
    setSkillsConfigSaving(false);
  };

  const handleSaveSkill = async () => {
    const wsName = selectedWorkspace;
    if (!wsName || !skillForm.name || !skillForm.description || !skillForm.steps.trim()) return;
    setSkillSaving(true);
    try {
      const steps = skillForm.steps.split('\n').map(s => s.trim()).filter(Boolean);
      const tags = skillForm.tags ? skillForm.tags.split(',').map(t => t.trim()).filter(Boolean) : [];
      await createAgentSkill(id, { workspace: wsName, name: skillForm.name, description: skillForm.description, steps, tags });
      setSkillForm({ name: '', description: '', steps: '', tags: '' });
      setShowAddSkill(false);
      await fetchSkills(wsName);
      setSkillsMessage('Skill saved.');
      setTimeout(() => setSkillsMessage(''), 3000);
    } catch (_) {}
    setSkillSaving(false);
  };

  const handleDeleteSkill = async (skillId) => {
    const wsName = selectedWorkspace;
    if (!wsName) return;
    setSkillDeleteBusy(b => ({ ...b, [skillId]: true }));
    try {
      await deleteAgentSkill(id, skillId, wsName);
      setSkills(prev => prev.filter(s => s.id !== skillId));
    } catch (_) {}
    setSkillDeleteBusy(b => ({ ...b, [skillId]: false }));
  };

  const fetchDockerData = async () => {
    setDockerLoading(true);
    try {
      const [imagesResp, containersResp] = await Promise.all([
        getContainerImages(),
        getContainers(),
      ]);
      setDockerImages(imagesResp.data?.images || []);
      const allContainers = containersResp.data?.containers || [];
      setDockerContainers(allContainers.filter(c => c.agent_id === id || c.name?.includes(id)));
    } catch (_) {}
    setDockerLoading(false);
  };

  const fetchDockerfile = async () => {
    setDockerfileLoading(true);
    try {
      const resp = await getDockerfile(id);
      setDockerfileContent(typeof resp.data === 'string' ? resp.data : resp.data);
    } catch (_) {
      setDockerfileContent('');
    }
    setDockerfileLoading(false);
  };

  const handleBuildBase = async () => {
    setBuildingBase(true);
    setBuildLog('');
    setBuildError('');
    try {
      const resp = await buildBaseImage({ no_cache: false });
      setBuildLog(resp.data?.log || 'Build complete.');
    } catch (e) {
      setBuildError(e.response?.data?.detail || e.message || 'Build failed');
    } finally {
      setBuildingBase(false);
      fetchDockerData();
    }
  };

  const handleBuildAgent = async () => {
    setBuildingAgent(true);
    setBuildLog('');
    setBuildError('');
    try {
      const resp = await buildAgentImage(id, { no_cache: false });
      setBuildLog(resp.data?.log || 'Build complete.');
    } catch (e) {
      setBuildError(e.response?.data?.detail || e.message || 'Build failed');
    } finally {
      setBuildingAgent(false);
      fetchDockerData();
    }
  };

  const handleShowContainerLogs = async (name) => {
    setContainerLogsName(name);
    setContainerLogsLoading(true);
    setContainerLogsText('');
    try {
      const resp = await getContainerLogs(name, 300);
      setContainerLogsText(typeof resp.data === 'string' ? resp.data : '');
    } catch (e) {
      setContainerLogsText(`[error: ${e.message}]`);
    }
    setContainerLogsLoading(false);
  };

  const handleStopContainer = async (name) => {
    setDockerActionBusy(b => ({ ...b, [name]: true }));
    try {
      await stopContainerByName(name);
      fetchDockerData();
    } catch (_) {}
    setDockerActionBusy(b => ({ ...b, [name]: false }));
  };

  const handleRemoveContainer = async (name) => {
    setDockerActionBusy(b => ({ ...b, [name]: true }));
    try {
      await removeContainer(name);
      fetchDockerData();
    } catch (_) {}
    setDockerActionBusy(b => ({ ...b, [name]: false }));
  };

  const fetchData = async () => {
    try {
      const defaultChatWorkspace = selectedWorkspace || 'default';
      const [agentResp, historyResp, tasksResp, workspacesResp, wsCapResp, logsResp] = await Promise.all([
        getAgent(id, defaultChatWorkspace),
        getAgentHistory(id),
        getTasks(workspaceFilter),
        getWorkspaces(),
        getAgentWorkspaceCapacities(id).catch(() => ({ data: {} })),
        getAgentLogs(id).catch(() => ({ data: { runs: [], nodes: [] } })),
      ]);
      setAgent(agentResp.data);
      setIsDefaultChat(!!agentResp.data?.is_default_chat_agent);
      setHistory(historyResp.data);
      setAgentLogsData(logsResp.data || { runs: [], nodes: [] });
      setTasks(tasksResp.data || []);
      setWorkspaces(workspacesResp.data || []);
      setWsCapacities(wsCapResp.data || {});
      const savedTools = Array.isArray(agentResp.data?.tools) ? agentResp.data.tools : [];
      if (!toolsDraftDirty.current) {
        setSelectedTools([...new Set([...savedTools])]);
      }
      if (!memoryDraftDirty.current) {
        setMemoryType(agentResp.data.memory_type || 'none');
        setMemoryData(typeof agentResp.data.memory_data === 'string' ? agentResp.data.memory_data : JSON.stringify(agentResp.data.memory_data || '', null, 2));
      }
      try {
        const nodesResp = await getNodes(workspaceFilter);
        setNodes((nodesResp.data || []).filter((n) => n.agent_id === id));
      } catch {
        setNodes([]);
      }
      try {
        const definitionResp = await getAgentDefinition(id);
        const def = definitionResp.data || { system_prompt: '', instructions: '', capabilities: '', usage: '', source: '', definition_dir: '' };
        setAgentDefinition(def);
        setDefDraft(prev => ({
          instructions: defDraftDirty.current.instructions ? prev.instructions : (def.instructions || ''),
          capabilities: defDraftDirty.current.capabilities ? prev.capabilities : (def.capabilities || ''),
          usage: defDraftDirty.current.usage ? prev.usage : (def.usage || ''),
        }));
        setDefError(prev => ({
          instructions: defDraftDirty.current.instructions ? prev.instructions : '',
          capabilities: defDraftDirty.current.capabilities ? prev.capabilities : '',
          usage: defDraftDirty.current.usage ? prev.usage : '',
        }));
      } catch {
        setAgentDefinition({ system_prompt: '', instructions: '', capabilities: '', usage: '', source: '', definition_dir: '' });
        setDefDraft(prev => ({
          instructions: defDraftDirty.current.instructions ? prev.instructions : '',
          capabilities: defDraftDirty.current.capabilities ? prev.capabilities : '',
          usage: defDraftDirty.current.usage ? prev.usage : '',
        }));
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
  }, [id, liveUpdates, workspaceFilter, selectedWorkspace]);

  useEffect(() => {
    toolsDraftDirty.current = false;
    setToolsMessage('');
    memoryDraftDirty.current = false;
    defDraftDirty.current = { instructions: false, capabilities: false, usage: false };
    setDefError({ instructions: '', capabilities: '', usage: '' });
    setModelForm(EMPTY_MODEL);
    setModelMessage('');
    setReasoningSettings(defaultReasoningSettings);
  }, [id]);

  // Load reasoning settings from server whenever agent changes
  useEffect(() => {
    getAgentReasoning(id)
      .then(r => setReasoningSettings({
        thinkEnabled: !!r.data.think_enabled,
        thinkMode: r.data.think_mode || 'standard',
        planEnabled: !!r.data.plan_enabled,
        planFormat: r.data.plan_format || 'structured',
      }))
      .catch(() => setReasoningSettings(defaultReasoningSettings));
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

  const handleSaveWsCapacity = async (wsName) => {
    const raw = wsCapacityEdits[wsName];
    const val = raw === '' || raw === undefined ? null : parseInt(raw, 10);
    if (val !== null && (isNaN(val) || val < 1)) return;
    setWsCapacitySaving(wsName);
    try {
      if (val === null) {
        await removeWorkspaceAgentCapacity(wsName, id);
      } else {
        await setWorkspaceAgentCapacity(wsName, id, val);
      }
      setWsCapacities(prev => {
        const next = { ...prev };
        if (val === null) delete next[wsName]; else next[wsName] = val;
        return next;
      });
      setWsCapacityEdits(prev => { const n = { ...prev }; delete n[wsName]; return n; });
    } catch {
      alert('Failed to save capacity');
    } finally {
      setWsCapacitySaving(null);
    }
  };

  const handleToggleDefaultChat = async (checked) => {
    setDefaultChatSaving(true);
    setDefaultChatMessage('');
    try {
      const defaultChatWorkspace = selectedWorkspace || 'default';
      if (checked) {
        await setDefaultChatAgent(id, defaultChatWorkspace);
        setIsDefaultChat(true);
        setDefaultChatMessage(`Set as default chat agent for ${defaultChatWorkspace}.`);
      } else {
        await clearDefaultChatAgent(id, defaultChatWorkspace);
        setIsDefaultChat(false);
        setDefaultChatMessage('Default cleared.');
      }
      setTimeout(() => setDefaultChatMessage(''), 3000);
    } catch {
      setDefaultChatMessage('Failed to update default chat agent.');
    } finally {
      setDefaultChatSaving(false);
    }
  };

  const handleToggleShared = async (checked) => {
    setSharingSaving(true);
    setSharingMessage('');
    try {
      const resp = await updateAgentSharing(id, checked);
      setShared(!!resp.data?.shared);
      setAgent(prev => (prev ? { ...prev, shared: !!resp.data?.shared } : prev));
      setSharingMessage(checked
        ? 'Agent is now shared across all workspaces.'
        : 'Agent is now private to its workspace.');
      setTimeout(() => setSharingMessage(''), 3000);
    } catch (e) {
      setSharingMessage(e.response?.data?.detail || 'Failed to update sharing.');
    } finally {
      setSharingSaving(false);
    }
  };

  // Load shared memory pools when the memory tab opens
  useEffect(() => {
    if (activeTab !== 'memory') return;
    getSharedMemories(workspaceFilter).then(r => setSharedMemories(r.data || [])).catch(() => {});
  }, [activeTab, workspaceFilter]);

  // Load Docker data + Dockerfile when the docker tab opens
  useEffect(() => {
    if (activeTab !== 'docker') return;
    fetchDockerData();
    fetchDockerfile();
  }, [activeTab]);

  // Load skills when the skills tab opens
  useEffect(() => {
    if (activeTab !== 'skills') return;
    fetchSkills(selectedWorkspace);
  }, [activeTab, selectedWorkspace]);

  // Sync skills_enabled from agent spec
  useEffect(() => {
    if (agent) setSkillsEnabled(!!agent.skills_enabled);
  }, [agent]);

  // Sync shared (workspace exposure) from agent spec
  useEffect(() => {
    if (agent) setShared(!!agent.shared);
  }, [agent]);

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


  const handleStartNode = async () => {
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
    toolsDraftDirty.current = true;
    setToolsMessage('');
  };

  const handleSaveTools = async () => {
    setToolsSaving(true);
    setToolsMessage('');
    try {
      await updateAgentTools(id, { tools: selectedTools });
      setToolsMessage('Tools updated');
      toolsDraftDirty.current = false;
      await fetchData();
    } catch (error) {
      setToolsMessage(error.response?.data?.detail || 'Failed to update tools');
    } finally {
      setToolsSaving(false);
    }
  };

  const handleSaveDefinitionField = async (field) => {
    setDefSaving(prev => ({ ...prev, [field]: true }));
    setDefError(prev => ({ ...prev, [field]: '' }));
    try {
      const payload = { [field]: defDraft[field] };
      const { data } = await updateAgentDefinition(id, payload);
      setAgentDefinition(data);
      defDraftDirty.current = { ...defDraftDirty.current, [field]: false };
      setDefDraft(prev => ({
        instructions: field === 'instructions' ? (data.instructions || '') : prev.instructions,
        capabilities: field === 'capabilities' ? (data.capabilities || '') : prev.capabilities,
        usage: field === 'usage' ? (data.usage || '') : prev.usage,
      }));
    } catch (error) {
      setDefError(prev => ({
        ...prev,
        [field]: error.response?.data?.detail || error.message || 'Failed to save',
      }));
    } finally {
      setDefSaving(prev => ({ ...prev, [field]: false }));
    }
  };

  const handleResetDefinitionField = (field) => {
    defDraftDirty.current = { ...defDraftDirty.current, [field]: false };
    setDefDraft(prev => ({ ...prev, [field]: agentDefinition[field] || '' }));
    setDefError(prev => ({ ...prev, [field]: '' }));
  };

  const handleDefinitionDraftChange = (field, value) => {
    defDraftDirty.current = { ...defDraftDirty.current, [field]: value !== (agentDefinition[field] || '') };
    setDefDraft(prev => ({ ...prev, [field]: value }));
    setDefError(prev => ({ ...prev, [field]: '' }));
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
  const agentTools = Array.isArray(agent?.tools) ? agent.tools : [];
  const mergedTools = [...new Set([...agentTools])];
  const visibleToolIds = [...new Set([...availableTools, ...mergedTools, ...selectedTools])];
  const toolsDirty = toolsDraftDirty.current || ([...selectedTools].sort().join('|') !== [...mergedTools].sort().join('|'));
  const runningNodesCount = nodes.filter((n) => n.status === 'running' || n.status === 'starting').length;
  const agentTasks = (tasks || []).filter((t) => t.assigned_agent_type === id);
  const runningTasks = agentTasks.filter((t) => t.agent_state === 'running');
  const agentSingleCapacity = agent?.capacity || 1;
  const isDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';
  // workspace-level session cap (enforced by backend); unlimited in default workspace
  const wsSessionCap = isDefaultWorkspace ? Infinity : (wsCapacities[selectedWorkspace] ?? 1);
  // max sessions = nodes × per-node capacity, capped by workspace limit
  const maxSessions = Math.min(runningNodesCount * agentSingleCapacity, wsSessionCap === Infinity ? Infinity : wsSessionCap);
  const nodesOverWsCap = !isDefaultWorkspace && runningNodesCount >= wsSessionCap;
  const atCapacity = maxSessions !== Infinity && maxSessions > 0 && runningTasks.length >= maxSessions;
  const noNodes = runningNodesCount === 0;
  const sessionLoadFactor = maxSessions > 0 && maxSessions !== Infinity
    ? Math.min(100, Math.round((runningTasks.length / maxSessions) * 100))
    : (maxSessions === Infinity && runningTasks.length > 0 ? Math.min(100, Math.round((runningTasks.length / (runningTasks.length + 1)) * 100)) : 0);

  return (
    <div>
      <Link to="/agents" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Agents
      </Link>

      <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-indigo-600 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">{agent.name}</h2>
        <p className="text-sm text-gray-500 mb-4">{agent.id}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Nodes card */}
          <div className={`border rounded-lg px-3 py-2 ${nodesOverWsCap ? 'bg-orange-50 border-orange-300' : 'bg-indigo-50 border-indigo-100'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${nodesOverWsCap ? 'text-orange-500' : 'text-indigo-500'}`}>Nodes</div>
            <div className={`text-sm font-semibold ${nodesOverWsCap ? 'text-orange-900' : 'text-indigo-900'}`}>
              {runningNodesCount} / {isDefaultWorkspace ? '∞' : wsSessionCap} running
            </div>
            {nodesOverWsCap && (
              <div className="text-[10px] text-orange-600 mt-0.5 font-medium">Exceeds workspace cap</div>
            )}
          </div>

          {/* Sessions card */}
          <div className={`border rounded-lg px-3 py-2 ${atCapacity ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-100'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${atCapacity ? 'text-red-500' : 'text-green-500'}`}>Sessions</div>
            {noNodes ? (
              <div className="text-xs text-amber-700 font-medium mt-0.5">No nodes running — start a node to accept tasks</div>
            ) : (
              <>
                <div className={`text-sm font-semibold mb-1 ${atCapacity ? 'text-red-900' : 'text-green-900'}`}>
                  {runningTasks.length} / {maxSessions === Infinity ? '∞' : maxSessions} open
                </div>
                <div className={`h-1.5 rounded-full overflow-hidden ${atCapacity ? 'bg-red-200' : 'bg-green-200'}`}>
                  <div
                    className={`h-full rounded-full ${atCapacity ? 'bg-red-500' : sessionLoadFactor > 80 ? 'bg-orange-500' : 'bg-indigo-500'}`}
                    style={{ width: `${maxSessions === Infinity ? 0 : sessionLoadFactor}%` }}
                  />
                </div>
                <div className={`text-[10px] mt-0.5 ${atCapacity ? 'text-red-600 font-semibold' : 'text-green-700'}`}>
                  {atCapacity ? 'At capacity — no slots available' : `Load ${maxSessions === Infinity ? '—' : sessionLoadFactor + '%'}`}
                </div>
              </>
            )}
          </div>

          {/* Running tasks card */}
          <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-amber-500 font-semibold">Running Tasks</div>
            <div className="text-sm font-semibold text-amber-900">{runningTasks.length}</div>
            <div className="text-[10px] text-amber-600 mt-0.5">{agentTasks.length} total assigned</div>
          </div>
        </div>
      </div>

      <div className="mb-6 border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'overview', label: 'Overview', icon: Activity },
            { id: 'history', label: 'Sessions', icon: History },
            { id: 'logs', label: 'Runs', icon: FileText },
            { id: 'model', label: 'Model', icon: BrainCircuit },
            { id: 'memory', label: 'Memory', icon: Database },
            { id: 'tools', label: 'Tools', icon: Wrench },
            { id: 'commands', label: 'Commands', icon: Terminal },
            { id: 'nodes', label: 'Nodes', icon: Server },
            { id: 'tasks', label: 'Tasks', icon: Clock },
            { id: 'skills', label: 'Skills', icon: BookOpen },
            { id: 'config', label: 'Config', icon: FileCode },
            { id: 'docker', label: 'Docker', icon: Layers },
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

          {/* ── Agent Identity ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <Activity className="w-4 h-4 text-indigo-500" /> Agent Identity
            </h3>
            {agent.description && (
              <p className="text-base text-gray-600 mb-5 leading-relaxed">{agent.description}</p>
            )}
            <div className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">ID</span>
                <span className=" text-gray-700 text-xs break-all">{agent.id}</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Type</span>
                <span className="font-medium capitalize">{agent.type || 'local'}</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Domain</span>
                <span className=" text-gray-700 text-xs">{agent.domain || 'general'}</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Entrypoint</span>
                <span className=" text-xs text-gray-700 break-all">{agent.entrypoint || '—'}</span>
              </div>
              {agentDefinition.source && (
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Definition Source</span>
                  <span className=" text-xs text-gray-700">{agentDefinition.source}</span>
                </div>
              )}
              {agentDefinition.definition_dir && (
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Definition Folder</span>
                  <span className=" text-xs text-indigo-600 break-all">{agentDefinition.definition_dir}</span>
                </div>
              )}
              {selectedWorkspace && selectedWorkspace !== 'default' && (
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Workspace Capacity</span>
                  {(() => {
                    const effectiveCapacity = wsCapacities[selectedWorkspace] ?? 1;
                    const editVal = wsCapacityEdits[selectedWorkspace];
                    const isEditing = editVal !== undefined;
                    const isSaving = wsCapacitySaving === selectedWorkspace;
                    return isEditing ? (
                      <div className="flex items-center gap-2">
                        <input type="number" min="1" value={editVal}
                          onChange={e => setWsCapacityEdits(prev => ({ ...prev, [selectedWorkspace]: e.target.value }))}
                          className="w-24 border border-gray-300 rounded px-2 py-0.5 text-sm focus:ring-indigo-500 focus:border-indigo-500"
                        />
                        <button onClick={() => handleSaveWsCapacity(selectedWorkspace)} disabled={isSaving}
                          className="text-xs text-white bg-indigo-600 hover:bg-indigo-700 px-2 py-1 rounded disabled:opacity-50">
                          {isSaving ? '...' : 'Save'}
                        </button>
                        <button onClick={() => setWsCapacityEdits(prev => { const n = { ...prev }; delete n[selectedWorkspace]; return n; })}
                          className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{effectiveCapacity} concurrent runs</span>
                        <button onClick={() => setWsCapacityEdits(prev => ({ ...prev, [selectedWorkspace]: String(effectiveCapacity) }))}
                          className="text-xs text-indigo-600 hover:text-indigo-800">Edit</button>
                      </div>
                    );
                  })()}
                </div>
              )}
            </div>
          </div>

          {/* ── Workspace Visibility ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              {shared ? <Globe className="w-4 h-4 text-indigo-500" /> : <Lock className="w-4 h-4 text-indigo-500" />}
              Workspace Visibility
            </h3>
            {agent.system ? (
              <p className="text-sm text-gray-500 flex items-center gap-2">
                <Globe className="w-4 h-4 text-gray-400" />
                System agents are available in every workspace and cannot be restricted.
              </p>
            ) : (
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800">
                      {shared ? 'Shared across all workspaces' : 'Private to its workspace'}
                    </p>
                    <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                      {shared
                        ? 'This agent is exposed and can be added to any workspace.'
                        : agent.owner_workspace
                          ? <>This agent is only visible in workspace <span className="font-semibold text-gray-700">{agent.owner_workspace}</span> and cannot be added to others. Enable sharing to expose it everywhere.</>
                          : 'This agent is not bound to a workspace. Enable sharing to mark it as exposed across all workspaces.'}
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer flex-shrink-0 mt-0.5">
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={shared}
                      disabled={sharingSaving}
                      onChange={(e) => handleToggleShared(e.target.checked)}
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:ring-2 peer-focus:ring-indigo-300 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600" />
                  </label>
                </div>
                {sharingMessage && (
                  <p className="text-xs text-indigo-600">{sharingMessage}</p>
                )}
              </div>
            )}
          </div>

          {/* ── Tools & Memory stats ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Tools */}
            <div className="bg-white p-5 shadow-md rounded-lg">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                  <Wrench className="w-4 h-4 text-indigo-500" /> Tools
                </h3>
                <button type="button" onClick={() => setActiveTab('tools')}
                  className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50">
                  Manage
                </button>
              </div>
              <div className="flex items-center gap-6 mb-4">
                <div className="text-center">
                  <p className="text-3xl font-bold text-indigo-700">{selectedTools.length}</p>
                  <p className="text-xs text-gray-400 mt-0.5">Enabled</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-bold text-gray-300">{availableTools.length}</p>
                  <p className="text-xs text-gray-400 mt-0.5">Available</p>
                </div>
              </div>
              {selectedTools.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {selectedTools.slice(0, 8).map(tool => (
                    <span key={tool} className="text-xs bg-indigo-50 text-indigo-700 border border-indigo-100 px-2 py-0.5 rounded-full">{tool}</span>
                  ))}
                  {selectedTools.length > 8 && (
                    <button type="button" onClick={() => setActiveTab('tools')}
                      className="text-xs text-gray-400 hover:text-indigo-600 px-1 py-0.5">
                      +{selectedTools.length - 8} more
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-xs text-gray-400 italic">No tools enabled. Click Manage to configure.</p>
              )}
            </div>

            {/* Memory */}
            <div className="bg-white p-5 shadow-md rounded-lg">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                  <Database className="w-4 h-4 text-amber-500" /> Memory
                </h3>
                <button type="button" onClick={() => setActiveTab('memory')}
                  className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50">
                  Configure
                </button>
              </div>
              <div className="space-y-3">
                <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold ${
                  memoryType === 'none'   ? 'bg-gray-100 text-gray-500' :
                  memoryType === 'local'  ? 'bg-blue-100 text-blue-700' :
                                            'bg-amber-100 text-amber-700'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${
                    memoryType === 'none'  ? 'bg-gray-400' :
                    memoryType === 'local' ? 'bg-blue-500' : 'bg-amber-500 animate-pulse'
                  }`} />
                  {memoryType === 'none' ? 'No memory' : memoryType === 'local' ? 'Local (agent-specific)' : 'Shared pool'}
                </span>
                {memoryType === 'shared' && memoryData && (
                  <div className="text-xs text-gray-500 truncate">Pool: {memoryData}</div>
                )}
                {memoryType === 'local' && memoryData && (
                  <div className="text-xs text-gray-500 italic line-clamp-2">{memoryData.slice(0, 120)}{memoryData.length > 120 ? '…' : ''}</div>
                )}
                {memoryType === 'none' && (
                  <p className="text-xs text-gray-400">No memory persistence between sessions.</p>
                )}
              </div>
            </div>
          </div>

          {/* Model & Parameters card */}
          {(() => {
            // All model fields are top-level on the agent object
            const provider = agent.provider && agent.provider !== 'inherit' ? agent.provider : null;
            const agentModel = agent.model || null;
            const agentBaseUrl = agent.base_url || null;
            const hasModelConfig = provider || agentModel || agentBaseUrl || agent.temperature != null || agent.max_tokens != null;
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
                  {agentModel && (
                    <div className="col-span-2 flex items-center gap-2">
                      <span className="text-gray-500 w-28 shrink-0">Model</span>
                      <span className=" text-xs bg-gray-100 px-2 py-0.5 rounded">{agentModel}</span>
                    </div>
                  )}
                  {agentBaseUrl && (
                    <div className="col-span-2 flex items-center gap-2 min-w-0">
                      <span className="text-gray-500 w-28 shrink-0">Base URL</span>
                      <span className=" text-xs text-indigo-600 truncate">{agentBaseUrl}</span>
                    </div>
                  )}
                  {agent.temperature != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500">Temperature</span>
                      <span className=" text-xs bg-gray-100 px-2 py-0.5 rounded">{agent.temperature}</span>
                    </div>
                  )}
                  {agent.max_tokens != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500">Max Tokens</span>
                      <span className=" text-xs bg-gray-100 px-2 py-0.5 rounded">{agent.max_tokens.toLocaleString()}</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })()}

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

          {/* ── Chat Settings ── */}
          {(
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-1">
                <MessageSquare className="w-4 h-4 text-indigo-500" /> Chat Settings
              </h3>
              <p className="text-xs text-gray-500 mb-4">Configure how this agent appears in the Chat interface.</p>
              <label className="flex items-center gap-3 cursor-pointer select-none">
                <div className="relative">
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={isDefaultChat}
                    disabled={defaultChatSaving}
                    onChange={e => handleToggleDefaultChat(e.target.checked)}
                  />
                  <div className={`w-10 h-6 rounded-full transition-colors ${isDefaultChat ? 'bg-indigo-600' : 'bg-gray-300'} ${defaultChatSaving ? 'opacity-50' : ''}`} />
                  <div className={`absolute top-1 left-1 w-4 h-4 bg-white rounded-full shadow transition-transform ${isDefaultChat ? 'translate-x-4' : 'translate-x-0'}`} />
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-800">Default chat agent</div>
                  <div className="text-xs text-gray-500">Pre-select this agent for the current workspace when opening Chat or starting a new conversation.</div>
                </div>
              </label>
              {defaultChatMessage && (
                <p className="mt-3 text-xs text-indigo-600">{defaultChatMessage}</p>
              )}
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
                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-600">
                          {run.task_id ? (
                            <Link to={`/tasks/${run.task_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.task_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs text-gray-500">
                          {new Date(run.started_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-3">
                            {run.session_id && (
                              <Link to={`/sessions/${run.session_id}`} className="text-indigo-600 hover:text-indigo-900 text-xs font-medium flex items-center gap-1">
                                <History className="w-3 h-3" /> Session
                              </Link>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

        </div>
      )}

      {activeTab === 'logs' && (
        <div className="space-y-6">
          <div className="bg-indigo-50 border border-indigo-100 rounded-lg p-4">
            <div className="text-sm font-semibold text-indigo-900 mb-1">Node-scoped logs</div>
            <div className="text-xs text-indigo-700">
              Agent run/chat logs are written to node folders under <code>agents/state/node_runs/&lt;node_id&gt;/</code>.
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <FileText className="w-5 h-5 mr-2 text-indigo-600" /> Run Logs
            </h3>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Node</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Task</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Log File</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {(agentLogsData.runs || []).length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">No run logs found for this agent.</td>
                    </tr>
                  ) : (
                    (agentLogsData.runs || []).map((run) => (
                      <tr key={run.run_id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 whitespace-nowrap text-sm capitalize">{run.status || '—'}</td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs">
                          {run.node_id ? (
                            <Link to={`/nodes/${run.node_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.node_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs">
                          {run.task_id ? (
                            <Link to={`/tasks/${run.task_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.task_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 text-xs text-gray-500 max-w-[500px] truncate">{run.log_file || '—'}</td>
                        <td className="px-4 py-2 whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-3">
                            <Link to={`/messages/${run.run_id}`} className="text-indigo-600 hover:text-indigo-900 text-xs font-medium flex items-center gap-1">
                              <MessageSquare className="w-3 h-3" /> Message
                            </Link>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" /> Node Process Logs
            </h3>
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">Node</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">Workspace</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">Log File</th>
                    <th className="text-right px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {(agentLogsData.nodes || []).length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">No node logs found for this agent.</td>
                    </tr>
                  ) : (
                    (agentLogsData.nodes || []).map((node) => {
                      const nodeId = node.node_id || node.id;
                      return (
                        <tr key={nodeId} className="hover:bg-gray-50">
                          <td className="px-4 py-2 text-xs">
                            <Link to={`/nodes/${nodeId}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(nodeId || '').slice(0, 8)}…
                            </Link>
                          </td>
                          <td className="px-4 py-2"><NodeStatusBadge status={node.status} /></td>
                          <td className="px-4 py-2 text-xs text-gray-600">{node.workspace || '—'}</td>
                          <td className="px-4 py-2 text-xs text-gray-500 max-w-[500px] truncate">{node.log_file || '—'}</td>
                          <td className="px-4 py-2 text-right">
                            <button
                              onClick={() => openNodeLogs(node)}
                              className="text-indigo-600 hover:text-indigo-900 text-xs font-medium inline-flex items-center justify-end"
                            >
                              <Terminal className="w-3 h-3 mr-1" /> Open
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
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
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  )}
                  {memoryData && (
                    <p className="text-xs text-gray-400 mt-1 truncate">ID: {memoryData}</p>
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
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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

          {/* ── Memory Tools ── */}
          {memoryType === 'shared' && memoryData && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-sm font-bold text-gray-900 mb-3 flex items-center gap-2">
                <Wrench className="w-4 h-4 text-indigo-500" /> Memory Tools
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* read_memory — always on */}
                <div className="p-3 border-2 border-green-200 bg-green-50 rounded-xl flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-gray-900">Read Memory</div>
                    <div className="text-xs text-gray-500 mt-0.5">Read files, notes, and key-value pairs from the pool</div>
                  </div>
                  <span className="px-2.5 py-1 rounded-full text-xs font-semibold border bg-green-600 text-white border-green-600 shrink-0">
                    Always On
                  </span>
                </div>
                {/* write_memory — toggleable */}
                {(() => {
                  const enabled = selectedTools.includes('write_memory');
                  return (
                    <div className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${enabled ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                      <div>
                        <div className="text-sm font-semibold text-gray-900">Write Memory</div>
                        <div className="text-xs text-gray-500 mt-0.5">Create or update files, notes, and key-value pairs</div>
                      </div>
                      <button
                        type="button"
                        onClick={async () => {
                          const next = enabled
                            ? selectedTools.filter(t => t !== 'write_memory')
                            : [...selectedTools, 'write_memory'];
                          setSelectedTools(next);
                          setToolsSaving(true);
                          try { await updateAgentTools(id, { tools: next }); await fetchData(); } catch {} finally { setToolsSaving(false); }
                        }}
                        className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          enabled ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'On' : 'Off'}
                      </button>
                    </div>
                  );
                })()}
              </div>
            </div>
          )}

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

          {/* ── Reasoning Capabilities ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold flex items-center mb-1">
              <BrainCircuit className="w-5 h-5 mr-2 text-violet-600" />
              Reasoning Capabilities
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Enable structured reasoning and planning for this agent. These capabilities are configured separately from regular tools.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Think card */}
              {(() => {
                const enabled = reasoningSettings.thinkEnabled;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-violet-300 bg-violet-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-violet-100' : 'bg-gray-200'}`}>
                          <BrainCircuit className={`w-4 h-4 ${enabled ? 'text-violet-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">Think</div>
                          <div className="text-xs text-gray-500">Step-by-step reasoning</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const next = { ...reasoningSettings, thinkEnabled: !enabled };
                          setReasoningSettings(next);
                          updateAgentReasoning(id, { think_enabled: next.thinkEnabled }).catch(() => {});
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          enabled ? 'bg-violet-600 text-white border-violet-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      Gives the agent a scratchpad to reason before and after actions — diagnose errors, check logic, and analyse results.
                    </p>
                    {enabled && (
                      <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">Reasoning Depth</label>
                        <select
                          value={reasoningSettings.thinkMode}
                          onChange={e => {
                            const next = { ...reasoningSettings, thinkMode: e.target.value };
                            setReasoningSettings(next);
                            updateAgentReasoning(id, { think_mode: e.target.value }).catch(() => {});
                          }}
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-violet-500 bg-white"
                        >
                          {THINK_MODES.map(m => (
                            <option key={m.value} value={m.value}>{m.label} — {m.desc}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Plan card */}
              {(() => {
                const enabled = reasoningSettings.planEnabled;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-indigo-100' : 'bg-gray-200'}`}>
                          <Layers className={`w-4 h-4 ${enabled ? 'text-indigo-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">Plan</div>
                          <div className="text-xs text-gray-500">Upfront structured planning</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const next = { ...reasoningSettings, planEnabled: !enabled };
                          setReasoningSettings(next);
                          updateAgentReasoning(id, { plan_enabled: next.planEnabled }).catch(() => {});
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          enabled ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      Lets the agent produce a full execution plan before starting work — outlining steps, dependencies, and risks upfront.
                    </p>
                    {enabled && (
                      <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">Planning Format</label>
                        <select
                          value={reasoningSettings.planFormat}
                          onChange={e => {
                            const next = { ...reasoningSettings, planFormat: e.target.value };
                            setReasoningSettings(next);
                            updateAgentReasoning(id, { plan_format: e.target.value }).catch(() => {});
                          }}
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
                        >
                          {PLAN_FORMATS.map(f => (
                            <option key={f.value} value={f.value}>{f.label} — {f.desc}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* ── Regular Tools ── */}
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
            {visibleToolIds.filter(t => !REASONING_TOOLS.includes(t) && !MEMORY_TOOLS.includes(t)).length ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {visibleToolIds.filter(t => !REASONING_TOOLS.includes(t) && !MEMORY_TOOLS.includes(t)).map((tool) => {
                  const enabled = selectedTools.includes(tool);
                  return (
                    <div key={tool} className="p-3 border border-gray-100 rounded-lg bg-gray-50 flex items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-gray-800">{tool}</div>
                        <div className="text-xs text-gray-500 mt-1">source: tools</div>
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

        </div>
      )}

      {activeTab === 'nodes' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" />
              Agent Nodes
            </h3>
            <div className="flex items-center gap-2">
              {nodesOverWsCap && (
                <span className="text-xs text-orange-600 font-medium">Node limit reached ({wsSessionCap})</span>
              )}
              <button
                type="button"
                disabled={nodesOverWsCap}
                onClick={() => { setStartWorkspace(selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : ''); setShowStartNodeModal(true); }}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Play className="w-3.5 h-3.5 mr-1" />
                Start Node
              </button>
            </div>
          </div>
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
                          <span className=" text-xs text-gray-600">
                            {String(nodeId).slice(0, 8)}
                            <span className="text-gray-400">…</span>
                          </span>
                        </td>
                        <td className="px-5 py-3 text-gray-600 text-xs">{node.label || '—'}</td>
                        <td className="px-5 py-3">
                          {node.workspace
                            ? <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{node.workspace}</span>
                            : <span className="text-gray-400 text-xs">—</span>}
                        </td>
                        <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{fmtNodeDate(node.started_at)}</td>
                        <td className="px-5 py-3 text-gray-600 text-xs whitespace-nowrap">
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
                        <div className="text-xs text-gray-500 mt-1 truncate">{task.id}</div>
                        <div className="text-xs text-gray-500 mt-1">Workspace: <span className="">{task.workspace || '—'}</span></div>
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

      {activeTab === 'commands' && (() => {
        const agentCmds = agent?.commands || [];
        const globalCmds = [
          { name: '/help', description: 'Show available commands', template: '/help' },
          { name: '/clear', description: 'Clear the current conversation', template: '/clear' },
          { name: '/new', description: 'Start a new conversation', template: '/new' },
          { name: '/config', description: 'Show configuration for the current agent', template: '/config' },
        ];
        return (
          <div className="space-y-6">
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-lg font-bold mb-1 flex items-center gap-2">
                <Terminal className="w-5 h-5 text-indigo-600" /> Slash Commands
              </h3>
              <p className="text-sm text-gray-500 mb-5">
                Type <span className=" bg-gray-100 px-1 rounded">/</span> in the chat to trigger these commands. Use <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">↑↓</kbd> to navigate, <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">Enter</kbd> or <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">Tab</kbd> to select, <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">Esc</kbd> to dismiss.
              </p>

              {agentCmds.length > 0 && (
                <div className="mb-6">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">Agent Commands</h4>
                  <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden">
                    {agentCmds.map((cmd) => (
                      <div key={cmd.name} className="flex items-start gap-4 px-4 py-3 bg-white hover:bg-gray-50 transition-colors">
                        <span className=" text-sm font-semibold text-indigo-600 shrink-0 w-40">{cmd.name}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-gray-700">{cmd.description}</p>
                          <p className="text-xs text-gray-400 mt-0.5 truncate">Template: {cmd.template}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {agentCmds.length === 0 && (
                <div className="mb-6 flex items-center gap-3 p-4 bg-amber-50 border border-amber-100 rounded-xl text-sm text-amber-700">
                  <Hash className="w-4 h-4 shrink-0" />
                  No agent-specific commands defined. Add a <span className=" mx-1">"commands"</span> array to this agent in <span className=" ml-1">.agents_hub/agents.json</span>.
                </div>
              )}

              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">Global Commands</h4>
                <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden">
                  {globalCmds.map((cmd) => (
                    <div key={cmd.name} className="flex items-start gap-4 px-4 py-3 bg-white hover:bg-gray-50 transition-colors">
                      <span className=" text-sm font-semibold text-gray-600 shrink-0 w-40">{cmd.name}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-gray-700">{cmd.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {activeTab === 'config' && (
        <div className="space-y-6">
          {agentDefinition.definition_dir && (
            <div className="bg-indigo-50 border border-indigo-100 rounded-lg px-4 py-2 text-xs text-indigo-700 break-all">
              Definition folder: <span className="font-mono">{agentDefinition.definition_dir}</span>
            </div>
          )}

          {[
            { key: 'instructions', label: 'instructions.md', desc: 'Main system prompt (required).', icon: Terminal, required: true },
            { key: 'capabilities', label: 'capabilities.md', desc: 'What this agent can do. Optional — leave empty to delete.', icon: Zap, required: false },
            { key: 'usage',        label: 'usage.md',        desc: 'When and how to invoke this agent. Optional — leave empty to delete.', icon: BookOpen, required: false },
          ].map(({ key, label, desc, icon: Icon, required }) => {
            const original = agentDefinition[key] || '';
            const draft = defDraft[key] || '';
            const dirty = draft !== original;
            const saving = defSaving[key];
            const error = defError[key];
            const cannotDelete = required && !draft.trim();
            return (
              <div key={key} className="bg-white p-6 shadow-md rounded-lg">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div>
                    <h3 className="text-lg font-bold flex items-center">
                      <Icon className="w-5 h-5 mr-2 text-indigo-600" />
                      {label}
                      {required && <span className="ml-2 text-[10px] uppercase tracking-wide font-bold text-red-500">Required</span>}
                    </h3>
                    <p className="text-xs text-gray-500 mt-1">{desc}</p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      onClick={() => handleResetDefinitionField(key)}
                      disabled={!dirty || saving}
                      className="px-3 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Reset
                    </button>
                    <button
                      onClick={() => handleSaveDefinitionField(key)}
                      disabled={!dirty || saving || cannotDelete}
                      title={cannotDelete ? 'instructions.md cannot be empty' : undefined}
                      className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {saving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
                      Save
                    </button>
                  </div>
                </div>
                <textarea
                  value={draft}
                  onChange={e => handleDefinitionDraftChange(key, e.target.value)}
                  spellCheck={false}
                  className="w-full font-mono text-xs bg-gray-900 text-green-300 p-4 rounded-lg min-h-[200px] resize-y border border-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder={required ? 'Required — must contain the system prompt' : 'Optional — leave empty to delete this file'}
                />
                {error && (
                  <p className="text-xs text-red-600 mt-2">{error}</p>
                )}
                {dirty && !error && (
                  <p className="text-xs text-amber-600 mt-2">Unsaved changes</p>
                )}
              </div>
            );
          })}

          {agentDefinition.system_prompt && (
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-lg font-bold mb-2 flex items-center">
                <FileCode className="w-5 h-5 mr-2 text-indigo-600" />
                Assembled System Prompt (read-only)
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                What the agent actually receives at runtime: instructions.md plus capabilities.md and usage.md sections when present.
              </p>
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap max-h-96">
                {agentDefinition.system_prompt}
              </pre>
            </div>
          )}
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
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
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
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none disabled:opacity-50 disabled:bg-gray-50"
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
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
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

      {activeTab === 'docker' && (
        <div className="space-y-6">
          {/* Image status + build actions */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                <Layers className="w-4 h-4 text-indigo-500" /> Docker Images
              </h3>
              <button
                onClick={fetchDockerData}
                disabled={dockerLoading}
                className="text-xs text-gray-500 hover:text-gray-800 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50 flex items-center gap-1 disabled:opacity-40"
              >
                {dockerLoading ? <Loader className="w-3 h-3 animate-spin" /> : <Activity className="w-3 h-3" />}
                Refresh
              </button>
            </div>

            {/* Image rows */}
            <div className="space-y-2 mb-5">
              {[
                { label: 'Base image', tag: 'agents-hub/base:latest' },
                { label: `Agent image (${id})`, tag: `agents-hub/${id}:latest` },
              ].map(({ label, tag }) => {
                const exists = dockerImages.some(img => `${img.repository}:${img.tag}` === tag || img.repository === tag.split(':')[0]);
                return (
                  <div key={tag} className="flex items-center justify-between px-4 py-3 rounded-lg border border-gray-200 bg-gray-50">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-800">{label}</p>
                      <p className="text-xs text-gray-400 font-mono mt-0.5">{tag}</p>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold ${exists ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${exists ? 'bg-green-500' : 'bg-gray-400'}`} />
                      {exists ? 'Built' : 'Not built'}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Build buttons */}
            <div className="flex flex-wrap gap-3">
              <button
                onClick={handleBuildBase}
                disabled={buildingBase || buildingAgent}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-gray-700 rounded-lg hover:bg-gray-800 disabled:opacity-50"
              >
                {buildingBase ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {buildingBase ? 'Building base…' : 'Build base image'}
              </button>
              <button
                onClick={handleBuildAgent}
                disabled={buildingBase || buildingAgent}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {buildingAgent ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {buildingAgent ? 'Building…' : `Build agent image`}
              </button>
            </div>

            {/* Build output */}
            {(buildLog || buildError) && (
              <div className="mt-4">
                {buildError && (
                  <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-2">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />{buildError}
                  </div>
                )}
                {buildLog && (
                  <pre className="text-xs bg-gray-900 text-green-300 rounded-lg p-4 overflow-auto max-h-48 whitespace-pre-wrap">{buildLog}</pre>
                )}
              </div>
            )}
          </div>

          {/* Dockerfile preview */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-3">
              <FileCode className="w-4 h-4 text-indigo-500" /> Dockerfile
            </h3>
            <p className="text-xs text-gray-500 mb-3">
              Auto-generated per-agent Dockerfile. Extends <code className="text-indigo-600">agents-hub/base:latest</code> and sets the agent identity.
              Saved to <code className="text-indigo-600">agents/state/dockerfiles/{id}.Dockerfile</code>.
            </p>
            {dockerfileLoading ? (
              <div className="flex justify-center py-8"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
            ) : dockerfileContent ? (
              <pre className="text-xs bg-gray-900 text-green-300 rounded-lg p-4 overflow-auto max-h-72 whitespace-pre-wrap">{dockerfileContent}</pre>
            ) : (
              <p className="text-sm text-gray-400 italic">Dockerfile preview unavailable.</p>
            )}
          </div>

          {/* Running containers */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <Server className="w-4 h-4 text-indigo-500" /> Containers
              <span className="text-xs text-gray-400 font-normal">(for this agent)</span>
            </h3>
            {dockerLoading ? (
              <div className="flex justify-center py-6"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
            ) : dockerContainers.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <Server className="w-10 h-10 mx-auto mb-3 opacity-20" />
                <p className="text-sm">No containers found for this agent.</p>
                <p className="text-xs mt-1">Start a node in Docker mode from the Nodes tab.</p>
              </div>
            ) : (
              <div className="space-y-2">
                {dockerContainers.map(c => {
                  const isRunning = c.state === 'running';
                  const busy = dockerActionBusy[c.name];
                  return (
                    <div key={c.id || c.name} className="flex items-center gap-3 px-4 py-3 border border-gray-200 rounded-lg">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-mono font-medium text-gray-800 truncate">{c.name}</p>
                        <div className="flex items-center gap-3 text-xs text-gray-400 mt-0.5">
                          <span>{c.status || c.state}</span>
                          {c.image && <span className="truncate font-mono">{c.image}</span>}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          onClick={() => handleShowContainerLogs(c.name)}
                          className="text-xs text-gray-500 hover:text-gray-800 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50"
                        >
                          Logs
                        </button>
                        {isRunning && (
                          <button
                            onClick={() => handleStopContainer(c.name)}
                            disabled={busy}
                            className="text-xs text-orange-600 hover:text-orange-800 border border-orange-200 px-2 py-1 rounded-lg hover:bg-orange-50 disabled:opacity-40"
                          >
                            {busy ? <Loader className="w-3 h-3 animate-spin inline" /> : 'Stop'}
                          </button>
                        )}
                        {!isRunning && (
                          <button
                            onClick={() => handleRemoveContainer(c.name)}
                            disabled={busy}
                            className="text-xs text-red-600 hover:text-red-800 border border-red-200 px-2 py-1 rounded-lg hover:bg-red-50 disabled:opacity-40"
                          >
                            {busy ? <Loader className="w-3 h-3 animate-spin inline" /> : 'Remove'}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'skills' && (
        <div className="space-y-5">

          {/* ── Configuration card ── */}
          <div className="bg-white rounded-xl border border-t-4 border-t-purple-500 border-gray-200 p-6 shadow-sm">
            <h3 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-purple-500" /> Skills Configuration
            </h3>
            <p className="text-sm text-gray-500 mb-5">
              When enabled, relevant skills are automatically matched to the task description and injected before the agent starts.
              Agents can also discover and save new skills using the <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">save_skill</code> tool.
            </p>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-700">Procedural Skills</p>
                <p className="text-xs text-gray-400 mt-0.5">Scope: this agent · workspace-specific</p>
              </div>
              <button
                onClick={() => handleToggleSkillsEnabled(!skillsEnabled)}
                disabled={skillsConfigSaving}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${skillsEnabled ? 'bg-purple-600' : 'bg-gray-200'}`}
              >
                {skillsConfigSaving
                  ? <Loader className="absolute w-3 h-3 animate-spin text-white left-1/2 -translate-x-1/2" />
                  : <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${skillsEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
                }
              </button>
            </div>
          </div>

          {/* ── Skills list ── */}
          {!selectedWorkspace ? (
            <div className="bg-white rounded-xl border border-gray-200 p-10 text-center shadow-sm">
              <BookOpen className="w-8 h-8 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500">Select a workspace to view and manage skills.</p>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                <div>
                  <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                    <BookOpen className="w-4 h-4 text-purple-500" /> Skills
                    <span className="text-xs font-normal text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">{selectedWorkspace}</span>
                  </h3>
                  {skillsMessage && (
                    <p className="text-xs text-green-600 mt-0.5 flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> {skillsMessage}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => setShowAddSkill(v => !v)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-purple-600 rounded-lg hover:bg-purple-700"
                >
                  {showAddSkill ? <ChevronUp className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                  {showAddSkill ? 'Cancel' : 'Add Skill'}
                </button>
              </div>

              {/* Add skill form */}
              {showAddSkill && (
                <div className="px-5 py-4 bg-purple-50 border-b border-purple-100 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">Name</label>
                      <input
                        type="text"
                        value={skillForm.name}
                        onChange={e => setSkillForm(f => ({ ...f, name: e.target.value }))}
                        placeholder="e.g. Fix Python Import Error"
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">Tags <span className="text-gray-400 font-normal">(comma-separated)</span></label>
                      <input
                        type="text"
                        value={skillForm.tags}
                        onChange={e => setSkillForm(f => ({ ...f, tags: e.target.value }))}
                        placeholder="e.g. debugging, python, api"
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">When to use this skill</label>
                    <input
                      type="text"
                      value={skillForm.description}
                      onChange={e => setSkillForm(f => ({ ...f, description: e.target.value }))}
                      placeholder="e.g. When a Python module import fails with ModuleNotFoundError"
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">Steps <span className="text-gray-400 font-normal">(one per line)</span></label>
                    <textarea
                      value={skillForm.steps}
                      onChange={e => setSkillForm(f => ({ ...f, steps: e.target.value }))}
                      rows={5}
                      placeholder={"Check if package is in requirements.txt\nActivate virtual environment\nRun pip install -r requirements.txt\nRetry the import"}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none"
                    />
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={handleSaveSkill}
                      disabled={skillSaving || !skillForm.name || !skillForm.description || !skillForm.steps.trim()}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-purple-600 rounded-lg hover:bg-purple-700 disabled:opacity-40"
                    >
                      {skillSaving ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      {skillSaving ? 'Saving…' : 'Save Skill'}
                    </button>
                  </div>
                </div>
              )}

              {/* Skills table */}
              {skillsLoading ? (
                <div className="flex justify-center py-10">
                  <Loader className="w-5 h-5 animate-spin text-purple-400" />
                </div>
              ) : skills.length === 0 ? (
                <div className="py-12 text-center">
                  <BookOpen className="w-8 h-8 text-gray-200 mx-auto mb-3" />
                  <p className="text-sm text-gray-500">No skills yet for this agent in <strong>{selectedWorkspace}</strong>.</p>
                  <p className="text-xs text-gray-400 mt-1">Add one above, or the agent will create skills automatically when <code className="bg-gray-100 px-1 rounded">save_skill</code> is called.</p>
                </div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {skills.map(skill => (
                    <div key={skill.id} className="px-5 py-4 hover:bg-gray-50 group">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className="font-semibold text-sm text-gray-900">{skill.name}</span>
                            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${skill.source === 'user' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'}`}>
                              {skill.source}
                            </span>
                            {skill.use_count > 0 && (
                              <span className="text-[10px] text-gray-400">{skill.use_count}× used</span>
                            )}
                          </div>
                          <p className="text-xs text-gray-500 italic mb-2">{skill.description}</p>
                          {skill.tags.length > 0 && (
                            <div className="flex items-center gap-1 flex-wrap mb-2">
                              <Tag className="w-3 h-3 text-gray-300" />
                              {skill.tags.map(t => (
                                <span key={t} className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{t}</span>
                              ))}
                            </div>
                          )}
                          <ol className="space-y-0.5 pl-4">
                            {skill.steps.map((step, i) => (
                              <li key={i} className="text-xs text-gray-600 list-decimal">{step}</li>
                            ))}
                          </ol>
                        </div>
                        <button
                          onClick={() => handleDeleteSkill(skill.id)}
                          disabled={skillDeleteBusy[skill.id]}
                          className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-gray-300 hover:text-red-500 mt-0.5"
                          title="Delete skill"
                        >
                          {skillDeleteBusy[skill.id]
                            ? <Loader className="w-4 h-4 animate-spin" />
                            : <Trash2 className="w-4 h-4" />
                          }
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Container logs modal */}
      {containerLogsName && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <span className="text-gray-200 text-sm font-semibold font-mono">{containerLogsName}</span>
              <button onClick={() => setContainerLogsName(null)} className="text-gray-500 hover:text-gray-300">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              {containerLogsLoading ? (
                <div className="flex justify-center py-12"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
              ) : (
                <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{containerLogsText || '(no output)'}</pre>
              )}
            </div>
          </div>
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
                    <option key={ws.name} value={ws.name}>{ws.label || ws.id || ws.name}</option>
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
                <span className="text-gray-200 text-sm font-semibold">
                  {logsNode.agent_name || logsNode.agent_id || agent?.name || id}
                </span>
                <span className="text-gray-500 text-xs">
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
                <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logsNodeText}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentDetails;
