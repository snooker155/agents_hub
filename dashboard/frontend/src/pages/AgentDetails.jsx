import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import InstanceList from '../components/InstanceList';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { Activity, Radio, History, Server, Wrench, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2, FileCode, Play, Square, Loader, X, FileText, BrainCircuit, Eye, EyeOff, Link2, Layers, Hash, Copy, FileSearch, Zap, BarChart2, Wifi, MessageSquare, BookOpen, Plus, ChevronDown, ChevronUp, Tag, Globe, Lock, Share2, HelpCircle, Repeat, AlertTriangle, Users } from 'lucide-react';
import { checkCombination, CAPABILITY_LABELS } from '../lib/capabilities';
import ImportedAgentPanel from '../components/ImportedAgentPanel';
import { getAgent, getAgents, getAgentDelegates, updateAgentDelegates, getAgentEpisodicConfig, updateAgentEpisodicConfig, getAgentHistory, getAgentLogs, updateAgentMemory, eraseAgentMemory, updateAgentTools, updateAgentDescription, getAgentModel, updateAgentModel, getAgentReasoning, updateAgentReasoning, updateAgentResponseFormat, updateAgentClarifyGate, updateAgentSelfDelegation, getCustomBackends, getNodes, getAgentDefinition, updateAgentDefinition, getTasks, getTools, startNode, stopNode, deleteNode, getWorkspaces, getNodeLogs, getSharedMemories, getSharedMemory, testLocalModel, getAgentWorkspaceCapacities, setWorkspaceAgentCapacity, removeWorkspaceAgentCapacity, getDockerfile, buildBaseImage, buildAgentImage, getContainerImages, getContainers, getContainerLogs, stopContainerByName, removeContainer, setDefaultChatAgent, clearDefaultChatAgent, updateAgentSkillsConfig, getAgentSkills, createAgentSkill, deleteAgentSkill, updateAgentSharing, getAgentDefinitionChat, clearAgentDefinitionChat, stopAgentDefinitionChat, agentDefinitionChatUrl } from '../api';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
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
  raw:     { labelKey: 'agentDetails.rag.raw',     color: 'bg-gray-100 text-gray-600',    dot: 'bg-gray-400' },
  indexed: { labelKey: 'agentDetails.rag.indexed', color: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
  failed:  { labelKey: 'agentDetails.rag.failed',  color: 'bg-red-100 text-red-700',      dot: 'bg-red-500' },
};

function RagBadge({ status, vectorized }) {
  const { t } = useI18n();
  const cfg = RAG_STATUS[status] || RAG_STATUS.raw;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {vectorized ? t('agentDetails.rag.vectorized') : t(cfg.labelKey)}
    </span>
  );
}

function MemoryFileCard({ file, poolId }) {
  const { t } = useI18n();
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
            {file.rag_chunks > 0 && <span>{t('agentDetails.chunkCount', { count: file.rag_chunks })}</span>}
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
            <Hash className="w-3 h-3" /> {t('agentDetails.retrieval')} <code className="text-indigo-600">{t('agentDetails.readMemory')}</code> tool
          </p>
          <div className="bg-gray-900 rounded-lg px-3 py-2 flex items-start justify-between gap-2">
            <pre className="text-xs text-green-300 overflow-x-auto flex-1">{toolArgs}</pre>
            <button onClick={copyTool}
              className="text-gray-400 hover:text-white shrink-0 mt-0.5 transition-colors" title={t('agentDetails.copy')}>
              {copied ? <CheckCircle className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>

        {/* Vector metadata */}
        {file.vectorized && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs bg-indigo-50 rounded-lg px-3 py-2.5">
            <div className="flex items-center gap-1.5 col-span-2">
              <Zap className="w-3 h-3 text-indigo-500" />
              <span className="font-medium text-indigo-800">{t('agentDetails.vectorSearchAvailable')}</span>
            </div>
            {file.vector_db && <div><span className="text-gray-500">{t('agentDetails.vectorDb')}</span> <span className="font-medium text-gray-800">{file.vector_db}</span></div>}
            {file.vector_db_collection && <div><span className="text-gray-500">{t('agentDetails.collection')}</span> <span className="font-medium text-gray-800">{file.vector_db_collection}</span></div>}
            {file.embedding_model && <div><span className="text-gray-500">{t('agentDetails.model')}</span> <span className="font-medium text-gray-800">{file.embedding_model}</span></div>}
            {file.embedding_dims > 0 && <div><span className="text-gray-500">{t('agentDetails.dims')}</span> <span className="font-medium text-gray-800">{file.embedding_dims}</span></div>}
            {file.rag_chunk_size && <div><span className="text-gray-500">{t('agentDetails.chunkSize')}</span> <span className="font-medium text-gray-800">{t('agentDetails.charCount', { count: file.rag_chunk_size })}</span></div>}
          </div>
        )}

        {/* RAG only (no vector) */}
        {file.rag_status === 'indexed' && !file.vectorized && (
          <div className="text-xs bg-green-50 rounded-lg px-3 py-2 text-green-700 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5" />
            {t('agentDetails.textChunkedInto', { chunks: file.rag_chunks, size: file.rag_chunk_size })}{' '}
            {t('agentDetails.configureVectorDbIn')} <strong>{t('agentDetails.settingsRagVectors')}</strong> {t('agentDetails.toEnableSemanticSearch')}
          </div>
        )}

        {/* Content preview */}
        {showPreview && (
          <div>
            <p className="text-xs font-medium text-gray-500 mb-1 flex items-center gap-1"><Eye className="w-3 h-3" /> {t('agentDetails.contentPreview')}</p>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
              {file.content
                ? (file.content.length > 800 ? file.content.slice(0, 800) + '\n…' : file.content)
                : <span className="italic text-gray-400">{t('agentDetails.noContent')}</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MemoryPoolDetails({ pool }) {
  const { t } = useI18n();
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
                <CheckCircle className="w-3 h-3" /> {t('agentDetails.connected')}
              </span>
            </div>
            {pool.description && <p className="text-sm text-gray-500 mt-1 ml-6">{pool.description}</p>}
            <p className="text-xs text-gray-400 mt-1 ml-6">{pool.id}</p>
          </div>
          <a href="/memory" className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50 whitespace-nowrap flex items-center gap-1">
            <ExternalLink className="w-3 h-3" /> {t('agentDetails.manage')}
          </a>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-3 mt-4">
          {[
            { label: t('agentDetails.stats.totalFiles'), value: files.length, icon: FileText, color: 'text-gray-600 bg-gray-50' },
            { label: t('agentDetails.stats.rawText'),    value: rawFiles.length, icon: FileSearch, color: 'text-gray-500 bg-gray-50' },
            { label: t('agentDetails.stats.indexed'),    value: indexedFiles.length, icon: Layers, color: 'text-green-700 bg-green-50' },
            { label: t('agentDetails.stats.vectorized'), value: vectorized.length, icon: Zap, color: 'text-indigo-700 bg-indigo-50' },
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
          <p className="text-sm">{t('agentDetails.noFilesInThisPool')} <strong>{t('agentDetails.sharedMemory')}</strong> {t('agentDetails.page')}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Filter bar */}
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-gray-700">{t('agentDetails.dataSources')}</p>
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
            <p className="text-center text-sm text-gray-400 py-6">{t('agentDetails.noFilesInThisCategory')}</p>
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

// Tool ids the planning toggle owns; static data, kept out of the component so
// the loaders that read it stay stable.
const PLANNING_TOOLS = ['save_plan', 'get_plan', 'list_plans', 'update_plan_status', 'delete_plan'];

const defaultReasoningSettings = { thinkEnabled: false, thinkMode: 'standard', thinkingLevel: 'off', planEnabled: false, planFormat: 'structured' };

/**
 * Shown on the tabs that change what a system agent *is* — its tools and its
 * instructions — rather than merely how it runs. Those two are what the rest of
 * the product is built against, and editing either also detaches the agent from
 * the shipped seed, so it stops receiving updates with new versions.
 */
function SystemAgentWarning({ scope }) {
  const { t } = useI18n();
  return (
    <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
      <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-amber-900">{t('agentDetails.systemAgentWarningTitle')}</p>
        <p className="text-xs text-amber-800 mt-1 leading-relaxed">
          {scope === 'config'
            ? t('agentDetails.systemAgentWarningConfig')
            : t('agentDetails.systemAgentWarningTools')}
        </p>
        <p className="text-xs text-amber-700 mt-1 leading-relaxed">
          {t('agentDetails.systemAgentWarningStopsTracking')}
        </p>
      </div>
    </div>
  );
}

/**
 * The agent's own definition chat, pinned to this agent: the Agent Creator
 * edits instructions.md, capabilities.md, usage.md and the tool list in place,
 * and the editors below pick up the result.
 *
 * The callbacks are memoised on the agent id because EntityChat loads its
 * transcript in an effect keyed on them — fresh closures each render would
 * refetch the conversation continuously.
 */
function useDefinitionChatDescriptor(agentId, workspace, onChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getAgentDefinitionChat(agentId), [agentId]);
  const clearChat = useCallback(() => clearAgentDefinitionChat(agentId), [agentId]);
  const stopChat = useCallback(() => stopAgentDefinitionChat(agentId), [agentId]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'agentdef') onChanged();
  }, [onChanged]);

  return useMemo(() => (agentId ? {
    scope: `agent:${agentId}`,
    path: agentDefinitionChatUrl(agentId, workspace),
    loadChat, clearChat, stopChat, onEvent,
    title: t('agentDetails.definitionChat'),
    emptyHint: t('agentDetails.definitionChatHint'),
    suggestions: [
      t('agentDetails.chatSuggestSharpen'),
      t('agentDetails.chatSuggestCapabilities'),
      t('agentDetails.chatSuggestTools'),
      t('agentDetails.chatSuggestUsage'),
    ],
  } : null), [agentId, workspace, loadChat, clearChat, stopChat, onEvent, t]);
}

const AgentDetails = () => {
  const { t } = useI18n();
  const toast = useToast();
  const { id } = useParams();
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  // The definition chat sits beside the Config tab, folded away by default:
  // most visits to this page are to read, not to rewrite.
  const defChat = useChatColumn(false);
  // Only the Config tab has a chat beside it, so only that tab turns the page
  // into a bounded flex column — the other twelve keep scrolling normally.
  const [agent, setAgent] = useState(null);
  const [history, setHistory] = useState([]);
  const [agentLogsData, setAgentLogsData] = useState({ runs: [], nodes: [] });
  const [tasks, setTasks] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);

  const [agentDefinition, setAgentDefinition] = useState({ system_prompt: '', instructions: '', capabilities: '', usage: '', source: '', definition_dir: '' });
  const [defDraft, setDefDraft] = useState({ instructions: '', capabilities: '', usage: '' });
  const defDraftDirty = useRef({ instructions: false, capabilities: false, usage: false });
  const [defSaving, setDefSaving] = useState({ instructions: false, capabilities: false, usage: false });
  const [defError, setDefError] = useState({ instructions: '', capabilities: '', usage: '' });

  const [memoryType, setMemoryType] = useState('none');
  const [memoryData, setMemoryData] = useState('');
  // Shared memory pools attached to the agent; index 0 is the primary (write) pool.
  const [memoryPools, setMemoryPools] = useState([]);
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
  const [toolsMeta, setToolsMeta] = useState({}); // id -> { label, category, description }
  const [selectedTools, setSelectedTools] = useState([]);
  const toolsDraftDirty = useRef(false);
  const [toolsSaving, setToolsSaving] = useState(false);
  const [toolsMessage, setToolsMessage] = useState('');

  // Delegation allowlist — which agents this agent may hand work to via
  // run_agent_tool. Empty = no restriction (any workspace agent).
  const [allAgents, setAllAgents] = useState([]);
  const [delegates, setDelegates] = useState([]);
  const delegatesDirty = useRef(false);
  const [delegatesSaving, setDelegatesSaving] = useState(false);
  const [delegatesMessage, setDelegatesMessage] = useState('');

  // Episodic write tool (record_episode) — tri-state: 'auto' | 'on' | 'off'.
  const [episodicMode, setEpisodicMode] = useState('auto');
  const [episodicEffective, setEpisodicEffective] = useState(true);
  const [episodicSaving, setEpisodicSaving] = useState(false);

  // Reasoning capability settings (think / plan) — persisted to localStorage per agent
  const REASONING_TOOLS = ['think', 'plan'];
  // The persistent plan-store tools are auto-injected by the backend whenever the
  // Plan capability is on. They are never selectable on their own, so they must be
  // hidden from the Tools tab even if an older agent config still carries them.
  const MEMORY_TOOLS = ['read_memory', 'write_memory'];
  const THINK_MODES = ['standard', 'deep', 'analytical'].map((value) => ({
    value, label: t(`agentDetails.thinkModes.${value}.label`), desc: t(`agentDetails.thinkModes.${value}.desc`),
  }));
  // Native model reasoning level — a model parameter, separate from the think tool.
  const THINKING_LEVELS = ['off', 'low', 'medium', 'high'].map((value) => ({
    value, label: t(`agentDetails.thinkingLevels.${value}.label`), desc: t(`agentDetails.thinkingLevels.${value}.desc`),
  }));
  const PLAN_FORMATS = ['structured', 'bullet', 'numbered', 'freeform'].map((value) => ({
    value, label: t(`agentDetails.planFormats.${value}.label`), desc: t(`agentDetails.planFormats.${value}.desc`),
  }));
  const [reasoningSettings, setReasoningSettings] = useState(defaultReasoningSettings);

  // Structured response format (none | buttons | telegram) — seeded from the
  // loaded agent record, persisted via updateAgentResponseFormat.
  const [responseFormat, setResponseFormat] = useState('none');
  const [responseFormatSaving, setResponseFormatSaving] = useState(false);

  // Chat clarification gate — seeded from the loaded agent record, persisted via
  // updateAgentClarifyGate. When on, the agent asks for missing requirements
  // before executing instead of proceeding on assumptions.
  const [clarifyGate, setClarifyGate] = useState(false);
  const [clarifyGateSaving, setClarifyGateSaving] = useState(false);

  // Self-delegation — when on, the agent may target itself in run_agent_tool /
  // assign_agent_tool (a self-run recurses the same agent). Off by default.
  const [selfDelegation, setSelfDelegation] = useState(false);
  const [selfDelegationSaving, setSelfDelegationSaving] = useState(false);

  // User-defined custom model backends — shown as extra provider options in the
  // per-agent model override picker.
  const [customBackends, setCustomBackends] = useState([]);
  useEffect(() => {
    getCustomBackends().then(({ data }) => setCustomBackends(data.backends || [])).catch(() => {});
  }, []);

  // Default chat agent state
  const [isDefaultChat, setIsDefaultChat] = useState(false);
  const [defaultChatSaving, setDefaultChatSaving] = useState(false);
  const [defaultChatMessage, setDefaultChatMessage] = useState('');

  // Workspace sharing (exposure) state
  // Agent description editing (Overview → Agent Identity)
  const [descDraft, setDescDraft] = useState(null); // null = not editing
  const [descSaving, setDescSaving] = useState(false);

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

  const fetchSkills = useCallback(async (wsName) => {
    if (!wsName) return;
    setSkillsLoading(true);
    try {
      const resp = await getAgentSkills(id, wsName);
      setSkills(resp.data || []);
    } catch {
      setSkills([]);
    }
    setSkillsLoading(false);
  }, [id]);

  const handleToggleSkillsEnabled = async (enabled) => {
    setSkillsConfigSaving(true);
    try {
      await updateAgentSkillsConfig(id, { skills_enabled: enabled });
      setSkillsEnabled(enabled);
    } catch (e) {
      toast.error(t('agentDetails.errors.skillsConfig'), errorDetail(e));
    }
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
      setSkillsMessage(t('agentDetails.skillSaved'));
      setTimeout(() => setSkillsMessage(''), 3000);
    } catch (e) {
      toast.error(t('agentDetails.errors.saveSkill'), errorDetail(e));
    }
    setSkillSaving(false);
  };

  const handleDeleteSkill = async (skillId) => {
    const wsName = selectedWorkspace;
    if (!wsName) return;
    setSkillDeleteBusy(b => ({ ...b, [skillId]: true }));
    try {
      await deleteAgentSkill(id, skillId, wsName);
      setSkills(prev => prev.filter(s => s.id !== skillId));
    } catch (e) {
      toast.error(t('agentDetails.errors.deleteSkill'), errorDetail(e));
    }
    setSkillDeleteBusy(b => ({ ...b, [skillId]: false }));
  };

  const fetchDockerData = useCallback(async () => {
    setDockerLoading(true);
    try {
      const [imagesResp, containersResp] = await Promise.all([
        getContainerImages(),
        getContainers(),
      ]);
      setDockerImages(imagesResp.data?.images || []);
      const allContainers = containersResp.data?.containers || [];
      setDockerContainers(allContainers.filter(c => c.agent_id === id || c.name?.includes(id)));
    } catch (e) {
      toast.error(t('agentDetails.errors.dockerData'), errorDetail(e));
    }
    setDockerLoading(false);
  }, [id, t, toast]);

  const fetchDockerfile = useCallback(async () => {
    setDockerfileLoading(true);
    try {
      const resp = await getDockerfile(id);
      setDockerfileContent(typeof resp.data === 'string' ? resp.data : resp.data);
    } catch {
      setDockerfileContent('');
    }
    setDockerfileLoading(false);
  }, [id]);

  const handleBuildBase = async () => {
    setBuildingBase(true);
    setBuildLog('');
    setBuildError('');
    try {
      const resp = await buildBaseImage({ no_cache: false });
      setBuildLog(resp.data?.log || t('agentDetails.buildComplete'));
    } catch (e) {
      setBuildError(e.response?.data?.detail || e.message || t('agentDetails.buildFailed'));
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
      setBuildLog(resp.data?.log || t('agentDetails.buildComplete'));
    } catch (e) {
      setBuildError(e.response?.data?.detail || e.message || t('agentDetails.buildFailed'));
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
    } catch (e) {
      toast.error(t('agentDetails.errors.stopContainer'), errorDetail(e));
    }
    setDockerActionBusy(b => ({ ...b, [name]: false }));
  };

  const handleRemoveContainer = async (name) => {
    setDockerActionBusy(b => ({ ...b, [name]: true }));
    try {
      await removeContainer(name);
      fetchDockerData();
    } catch (e) {
      toast.error(t('agentDetails.errors.removeContainer'), errorDetail(e));
    }
    setDockerActionBusy(b => ({ ...b, [name]: false }));
  };

  const fetchData = useCallback(async () => {
    try {
      const defaultChatWorkspace = selectedWorkspace || 'default';
      const [agentResp, historyResp, tasksResp, workspacesResp, wsCapResp, logsResp] = await Promise.all([
        getAgent(id, defaultChatWorkspace),
        getAgentHistory(id, workspaceFilter),
        getTasks(workspaceFilter),
        getWorkspaces(),
        getAgentWorkspaceCapacities(id).catch(() => ({ data: {} })),
        getAgentLogs(id, workspaceFilter ? { workspace: workspaceFilter } : undefined).catch(() => ({ data: { runs: [], nodes: [] } })),
      ]);
      setAgent(agentResp.data);
      setIsDefaultChat(!!agentResp.data?.is_default_chat_agent);
      setResponseFormat(agentResp.data?.response_format || 'none');
      setClarifyGate(!!agentResp.data?.clarify_gate);
      setSelfDelegation(!!agentResp.data?.allow_self_delegation);
      setHistory(historyResp.data);
      setAgentLogsData(logsResp.data || { runs: [], nodes: [] });
      setTasks(tasksResp.data || []);
      setWorkspaces(workspacesResp.data || []);
      setWsCapacities(wsCapResp.data || {});
      const savedTools = Array.isArray(agentResp.data?.tools) ? agentResp.data.tools : [];
      if (!toolsDraftDirty.current) {
        // Drop any plan-store ids a legacy config may still carry — they are
        // driven by the Plan capability now, not stored in the tools list.
        setSelectedTools([...new Set(savedTools.filter(t => !PLANNING_TOOLS.includes(t)))]);
      }
      if (!memoryDraftDirty.current) {
        const mt = agentResp.data.memory_type || 'none';
        const md = agentResp.data.memory_data;
        setMemoryType(mt);
        if (mt === 'shared') {
          setMemoryPools(Array.isArray(md) ? md.map(String) : (md ? [String(md)] : []));
          setMemoryData('');
        } else {
          setMemoryPools([]);
          setMemoryData(typeof md === 'string' ? md : JSON.stringify(md || '', null, 2));
        }
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
  }, [id, selectedWorkspace, workspaceFilter]);

  // The definition chat: the column beside the Config tab and the floating page
  // chat are two frames around this one conversation.
  const definitionChat = useDefinitionChatDescriptor(agent?.id, selectedWorkspace, fetchData);
  usePageChat(definitionChat);

  useEffect(() => {
    fetchData();
  }, [id, liveUpdates, workspaceFilter, selectedWorkspace, fetchData]);
  useLiveRefetch(fetchData, { enabled: liveUpdates });

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
        thinkingLevel: r.data.thinking_level || 'off',
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
        const resp = await getTools(selectedWorkspace);
        const list = resp.data?.all || [];
        const meta = {};
        const ids = [];
        list.forEach((t) => {
          const tid = t.id || t.name;
          if (!tid) return;
          ids.push(tid);
          meta[tid] = {
            label: t.label || t.display_name || tid,
            category: t.category || 'other',
            description: t.description || '',
            capabilities: t.capabilities || [],
          };
        });
        setToolsMeta(meta);
        setAvailableTools([...new Set(ids)].sort());
      } catch {
        setToolsMeta({});
        setAvailableTools([]);
      }
    };
    fetchTools();
    // MCP servers are attached per workspace, so the list changes with it.
  }, [selectedWorkspace]);

  // Load the delegation allowlist and the set of agents available to delegate to.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [delResp, agentsResp, epiResp] = await Promise.all([
          getAgentDelegates(id),
          getAgents(selectedWorkspace || undefined),
          getAgentEpisodicConfig(id),
        ]);
        if (cancelled) return;
        setDelegates(delResp.data?.delegates || []);
        const list = agentsResp.data?.agents || agentsResp.data || [];
        setAllAgents(list.filter((a) => (a.id || a) !== id));
        const epiVal = epiResp.data?.episodic_write_enabled;
        setEpisodicMode(epiVal === true ? 'on' : epiVal === false ? 'off' : 'auto');
        setEpisodicEffective(epiResp.data?.effective !== false);
        delegatesDirty.current = false;
        setDelegatesMessage('');
      } catch {
        if (!cancelled) { setDelegates([]); setAllAgents([]); }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [id, selectedWorkspace]);

  const handleUpdateMemory = async () => {
    setIsUpdatingMemory(true);
    try {
      let data;
      if (memoryType === 'shared') {
        // Primary pool first; a single pool is sent as a plain id string.
        data = memoryPools.length === 1 ? memoryPools[0] : memoryPools;
      } else {
        data = memoryData;
        try {
          data = JSON.parse(memoryData);
        } catch {
          // keep as string
        }
      }
      await updateAgentMemory(id, { memory_type: memoryType, memory_data: data, workspace: selectedWorkspace || 'default' });
      memoryDraftDirty.current = false;
      fetchData();
    } catch (error) {
      console.error('Error updating memory:', error);
    } finally {
      setIsUpdatingMemory(false);
    }
  };

  // Pool list editing — index 0 is the primary (write) pool.
  const setPrimaryPool = (pid) => {
    memoryDraftDirty.current = true;
    setMemoryPools(prev => (pid ? [pid, ...prev.filter(p => p && p !== pid)] : prev.slice(1)));
  };

  const addExtraPool = (pid) => {
    if (!pid) return;
    memoryDraftDirty.current = true;
    setMemoryPools(prev => (prev.includes(pid) ? prev : [...prev, pid]));
  };

  const removePool = (pid) => {
    memoryDraftDirty.current = true;
    setMemoryPools(prev => prev.filter(p => p !== pid));
  };

  const handleEraseMemory = async () => {
    if (!window.confirm(t('agentDetails.confirmEraseMemory'))) return;
    setIsUpdatingMemory(true);
    try {
      await eraseAgentMemory(id, selectedWorkspace || 'default');
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
      alert(t('agentDetails.errors.saveCapacity'));
    } finally {
      setWsCapacitySaving(null);
    }
  };

  const handleSaveDescription = async () => {
    setDescSaving(true);
    try {
      const { data } = await updateAgentDescription(id, descDraft);
      setAgent(prev => ({ ...prev, description: data.description }));
      setDescDraft(null);
    } catch {
      alert(t('agentDetails.errors.saveDescription'));
    } finally {
      setDescSaving(false);
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
        setDefaultChatMessage(t('agentDetails.defaultCleared'));
      }
      setTimeout(() => setDefaultChatMessage(''), 3000);
    } catch {
      setDefaultChatMessage(t('agentDetails.errors.defaultChatAgent'));
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
        ? t('agentDetails.nowPublished')
        : t('agentDetails.nowPrivate'));
      setTimeout(() => setSharingMessage(''), 3000);
    } catch (e) {
      setSharingMessage(e.response?.data?.detail || t('agentDetails.errors.sharing'));
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
  }, [activeTab, fetchDockerData, fetchDockerfile]);

  // Load skills when the skills tab opens
  useEffect(() => {
    if (activeTab !== 'skills') return;
    fetchSkills(selectedWorkspace);
  }, [activeTab, fetchSkills, selectedWorkspace]);

  // Sync skills_enabled from agent spec
  useEffect(() => {
    if (agent) setSkillsEnabled(!!agent.skills_enabled);
  }, [agent]);

  // Sync shared (workspace exposure) from agent spec
  useEffect(() => {
    if (agent) setShared(!!agent.shared);
  }, [agent]);

  // Load full pool details whenever the primary pool changes
  useEffect(() => {
    const poolId = memoryType === 'shared' ? (memoryPools[0] || '').trim() : '';
    if (!poolId) { setConnectedPool(null); return; }
    setLoadingPool(true);
    getSharedMemory(poolId)
      .then(r => setConnectedPool(r.data))
      .catch(() => setConnectedPool(null))
      .finally(() => setLoadingPool(false));
  }, [memoryType, memoryPools]);


  const handleStartNode = async () => {
    setStartingNode(true);
    try {
      await startNode({ agent_id: id, workspace: startWorkspace || null, label: startLabel || null });
      setShowStartNodeModal(false);
      setStartWorkspace('');
      setStartLabel('');
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || t('agentDetails.errors.startNode'));
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
      alert(error.response?.data?.detail || error.message || t('agentDetails.errors.stopNode'));
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const handleDeleteNode = async (nodeId) => {
    if (!window.confirm(t('agentDetails.confirmRemoveNode'))) return;
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'deleting' }));
    try {
      await deleteNode(nodeId);
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || t('agentDetails.errors.removeNode'));
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
      setLogsNodeText(error.response?.data?.detail || t('agentDetails.errors.nodeLogs'));
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

  // Turn every tool in a category on (enable=true) or off (enable=false).
  const setCategoryTools = (toolIds, enable) => {
    setSelectedTools((prev) => {
      const next = new Set(prev);
      toolIds.forEach((t) => (enable ? next.add(t) : next.delete(t)));
      return [...next];
    });
    toolsDraftDirty.current = true;
    setToolsMessage('');
  };

  const handleSaveTools = async () => {
    setToolsSaving(true);
    setToolsMessage('');
    try {
      await updateAgentTools(id, { tools: selectedTools });
      setToolsMessage(t('agentDetails.toolsUpdated'));
      toolsDraftDirty.current = false;
      await fetchData();
    } catch (error) {
      // A capability violation comes back as a 409 whose detail is the
      // structured violation, not a string — render its message rather than
      // "[object Object]".
      const detail = error.response?.data?.detail;
      setToolsMessage(
        typeof detail === 'string'
          ? detail
          : detail?.message || t('agentDetails.errors.updateTools')
      );
    } finally {
      setToolsSaving(false);
    }
  };

  const toggleDelegate = (agentId) => {
    setDelegates((prev) => (
      prev.includes(agentId) ? prev.filter((a) => a !== agentId) : [...prev, agentId]
    ));
    delegatesDirty.current = true;
    setDelegatesMessage('');
  };

  const handleSaveDelegates = async () => {
    setDelegatesSaving(true);
    setDelegatesMessage('');
    try {
      const { data } = await updateAgentDelegates(id, delegates);
      setDelegates(data?.delegates || []);
      delegatesDirty.current = false;
      setDelegatesMessage(
        (data?.delegates || []).length
          ? t('agentDetails.delegationRestricted')
          : t('agentDetails.delegationUnrestricted')
      );
    } catch (error) {
      setDelegatesMessage(error.response?.data?.detail || t('agentDetails.errors.updateDelegation'));
    } finally {
      setDelegatesSaving(false);
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
        [field]: error.response?.data?.detail || error.message || t('agentDetails.errors.save'),
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
        if (!data.models?.length) setLocalModelsError(t('agentDetails.connectedNoModels'));
      } else {
        setLocalModelsError(data.error || t('agentDetails.connectionFailed'));
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
      setModelMessage(t('agentDetails.modelSettingsSaved'));
      setTimeout(() => setModelMessage(''), 3000);
    } catch (error) {
      setModelMessage(error.response?.data?.detail || t('agentDetails.errors.save'));
    } finally {
      setModelSaving(false);
    }
  };

  if (loading) return <div className="text-center py-10">{t('agentDetails.loadingAgentDetails')}</div>;
  if (!agent) return <div className="text-center py-10">{t('agentDetails.agentNotFound')}</div>;

  const activeTask = history.find(r => r.status === 'running');
  const agentTools = Array.isArray(agent?.tools) ? agent.tools : [];
  const mergedTools = [...new Set([...agentTools])];
  const visibleToolIds = [...new Set([...availableTools, ...mergedTools, ...selectedTools])];
  const toolsDirty = toolsDraftDirty.current || ([...selectedTools].sort().join('|') !== [...mergedTools].sort().join('|'));
  // Blocked capability combination formed by the current selection, evaluated
  // client-side so it appears while toggling. The server refuses the save
  // regardless (409) unless the agent carries capability_override.
  const capabilityViolation = checkCombination(selectedTools, toolsMeta);
  const capabilityOverridden = Boolean(agent?.capability_override);

  // Group regular (non-reasoning, non-memory) tools by category for the Tools tab.
  const regularToolIds = visibleToolIds.filter(t => !REASONING_TOOLS.includes(t) && !PLANNING_TOOLS.includes(t) && !MEMORY_TOOLS.includes(t));
  const toolCategories = (() => {
    const groups = {};
    regularToolIds.forEach((tid) => {
      const cat = toolsMeta[tid]?.category || 'other';
      (groups[cat] = groups[cat] || []).push(tid);
    });
    return Object.entries(groups)
      .map(([category, ids]) => ({ category, ids: ids.sort() }))
      .sort((a, b) => a.category.localeCompare(b.category));
  })();
  const formatCategory = (c) => c.replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
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
    <PageContainer>
      <PageHeader
        icon={Users}
        title={agent.name}
        description={agent.id}
        backTo="/agents"
        backLabel={t('agentDetails.agents')}
      />

      <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-indigo-600 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Nodes card */}
          <div className={`border rounded-lg px-3 py-2 ${nodesOverWsCap ? 'bg-orange-50 border-orange-300' : 'bg-indigo-50 border-indigo-100'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${nodesOverWsCap ? 'text-orange-500' : 'text-indigo-500'}`}>{t('agentDetails.nodes')}</div>
            <div className={`text-sm font-semibold ${nodesOverWsCap ? 'text-orange-900' : 'text-indigo-900'}`}>
              {runningNodesCount} / {isDefaultWorkspace ? '∞' : wsSessionCap} running
            </div>
            {nodesOverWsCap && (
              <div className="text-[10px] text-orange-600 mt-0.5 font-medium">{t('agentDetails.exceedsWorkspaceCap')}</div>
            )}
          </div>

          {/* Sessions card */}
          <div className={`border rounded-lg px-3 py-2 ${atCapacity ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-100'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${atCapacity ? 'text-red-500' : 'text-green-500'}`}>{t('agentDetails.sessions')}</div>
            {noNodes ? (
              <div className="text-xs text-amber-700 font-medium mt-0.5">{t('agentDetails.noNodesRunningStartA')}</div>
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
                  {atCapacity ? t('agentDetails.atCapacityNoSlots') : t('agentDetails.load', { pct: maxSessions === Infinity ? '—' : `${sessionLoadFactor}%` })}
                </div>
              </>
            )}
          </div>

          {/* Running tasks card */}
          <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-amber-500 font-semibold">{t('agentDetails.runningTasks')}</div>
            <div className="text-sm font-semibold text-amber-900">{runningTasks.length}</div>
            <div className="text-[10px] text-amber-600 mt-0.5">{t('agentDetails.totalAssigned', { count: agentTasks.length })}</div>
          </div>
        </div>
      </div>

      <div className="border-b border-gray-200 flex items-end justify-between gap-4">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'overview', label: t('agentDetails.tabs.overview'), icon: Activity },
            // The live copies of *this* agent. The History tab below is their
            // journal; this one is what is running right now.
            { id: 'instances', label: t('agentDetails.tabs.instances'), icon: Radio },
            { id: 'history', label: t('agentDetails.tabs.history'), icon: History },
            { id: 'logs', label: t('agentDetails.tabs.logs'), icon: FileText },
            { id: 'model', label: t('agentDetails.tabs.model'), icon: BrainCircuit },
            { id: 'memory', label: t('agentDetails.tabs.memory'), icon: Database },
            { id: 'tools', label: t('agentDetails.tabs.tools'), icon: Wrench },
            { id: 'commands', label: t('agentDetails.tabs.commands'), icon: Terminal },
            { id: 'nodes', label: t('agentDetails.tabs.nodes'), icon: Server },
            { id: 'tasks', label: t('agentDetails.tabs.tasks'), icon: Clock },
            { id: 'skills', label: t('agentDetails.tabs.skills'), icon: BookOpen },
            { id: 'config', label: t('agentDetails.tabs.config'), icon: FileCode },
            { id: 'docker', label: t('agentDetails.tabs.docker'), icon: Layers },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center px-4 py-2 first:pl-0 text-sm font-semibold border-b-2 transition-colors ${
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
        {/* The definition chat belongs to the Config tab only, so its toggle
            rides beside the tabs and disappears with them. */}
        {activeTab === 'config' && (
          <div className="pb-2 shrink-0">
            <ChatToggle open={defChat.open} onToggle={defChat.toggle}
                        label={t('agentDetails.definitionChat')} />
          </div>
        )}
      </div>

      {activeTab === 'overview' && (
        <div className="space-y-6">

          {/* Imported agents lead with their readiness: for one that is not yet
              runnable, this panel is the whole remaining setup. */}
          <ImportedAgentPanel
            agent={agent}
            workspace={selectedWorkspace}
            onUpdated={fetchData}
          />

          {/* ── Agent Identity ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <Activity className="w-4 h-4 text-indigo-500" /> {t('agentDetails.agentIdentity')}
            </h3>
            <div className="mb-5">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('agentDetails.description')}</span>
                {descDraft === null && (
                  <button onClick={() => setDescDraft(agent.description || '')}
                    className="text-xs text-indigo-600 hover:text-indigo-800">{t('agentDetails.edit')}</button>
                )}
              </div>
              {descDraft === null ? (
                agent.description
                  ? <p className="text-base text-gray-600 leading-relaxed">{agent.description}</p>
                  : <p className="text-sm text-gray-400 italic">{t('agentDetails.noDescriptionYet')}</p>
              ) : (
                <div className="space-y-2">
                  <textarea value={descDraft} rows={3}
                    onChange={e => setDescDraft(e.target.value)}
                    placeholder={t('agentDetails.whatDoesThisAgentDo')}
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
                  />
                  <div className="flex items-center gap-2">
                    <button onClick={handleSaveDescription} disabled={descSaving}
                      className="text-xs text-white bg-indigo-600 hover:bg-indigo-700 px-2 py-1 rounded disabled:opacity-50">
                      {descSaving ? '...' : 'Save'}
                    </button>
                    <button onClick={() => setDescDraft(null)} disabled={descSaving}
                      className="text-xs text-gray-500 hover:text-gray-700">{t('agentDetails.cancel')}</button>
                  </div>
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">ID</span>
                <span className=" text-gray-700 text-xs break-all">{agent.id}</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('agentDetails.domain')}</span>
                <span className=" text-gray-700 text-xs">{agent.domain || 'general'}</span>
              </div>
              {selectedWorkspace && selectedWorkspace !== 'default' && (
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('agentDetails.workspaceCapacity')}</span>
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
                          className="text-xs text-gray-500 hover:text-gray-700">{t('agentDetails.cancel')}</button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{t('agentDetails.concurrentRuns', { count: effectiveCapacity })}</span>
                        <button onClick={() => setWsCapacityEdits(prev => ({ ...prev, [selectedWorkspace]: String(effectiveCapacity) }))}
                          className="text-xs text-indigo-600 hover:text-indigo-800">{t('agentDetails.edit')}</button>
                      </div>
                    );
                  })()}
                </div>
              )}
            </div>
          </div>

          {/* ── Marketplace publishing ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              {shared ? <Globe className="w-4 h-4 text-indigo-500" /> : <Lock className="w-4 h-4 text-indigo-500" />}
              Marketplace
            </h3>
            {agent.system ? (
              <p className="text-sm text-gray-500 flex items-center gap-2">
                <Globe className="w-4 h-4 text-gray-400" />
                {t('agentDetails.systemAgentsAreAvailableIn')}
              </p>
            ) : (
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800">
                      {shared ? t('agentDetails.publishedToMarketplace') : t('agentDetails.privateToWorkspace')}
                    </p>
                    <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                      {shared
                        ? <>{t('agentDetails.visibleToEveryone')} <Link to="/marketplace" className="text-indigo-600 hover:text-indigo-800 font-semibold">{t('agentDetails.marketplace')}</Link> {t('agentDetails.andCanBeAddedTo')}</>
                        : agent.owner_workspace
                          ? <>{t('agentDetails.onlyVisibleIn')} <span className="font-semibold text-gray-700">{agent.owner_workspace}</span> {t('agentDetails.andCannotBeAddedTo')}</>
                          : t('agentDetails.notBoundToWorkspace')}
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
                  <Wrench className="w-4 h-4 text-indigo-500" /> {t('agentDetails.tools')}
                </h3>
                <button type="button" onClick={() => setActiveTab('tools')}
                  className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50">
                  {t('agentDetails.manage')}
                </button>
              </div>
              <div className="flex items-center gap-6 mb-4">
                <div className="text-center">
                  <p className="text-3xl font-bold text-indigo-700">{selectedTools.length}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{t('agentDetails.enabled')}</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-bold text-gray-300">{availableTools.length}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{t('agentDetails.available')}</p>
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
                <p className="text-xs text-gray-400 italic">{t('agentDetails.noToolsEnabledClickManage')}</p>
              )}
            </div>

            {/* Memory */}
            <div className="bg-white p-5 shadow-md rounded-lg">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                  <Database className="w-4 h-4 text-amber-500" /> {t('agentDetails.memory')}
                </h3>
                <button type="button" onClick={() => setActiveTab('memory')}
                  className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50">
                  {t('agentDetails.configure')}
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
                  {memoryType === 'none' ? t('agentDetails.noMemory') : memoryType === 'local' ? t('agentDetails.localAgentSpecific') : t('agentDetails.sharedPool')}
                </span>
                {memoryType === 'shared' && memoryPools.length > 0 && (
                  <div className="text-xs text-gray-500 truncate">
                    {memoryPools.length === 1
                      ? t('agentDetails.poolSingle', { pool: memoryPools[0] })
                      : t('agentDetails.poolMulti', { count: memoryPools.length, pool: memoryPools[0] })}
                  </div>
                )}
                {memoryType === 'local' && memoryData && (
                  <div className="text-xs text-gray-500 italic line-clamp-2">{memoryData.slice(0, 120)}{memoryData.length > 120 ? '…' : ''}</div>
                )}
                {memoryType === 'none' && (
                  <p className="text-xs text-gray-400">{t('agentDetails.noMemoryPersistenceBetweenSessions')}</p>
                )}
              </div>
            </div>
          </div>

          {activeTask && (
            <div className="bg-indigo-50 p-4 rounded-lg border border-indigo-100">
              <h3 className="text-indigo-800 font-bold flex items-center mb-2">
                <Clock className="w-4 h-4 mr-2" /> {t('agentDetails.currentlyActive')}
              </h3>
              <p className="text-sm text-indigo-900 font-medium truncate mb-2">{t('agentDetails.taskId')}: {activeTask.task_id}</p>
              <Link to={`/tasks/${activeTask.task_id}`} className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 inline-block">
                {t('agentDetails.viewTaskDetails')}
              </Link>
            </div>
          )}

          {/* ── Chat Settings ── */}
          {(
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-1">
                <MessageSquare className="w-4 h-4 text-indigo-500" /> {t('agentDetails.chatSettings')}
              </h3>
              <p className="text-xs text-gray-500 mb-4">{t('agentDetails.configureHowThisAgentAppears')}</p>
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
                  <div className="text-sm font-medium text-gray-800">{t('agentDetails.defaultChatAgent')}</div>
                  <div className="text-xs text-gray-500">{t('agentDetails.preSelectThisAgentFor')}</div>
                </div>
              </label>
              {defaultChatMessage && (
                <p className="mt-3 text-xs text-indigo-600">{defaultChatMessage}</p>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === 'instances' && (
        <InstanceList
          agentId={id}
          workspace={workspaceFilter}
          liveUpdates={liveUpdates}
          showAgentColumn={false}
        />
      )}

      {activeTab === 'history' && (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <History className="w-5 h-5 mr-2" /> {t('agentDetails.executionHistory')}
            </h3>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.status')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.taskId')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.startedAt')}</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {history.length === 0 ? (
                    <tr>
                      <td colSpan="4" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noExecutionHistoryFoundFor')}</td>
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
                                <History className="w-3 h-3" /> {t('agentDetails.session')}
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
            <div className="text-sm font-semibold text-indigo-900 mb-1">{t('agentDetails.nodeScopedLogs')}</div>
            <div className="text-xs text-indigo-700">
              {t('agentDetails.nodeScopedLogsHint')} <code>agents/state/node_runs/&lt;node_id&gt;/</code>.
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <FileText className="w-5 h-5 mr-2 text-indigo-600" /> {t('agentDetails.runLogs')}
            </h3>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.status')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.node')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.task')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.logFile')}</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {(agentLogsData.runs || []).length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noRunLogsFoundFor')}</td>
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
                              <MessageSquare className="w-3 h-3" /> {t('agentDetails.message')}
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
              <Server className="w-5 h-5 mr-2 text-indigo-600" /> {t('agentDetails.nodeProcessLogs')}
            </h3>
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.node')}</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.status')}</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.workspace')}</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.logFile')}</th>
                    <th className="text-right px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {(agentLogsData.nodes || []).length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noNodeLogsFoundFor')}</td>
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
                              <Terminal className="w-3 h-3 mr-1" /> {t('agentDetails.open')}
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
            <h3 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
              <Database className="w-5 h-5 text-amber-500" /> {t('agentDetails.memoryConfiguration')}
            </h3>
            <p className="text-xs text-gray-400 mb-4">
              Memory is assigned per workspace — this configuration applies in <span className="font-semibold text-gray-500">{selectedWorkspace || 'default'}</span> {t('agentDetails.only')}
            </p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.memoryType')}</label>
                <select
                  value={memoryType}
                  onChange={(e) => { setMemoryType(e.target.value); memoryDraftDirty.current = true; if (e.target.value !== 'shared') setConnectedPool(null); }}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="none">{t('agentDetails.none')}</option>
                  <option value="local">{t('agentDetails.localAgentSpecificOption')}</option>
                  <option value="shared">{t('agentDetails.sharedMemoryPool')}</option>
                </select>
              </div>

              {memoryType === 'shared' && (() => {
                const poolNameById = Object.fromEntries(sharedMemories.map(m => [m.id, m.name]));
                const primary = memoryPools[0] || '';
                const extras = memoryPools.slice(1);
                const unattached = sharedMemories.filter(m => !memoryPools.includes(m.id));
                return (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.primaryPool')}</label>
                      <p className="text-xs text-gray-400 mb-1.5">{t('agentDetails.primaryPoolHint')}</p>
                      {sharedMemories.length > 0 ? (
                        <select
                          value={primary}
                          onChange={(e) => setPrimaryPool(e.target.value)}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                          <option value="">{t('agentDetails.selectAMemoryPool')}</option>
                          {sharedMemories.map(m => (
                            <option key={m.id} value={m.id}>
                              {m.name}  ({t('agentDetails.fileCount', { count: (m.files || []).length })})
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={primary}
                          onChange={(e) => setPrimaryPool(e.target.value.trim())}
                          placeholder={t('agentDetails.sharedMemoryPoolIdUuid')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                      )}
                      {primary && (
                        <p className="text-xs text-gray-400 mt-1 truncate">ID: {primary}</p>
                      )}
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.additionalPools')}</label>
                      <p className="text-xs text-gray-400 mb-1.5">{t('agentDetails.additionalPoolsHint')}</p>
                      {extras.length > 0 && (
                        <div className="space-y-1.5 mb-2">
                          {extras.map(pid => (
                            <div key={pid} className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5">
                              <span className="text-sm text-gray-700 truncate flex-1" title={pid}>
                                {poolNameById[pid] || pid}
                              </span>
                              <button type="button" onClick={() => setPrimaryPool(pid)}
                                className="text-xs text-indigo-600 hover:text-indigo-800 shrink-0">
                                {t('agentDetails.makePrimary')}
                              </button>
                              <button type="button" onClick={() => removePool(pid)}
                                className="text-gray-400 hover:text-red-500 shrink-0" title={t('agentDetails.detachPool')}>
                                <X className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      {unattached.length > 0 ? (
                        <select
                          value=""
                          onChange={(e) => addExtraPool(e.target.value)}
                          className="w-full border border-dashed border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                          <option value="">{t('agentDetails.attachAnotherPool')}</option>
                          {unattached.map(m => (
                            <option key={m.id} value={m.id}>{m.name}</option>
                          ))}
                        </select>
                      ) : extras.length === 0 ? (
                        <p className="text-xs text-gray-400 italic">{t('agentDetails.noOtherPoolsAvailableTo')}</p>
                      ) : null}
                    </div>
                  </>
                );
              })()}

              {memoryType === 'local' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.memoryContent')}</label>
                  <textarea
                    value={memoryData}
                    onChange={(e) => { setMemoryData(e.target.value); memoryDraftDirty.current = true; }}
                    placeholder={t('agentDetails.enterMemoryContentOrConfiguration')}
                    rows={5}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              )}

              <div className="flex gap-2 pt-1">
                <button
                  onClick={handleUpdateMemory}
                  disabled={isUpdatingMemory || (memoryType === 'shared' && memoryPools.length === 0)}
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
                  <Trash2 className="w-4 h-4" /> {t('agentDetails.erase')}
                </button>
              </div>
            </div>
          </div>

          {/* ── Memory Tools ── */}
          {memoryType === 'shared' && memoryPools.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-sm font-bold text-gray-900 mb-3 flex items-center gap-2">
                <Wrench className="w-4 h-4 text-indigo-500" /> {t('agentDetails.memoryTools')}
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* read_memory — always on */}
                <div className="p-3 border-2 border-green-200 bg-green-50 rounded-xl flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-gray-900">{t('agentDetails.readMemory2')}</div>
                    <div className="text-xs text-gray-500 mt-0.5">{t('agentDetails.readFilesNotesAndKey')}</div>
                  </div>
                  <span className="px-2.5 py-1 rounded-full text-xs font-semibold border bg-green-600 text-white border-green-600 shrink-0">
                    {t('agentDetails.alwaysOn')}
                  </span>
                </div>
                {/* write_memory — toggleable */}
                {(() => {
                  const enabled = selectedTools.includes('write_memory');
                  return (
                    <div className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${enabled ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                      <div>
                        <div className="text-sm font-semibold text-gray-900">{t('agentDetails.writeMemory')}</div>
                        <div className="text-xs text-gray-500 mt-0.5">{t('agentDetails.createOrUpdateFilesNotes')}</div>
                      </div>
                      <button
                        type="button"
                        onClick={async () => {
                          const next = enabled
                            ? selectedTools.filter(t => t !== 'write_memory')
                            : [...selectedTools, 'write_memory'];
                          setSelectedTools(next);
                          setToolsSaving(true);
                          try { await updateAgentTools(id, { tools: next }); await fetchData(); } catch (e) { toast.error(t('agentDetails.errors.updateTools'), errorDetail(e)); } finally { setToolsSaving(false); }
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
                {/* record_episode — episodic write tool, tri-state selector */}
                <div className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${episodicEffective ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-gray-900">{t('agentDetails.episodicWrite')}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {t('agentDetails.episodicHintBefore')}
                      {' '}<span className="font-medium">{t('agentDetails.auto')}</span> {t('agentDetails.episodicHintAuto')}
                      {' '}{t('agentDetails.currently')} <span className="font-semibold">{episodicEffective ? t('agentDetails.active') : t('agentDetails.inactive')}</span>.
                    </div>
                  </div>
                  <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden shrink-0">
                    {['auto', 'on', 'off'].map((m) => (
                      <button
                        key={m}
                        type="button"
                        disabled={episodicSaving}
                        onClick={async () => {
                          if (m === episodicMode) return;
                          const prev = episodicMode;
                          setEpisodicMode(m);
                          setEpisodicSaving(true);
                          try {
                            const val = m === 'on' ? true : m === 'off' ? false : null;
                            const { data } = await updateAgentEpisodicConfig(id, val);
                            const ev = data?.episodic_write_enabled;
                            setEpisodicMode(ev === true ? 'on' : ev === false ? 'off' : 'auto');
                            setEpisodicEffective(data?.effective !== false);
                            await fetchData();
                          } catch { setEpisodicMode(prev); }
                          finally { setEpisodicSaving(false); }
                        }}
                        className={`px-2.5 py-1 text-xs font-semibold capitalize ${
                          episodicMode === m ? 'bg-indigo-600 text-white' : 'bg-white text-gray-500 hover:bg-gray-50'
                        }`}
                      >
                        {t(`agentDetails.episodicModes.${m}`)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ── Connected pool details ── */}
          {memoryType === 'shared' && (
            loadingPool ? (
              <div className="flex items-center justify-center h-32 bg-white rounded-xl border border-gray-200">
                <Loader className="w-5 h-5 animate-spin text-indigo-400 mr-2" />
                <span className="text-sm text-gray-500">{t('agentDetails.loadingPool')}</span>
              </div>
            ) : connectedPool ? (
              <div>
                {memoryPools.length > 1 && (
                  <p className="text-xs text-gray-400 mb-2">{t('agentDetails.showingThePrimaryPoolAdditional')} <Link to="/memory" className="text-indigo-600 hover:text-indigo-800">{t('agentDetails.sharedMemory')}</Link> {t('agentDetails.page')}</p>
                )}
                <MemoryPoolDetails pool={connectedPool} />
              </div>
            ) : memoryPools[0] ? (
              <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 shrink-0" />
                Pool not found. Check the ID or create a pool in <strong>{t('agentDetails.sharedMemory')}</strong>.
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center bg-white rounded-xl border border-dashed border-gray-200 p-10 text-center text-gray-400">
                <Database className="w-10 h-10 mb-3 opacity-20" />
                <p className="text-sm">{t('agentDetails.selectASharedMemoryPool')}</p>
              </div>
            )
          )}
        </div>
      )}

      {activeTab === 'tools' && (
        <div className="space-y-6">
          {agent.system && <SystemAgentWarning scope="tools" />}

          {/* ── Agent Behavior ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold flex items-center mb-1">
              <BrainCircuit className="w-5 h-5 mr-2 text-violet-600" />
              {t('agentDetails.agentBehavior')}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              {t('agentDetails.configureHowThisAgentThinks')}
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Think card */}
              {(() => {
                const thinkToolOn = reasoningSettings.thinkEnabled;
                const nativeOn = (reasoningSettings.thinkingLevel || 'off') !== 'off';
                // Enable/disable applies to the whole Think block (native + tool).
                const blockEnabled = nativeOn || thinkToolOn;

                // Persist a partial reasoning change (state + server) in one place.
                const patchReasoning = (patch) => {
                  setReasoningSettings({ ...reasoningSettings, ...patch });
                  const payload = {};
                  if ('thinkingLevel' in patch) payload.thinking_level = patch.thinkingLevel;
                  if ('thinkEnabled' in patch) payload.think_enabled = patch.thinkEnabled;
                  if ('thinkMode' in patch) payload.think_mode = patch.thinkMode;
                  updateAgentReasoning(id, payload).catch(() => {});
                };

                // Tool segmented switcher: an "Off" segment plus the depth modes.
                const toolSegments = [{ value: 'off', label: t('agentDetails.off'), desc: t('agentDetails.noScratchpadTool') }, ...THINK_MODES];

                const segBtn = (selected, onClick, label, desc) => (
                  <button
                    key={label}
                    type="button"
                    title={desc}
                    onClick={onClick}
                    className={`flex-1 px-2 py-1.5 text-xs font-semibold border-l first:border-l-0 border-gray-200 transition-colors ${
                      selected ? 'bg-violet-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {label}
                  </button>
                );

                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${blockEnabled ? 'border-violet-300 bg-violet-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${blockEnabled ? 'bg-violet-100' : 'bg-gray-200'}`}>
                          <BrainCircuit className={`w-4 h-4 ${blockEnabled ? 'text-violet-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.think')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.thinkBlockHint')}</div>
                        </div>
                      </div>
                      {/* Enable/disable the whole block: turns both native
                          thinking and the scratchpad tool off. */}
                      <button
                        type="button"
                        onClick={() => blockEnabled
                          ? patchReasoning({ thinkingLevel: 'off', thinkEnabled: false })
                          : patchReasoning({ thinkingLevel: 'medium' })}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          blockEnabled ? 'bg-violet-600 text-white border-violet-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {blockEnabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      <span className="font-medium">{t('agentDetails.nativeThinking')}</span> is a model parameter — how much the model reasons on its
                      own. The <span className="font-medium">{t('agentDetails.thinkTool')}</span> is a separate scratchpad the agent can call; pick its
                      depth or turn it off.
                    </p>

                    {blockEnabled && (
                      <div className="grid grid-cols-2 gap-3">
                        {/* Native thinking column */}
                        <div>
                          <label className="block text-xs font-medium text-gray-700 mb-1">{t('agentDetails.nativeThinking')}</label>
                          <div className="flex rounded-lg border border-gray-300 overflow-hidden bg-white">
                            {THINKING_LEVELS.filter(m => m.value !== 'off').map(m =>
                              segBtn(
                                reasoningSettings.thinkingLevel === m.value,
                                () => patchReasoning({ thinkingLevel: m.value }),
                                m.label,
                                m.desc,
                              )
                            )}
                          </div>
                        </div>
                        {/* Think tool column */}
                        <div>
                          <label className="block text-xs font-medium text-gray-700 mb-1">{t('agentDetails.thinkTool2')}</label>
                          <div className="flex rounded-lg border border-gray-300 overflow-hidden bg-white">
                            {toolSegments.map(m =>
                              m.value === 'off'
                                ? segBtn(!thinkToolOn, () => patchReasoning({ thinkEnabled: false }), m.label, m.desc)
                                : segBtn(
                                    thinkToolOn && reasoningSettings.thinkMode === m.value,
                                    () => patchReasoning({ thinkEnabled: true, thinkMode: m.value }),
                                    m.label,
                                    m.desc,
                                  )
                            )}
                          </div>
                        </div>
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
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.plan')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.upfrontStructuredPlanning')}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const next = { ...reasoningSettings, planEnabled: !enabled };
                          setReasoningSettings(next);
                          // The plan / save_plan / get_plan / list_plans / update_plan_status /
                          // delete_plan tools are auto-injected by the backend whenever this
                          // capability is on, so toggling the flag is all that's needed — they
                          // are not stored in the agent's regular tools list.
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
                      {t('agentDetails.letsTheAgentProduceA')}
                    </p>
                    {enabled && (
                      <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">{t('agentDetails.planningFormat')}</label>
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

              {/* Clarify card */}
              {(() => {
                const enabled = clarifyGate;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-amber-300 bg-amber-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-amber-100' : 'bg-gray-200'}`}>
                          <HelpCircle className={`w-4 h-4 ${enabled ? 'text-amber-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.clarify')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.askBeforeActingOnGaps')}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={clarifyGateSaving}
                        onClick={() => {
                          const next = !enabled;
                          const prev = enabled;
                          setClarifyGate(next);
                          setClarifyGateSaving(true);
                          updateAgentClarifyGate(id, next)
                            .then((r) => setClarifyGate(!!r.data?.clarify_gate))
                            .catch(() => setClarifyGate(prev))
                            .finally(() => setClarifyGateSaving(false));
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 disabled:opacity-50 ${
                          enabled ? 'bg-amber-500 text-white border-amber-500' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600">
                      When the agent lacks key information, it asks a few clarifying questions and waits instead of guessing. In a task it pauses (awaiting input) until you answer; in chat it asks and continues once you reply.
                    </p>
                  </div>
                );
              })()}

              {/* Self-delegation card */}
              {(() => {
                const enabled = selfDelegation;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-indigo-100' : 'bg-gray-200'}`}>
                          <Repeat className={`w-4 h-4 ${enabled ? 'text-indigo-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.selfDelegation')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.letThisAgentCallItself')}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={selfDelegationSaving}
                        onClick={() => {
                          const next = !enabled;
                          const prev = enabled;
                          setSelfDelegation(next);
                          setSelfDelegationSaving(true);
                          updateAgentSelfDelegation(id, next)
                            .then((r) => setSelfDelegation(!!r.data?.allow_self_delegation))
                            .catch(() => setSelfDelegation(prev))
                            .finally(() => setSelfDelegationSaving(false));
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 disabled:opacity-50 ${
                          enabled ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600">
                      Allows the agent to target its own id in <code>{t('agentDetails.runAgentTool')}</code> / <code>{t('agentDetails.assignAgentTool')}</code>. Off by default because a self-run recurses the same agent. Enable only for agents meant to hand a sub-goal back to themselves, and keep an eye on runaway loops.
                    </p>
                  </div>
                );
              })()}
            </div>
          </div>

          {/* ── Response Format ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold flex items-center mb-1">
              <MessageSquare className="w-5 h-5 mr-2 text-teal-600" />
              {t('agentDetails.responseFormat')}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Let this agent reply with interactive UI (buttons / a Telegram inline keyboard)
              instead of plain text. When enabled, the agent is taught a structured-reply
              convention; surfaces that understand it (web chat, Telegram) render the buttons,
              others fall back to text.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {[
                ...['none', 'buttons', 'telegram', 'views'].map((value) => ({
                  value,
                  label: t(`agentDetails.responseFormats.${value}.label`),
                  desc: t(`agentDetails.responseFormats.${value}.desc`),
                })),
              ].map((opt) => {
                const active = responseFormat === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={responseFormatSaving}
                    onClick={() => {
                      if (active) return;
                      const prev = responseFormat;
                      setResponseFormat(opt.value);
                      setResponseFormatSaving(true);
                      updateAgentResponseFormat(id, opt.value)
                        .then((r) => setResponseFormat(r.data?.response_format || opt.value))
                        .catch(() => setResponseFormat(prev))
                        .finally(() => setResponseFormatSaving(false));
                    }}
                    className={`text-left rounded-xl border-2 p-4 transition-colors disabled:opacity-50 ${
                      active ? 'border-teal-300 bg-teal-50' : 'border-gray-200 bg-gray-50 hover:bg-gray-100'
                    }`}
                  >
                    <div className={`font-semibold text-sm ${active ? 'text-teal-700' : 'text-gray-900'}`}>
                      {opt.label}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{opt.desc}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* ── Regular Tools ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold flex items-center">
                <Wrench className="w-5 h-5 mr-2 text-indigo-600" />
                Tools
                <span className="ml-2 px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-xs font-semibold">
                  {regularToolIds.filter(t => selectedTools.includes(t)).length}/{regularToolIds.length}
                </span>
              </h3>
              <button
                type="button"
                onClick={handleSaveTools}
                disabled={toolsSaving || !toolsDirty || (Boolean(capabilityViolation?.blocking) && !capabilityOverridden)}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
              >
                {toolsSaving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
                Save Tools
              </button>
            </div>
            {capabilityViolation && (
              <div className={`mb-4 rounded-lg border p-3 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'border-amber-200 bg-amber-50' : 'border-red-200 bg-red-50'}`}>
                <div className={`text-sm font-bold flex items-center gap-2 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-800' : 'text-red-800'}`}>
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  {capabilityViolation.title}
                  {capabilityOverridden && capabilityViolation.blocking && (
                    <span className="px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 text-[10px] font-semibold uppercase tracking-wide">
                      {t('agentDetails.overrideActive')}
                    </span>
                  )}
                </div>
                <div className={`text-xs mt-1.5 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-800' : 'text-red-700'}`}>
                  {capabilityViolation.explanation}
                </div>
                {/* Name the offending capabilities and exactly which tools granted
                    each, so the fix is obvious instead of a guessing game. */}
                <ul className="mt-2 space-y-1">
                  {capabilityViolation.capabilities.map((cap) => (
                    <li key={cap} className={`text-xs ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-900' : 'text-red-800'}`}>
                      <span className="font-semibold">{CAPABILITY_LABELS[cap]}</span>
                      {' — '}
                      {(capabilityViolation.sources[cap] || []).join(', ') || '?'}
                    </li>
                  ))}
                </ul>
                <div className={`text-xs mt-2 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-700' : 'text-red-600'}`}>
                  {!capabilityViolation.blocking
                    ? t('agentDetails.capabilityAllowed')
                    : capabilityOverridden
                      ? t('agentDetails.capabilityOverridden')
                      : t('agentDetails.capabilityBlocked')}
                </div>
              </div>
            )}
            {toolsMessage && (
              <div className={`text-xs mb-3 ${toolsMessage === t('agentDetails.toolsUpdated') ? 'text-green-600' : 'text-red-600'}`}>
                {toolsMessage}
              </div>
            )}
            {toolCategories.length ? (
              <div className="space-y-5">
                {toolCategories.map(({ category, ids }) => {
                  const enabledCount = ids.filter(t => selectedTools.includes(t)).length;
                  const allOn = enabledCount === ids.length;
                  const noneOn = enabledCount === 0;
                  return (
                    <div key={category} className="border border-gray-100 rounded-lg overflow-hidden">
                      {/* Category header with master switch */}
                      <div className="flex items-center justify-between gap-3 px-4 py-2.5 bg-gray-50 border-b border-gray-100">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-gray-800">{formatCategory(category)}</span>
                          <span className="text-[11px] text-gray-400">{enabledCount}/{ids.length} on</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setCategoryTools(ids, !allOn)}
                          role="switch"
                          aria-checked={allOn}
                          title={allOn ? t('agentDetails.disableAllInCategory') : t('agentDetails.enableAllInCategory')}
                          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
                            allOn ? 'bg-indigo-600' : noneOn ? 'bg-gray-300' : 'bg-indigo-300'
                          }`}
                        >
                          <span
                            className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                              allOn ? 'translate-x-5' : 'translate-x-1'
                            }`}
                          />
                        </button>
                      </div>
                      {/* Tools in category */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3">
                        {ids.map((tool) => {
                          const enabled = selectedTools.includes(tool);
                          const meta = toolsMeta[tool] || {};
                          return (
                            <div key={tool} className="p-3 border border-gray-100 rounded-lg bg-white flex items-center justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-sm font-semibold text-gray-800 truncate">{meta.label || tool}</div>
                                <div className="text-xs text-gray-500 mt-1 truncate">{meta.description || tool}</div>
                              </div>
                              <button
                                type="button"
                                onClick={() => toggleTool(tool)}
                                className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                                  enabled ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'
                                }`}
                              >
                                {enabled ? 'On' : 'Off'}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-gray-500 italic">{t('agentDetails.noToolsConfiguredForThis')}</p>
            )}
            <p className="text-xs text-gray-500 mt-3">
              Toggle tools on/off, then click <span className="font-semibold">{t('agentDetails.saveTools')}</span> to apply changes.
            </p>
          </div>

          {selectedTools.includes('run_agent_tool') && (
            <div className="bg-white p-6 shadow-md rounded-lg">
              <div className="flex items-center justify-between gap-3 mb-2">
                <div className="flex items-center gap-2">
                  <Share2 className="w-5 h-5 text-indigo-600" />
                  <h3 className="text-lg font-bold text-gray-900">{t('agentDetails.delegation')}</h3>
                </div>
                <button
                  type="button"
                  onClick={handleSaveDelegates}
                  disabled={delegatesSaving || !delegatesDirty.current}
                  className={`px-4 py-2 rounded-lg text-sm font-semibold ${
                    delegatesSaving || !delegatesDirty.current
                      ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'bg-indigo-600 text-white hover:bg-indigo-700'
                  }`}
                >
                  {delegatesSaving ? t('common.saving') : t('agentDetails.saveDelegation')}
                </button>
              </div>
              <p className="text-sm text-gray-600 mb-4">
                {t('agentDetails.delegationIntro')} <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">{t('agentDetails.runAgentTool')}</code>.
                {t('agentDetails.delegationSelect')} <span className="font-semibold">{t('agentDetails.allUnselected')}</span> {t('agentDetails.delegationNoRestriction')}
              </p>

              <div className="flex items-center justify-between mb-2">
                <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
                  delegates.length ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'
                }`}>
                  {delegates.length
                    ? t('agentDetails.restrictedToCount', { count: delegates.length })
                    : t('agentDetails.noRestriction')}
                </span>
                {delegates.length > 0 && (
                  <button
                    type="button"
                    onClick={() => { setDelegates([]); delegatesDirty.current = true; setDelegatesMessage(''); }}
                    className="text-xs font-semibold text-gray-500 hover:text-gray-700"
                  >
                    {t('agentDetails.clearRestriction')}
                  </button>
                )}
              </div>

              {allAgents.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {allAgents.map((a) => {
                    const aid = a.id || a;
                    const enabled = delegates.includes(aid);
                    return (
                      <div key={aid} className="p-3 border border-gray-100 rounded-lg bg-white flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-gray-800 truncate">{a.name || aid}</div>
                          <div className="text-xs text-gray-500 mt-1 truncate">{a.description || aid}</div>
                        </div>
                        <button
                          type="button"
                          onClick={() => toggleDelegate(aid)}
                          className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                            enabled ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'
                          }`}
                        >
                          {enabled ? 'Allowed' : 'Off'}
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">{t('agentDetails.noOtherAgentsAvailableIn')}</p>
              )}

              {delegatesMessage && (
                <p className="text-xs text-gray-600 mt-3">{delegatesMessage}</p>
              )}
            </div>
          )}

        </div>
      )}

      {activeTab === 'nodes' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" />
              {t('agentDetails.agentNodes')}
            </h3>
            <div className="flex items-center gap-2">
              {nodesOverWsCap && (
                <span className="text-xs text-orange-600 font-medium">{t('agentDetails.nodeLimitReached', { limit: wsSessionCap })}</span>
              )}
              <button
                type="button"
                disabled={nodesOverWsCap}
                onClick={() => { setStartWorkspace(selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : ''); setShowStartNodeModal(true); }}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Play className="w-3.5 h-3.5 mr-1" />
                {t('agentDetails.startNode')}
              </button>
            </div>
          </div>
          {nodes.length === 0 ? (
            <p className="text-sm text-gray-500 italic">{t('agentDetails.noNodesFoundForThis')}</p>
          ) : (
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.status')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.nodeId')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.label')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.workspace')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.started')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.uptime')}</th>
                    <th className="text-right px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.actions')}</th>
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
                              title={t('agentDetails.viewLogs')}
                              className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                            >
                              <FileText className="w-4 h-4" />
                            </button>
                            {isActive ? (
                              <button
                                type="button"
                                onClick={() => handleStopNode(nodeId)}
                                disabled={!!busy}
                                title={t('agentDetails.stopNode')}
                                className="p-1.5 rounded text-gray-400 hover:text-orange-600 hover:bg-orange-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'stopping' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                              </button>
                            ) : (
                              <button
                                type="button"
                                onClick={() => handleDeleteNode(nodeId)}
                                disabled={!!busy}
                                title={t('agentDetails.removeRecord')}
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
            {t('agentDetails.tasksAssignedToThisAgent')}
          </h3>
          {agentTasks.length === 0 ? (
            <p className="text-sm text-gray-500 italic">{t('agentDetails.noTasksAssignedToThis')}</p>
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
                        <div className="text-xs text-gray-500 mt-1">{t('agentDetails.workspace2')} <span className="">{task.workspace || '—'}</span></div>
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
          { name: '/help', description: t('chat.commands.help'), template: '/help' },
          { name: '/clear', description: t('chat.commands.clear'), template: '/clear' },
          { name: '/new', description: t('chat.commands.new'), template: '/new' },
          { name: '/config', description: t('chat.commands.config'), template: '/config' },
        ];
        return (
          <div className="space-y-6">
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-lg font-bold mb-1 flex items-center gap-2">
                <Terminal className="w-5 h-5 text-indigo-600" /> {t('agentDetails.slashCommands')}
              </h3>
              <p className="text-sm text-gray-500 mb-5">
                Type <span className=" bg-gray-100 px-1 rounded">/</span> {t('agentDetails.inTheChatToTrigger')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">↑↓</kbd> {t('agentDetails.toNavigate')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">{t('agentDetails.enter')}</kbd> {t('agentDetails.or')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">{t('agentDetails.tab')}</kbd> {t('agentDetails.toSelect')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">{t('agentDetails.esc')}</kbd> {t('agentDetails.toDismiss')}
              </p>

              {agentCmds.length > 0 && (
                <div className="mb-6">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">{t('agentDetails.agentCommands')}</h4>
                  <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden">
                    {agentCmds.map((cmd) => (
                      <div key={cmd.name} className="flex items-start gap-4 px-4 py-3 bg-white hover:bg-gray-50 transition-colors">
                        <span className=" text-sm font-semibold text-indigo-600 shrink-0 w-40">{cmd.name}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-gray-700">{cmd.description}</p>
                          <p className="text-xs text-gray-400 mt-0.5 truncate">{t('agentDetails.template')}: {cmd.template}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {agentCmds.length === 0 && (
                <div className="mb-6 flex items-center gap-3 p-4 bg-amber-50 border border-amber-100 rounded-xl text-sm text-amber-700">
                  <Hash className="w-4 h-4 shrink-0" />
                  {t('agentDetails.noAgentCommands')} <span className=" mx-1">"commands"</span> {t('agentDetails.arrayToThisAgentIn')} <span className=" ml-1">.agents_hub/agents.json</span>.
                </div>
              )}

              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">{t('agentDetails.globalCommands')}</h4>
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
        <>
        <div className={defChat.gridClass}>
          <div className={`space-y-6 ${defChat.mainClass}`}>
          {agent.system && <SystemAgentWarning scope="config" />}

          {[
            { key: 'instructions', label: 'instructions.md', desc: t('agentDetails.definitions.instructions'), icon: Terminal, required: true },
            { key: 'capabilities', label: 'capabilities.md', desc: t('agentDetails.definitions.capabilities'), icon: Zap, required: false },
            { key: 'usage',        label: 'usage.md',        desc: t('agentDetails.definitions.usage'), icon: BookOpen, required: false },
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
                      {required && <span className="ml-2 text-[10px] uppercase tracking-wide font-bold text-red-500">{t('agentDetails.required')}</span>}
                    </h3>
                    <p className="text-xs text-gray-500 mt-1">{desc}</p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      onClick={() => handleResetDefinitionField(key)}
                      disabled={!dirty || saving}
                      className="px-3 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {t('agentDetails.reset')}
                    </button>
                    <button
                      onClick={() => handleSaveDefinitionField(key)}
                      disabled={!dirty || saving || cannotDelete}
                      title={cannotDelete ? t('agentDetails.instructionsRequired') : undefined}
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
                  placeholder={required ? t('agentDetails.definitionRequired') : t('agentDetails.definitionOptional')}
                />
                {error && (
                  <p className="text-xs text-red-600 mt-2">{error}</p>
                )}
                {dirty && !error && (
                  <p className="text-xs text-amber-600 mt-2">{t('agentDetails.unsavedChanges')}</p>
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
                {t('agentDetails.whatTheAgentActuallyReceives')}
              </p>
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap max-h-96">
                {agentDefinition.system_prompt}
              </pre>
            </div>
          )}
          </div>

          {defChat.open && definitionChat && (
            <ChatColumn>
              <EntityChat {...definitionChat} {...FILL_COLUMN} />
            </ChatColumn>
          )}
        </div>
        </>
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
                {t('agentDetails.overrideTheGlobalModelSettings')}
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
            <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('agentDetails.provider')}</h4>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {[
                { value: 'inherit',   label: t('agentDetails.inherit'), sub: t('agentDetails.globalSub') },
                { value: 'openai',    label: 'OpenAI',     sub: t('agentDetails.cloudSub') },
                { value: 'anthropic', label: 'Anthropic',  sub: 'Claude' },
                { value: 'google',    label: 'Google',     sub: 'Gemini' },
                { value: 'ollama',    label: 'Ollama',     sub: t('agentDetails.localSub') },
                { value: 'lmstudio', label: 'LM Studio',  sub: t('agentDetails.localSub') },
                ...customBackends.map(b => ({ value: b.id, label: b.label || b.id, sub: t('agentDetails.customSub') })),
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
            <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('agentDetails.model2')}</h4>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.modelName')}</label>
              <p className="text-xs text-gray-500 mb-2">
                {modelForm.provider === 'inherit' && t('agentDetails.inheritingModel')}
                {modelForm.provider === 'openai' && 'e.g. gpt-4o, gpt-4o-mini, gpt-4-turbo'}
                {modelForm.provider === 'anthropic' && 'e.g. claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5-20251001'}
                {modelForm.provider === 'google' && 'e.g. gemini-2.0-flash, gemini-1.5-pro'}
                {modelForm.provider === 'ollama' && t('agentDetails.ollamaModelHint')}
                {modelForm.provider === 'lmstudio' && t('agentDetails.lmstudioModelHint')}
                {customBackends.some(b => b.id === modelForm.provider) && t('agentDetails.customBackendModelHint')}
              </p>
              {(modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && localModels.length > 0 ? (
                <select
                  value={modelForm.model}
                  onChange={e => setModelForm(f => ({ ...f, model: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  <option value="">{t('agentDetails.selectAModel')}</option>
                  {localModels.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input
                  type="text"
                  value={modelForm.model}
                  onChange={e => setModelForm(f => ({ ...f, model: e.target.value }))}
                  placeholder={modelForm.provider === 'inherit' ? t('agentDetails.inheritingPlaceholder') : t('agentDetails.enterModelName')}
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
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.baseUrlOverride')}</label>
                <p className="text-xs text-gray-500 mb-2">
                  {modelForm.provider === 'openai' && t('agentDetails.openaiBaseUrlHint')}
                  {modelForm.provider === 'ollama' && t('agentDetails.ollamaBaseUrlHint')}
                  {modelForm.provider === 'lmstudio' && t('agentDetails.lmstudioBaseUrlHint')}
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
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.temperatureOverride')}</label>
                <input
                  type="number"
                  value={modelForm.temperature}
                  onChange={e => setModelForm(f => ({ ...f, temperature: e.target.value }))}
                  placeholder={t('agentDetails.inheritGlobal')}
                  min="0" max="2" step="0.05"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <p className="text-xs text-gray-400 mt-1">{t('agentDetails.clearToInheritFromGlobal')}</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.maxTokensOverride')}</label>
                <input
                  type="number"
                  value={modelForm.max_tokens}
                  onChange={e => setModelForm(f => ({ ...f, max_tokens: e.target.value }))}
                  placeholder={t('agentDetails.inheritGlobal')}
                  min="256" step="256"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <p className="text-xs text-gray-400 mt-1">{t('agentDetails.clearToInheritFromGlobal')}</p>
              </div>
            </div>
          </div>

          {/* API Key override */}
          {(modelForm.provider !== 'inherit' && modelForm.provider !== 'ollama' && modelForm.provider !== 'lmstudio') && (
            <div className="bg-white p-6 shadow-md rounded-lg space-y-4">
              <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('agentDetails.apiKeyOverride')}</h4>
              <p className="text-sm text-gray-500">
                Optionally store a per-agent API key. This overrides the key from global settings for this agent only.
                {modelHasApiKey && <span className="ml-1 text-green-600 font-medium">{t('agentDetails.aKeyIsCurrentlyStored')}</span>}
              </p>
              <div className="relative">
                <input
                  type={modelShowKey ? 'text' : 'password'}
                  value={modelForm.api_key}
                  onChange={e => setModelForm(f => ({ ...f, api_key: e.target.value }))}
                  placeholder={modelHasApiKey ? t('agentDetails.keyStored') : t('agentDetails.enterApiKey')}
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
                      .then(r => { setModelHasApiKey(!!r.data.has_api_key); setModelMessage(t('agentDetails.apiKeyRemoved')); setTimeout(() => setModelMessage(''), 3000); })
                      .catch(() => setModelMessage(t('agentDetails.errors.removeKey')));
                  }}
                  className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1"
                >
                  <Trash2 className="w-3.5 h-3.5" /> {t('agentDetails.removeStoredKey')}
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
                <Layers className="w-4 h-4 text-indigo-500" /> {t('agentDetails.dockerImages')}
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
                { label: t('agentDetails.baseImage'), tag: 'agents-hub/base:latest' },
                { label: t('agentDetails.agentImage', { id }), tag: `agents-hub/${id}:latest` },
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
                      {exists ? t('agentDetails.built') : t('agentDetails.notBuilt')}
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
                {buildingBase ? t('agentDetails.buildingBase') : t('agentDetails.buildBaseImage')}
              </button>
              <button
                onClick={handleBuildAgent}
                disabled={buildingBase || buildingAgent}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {buildingAgent ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {buildingAgent ? t('agentDetails.building') : t('agentDetails.buildAgentImage')}
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
              <FileCode className="w-4 h-4 text-indigo-500" /> {t('agentDetails.dockerfile')}
            </h3>
            <p className="text-xs text-gray-500 mb-3">
              {t('agentDetails.dockerfileHintBefore')} <code className="text-indigo-600">agents-hub/base:latest</code> {t('agentDetails.dockerfileHintAfter')}{' '}
              <code className="text-indigo-600">agents/state/dockerfiles/{id}.Dockerfile</code>.
            </p>
            {dockerfileLoading ? (
              <div className="flex justify-center py-8"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
            ) : dockerfileContent ? (
              <pre className="text-xs bg-gray-900 text-green-300 rounded-lg p-4 overflow-auto max-h-72 whitespace-pre-wrap">{dockerfileContent}</pre>
            ) : (
              <p className="text-sm text-gray-400 italic">{t('agentDetails.dockerfilePreviewUnavailable')}</p>
            )}
          </div>

          {/* Running containers */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <Server className="w-4 h-4 text-indigo-500" /> {t('agentDetails.containers')}
              <span className="text-xs text-gray-400 font-normal">({t('agentDetails.forThisAgent')})</span>
            </h3>
            {dockerLoading ? (
              <div className="flex justify-center py-6"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
            ) : dockerContainers.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <Server className="w-10 h-10 mx-auto mb-3 opacity-20" />
                <p className="text-sm">{t('agentDetails.noContainersFoundForThis')}</p>
                <p className="text-xs mt-1">{t('agentDetails.startANodeInDocker')}</p>
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
                          {t('agentDetails.logs')}
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
              <BookOpen className="w-5 h-5 text-purple-500" /> {t('agentDetails.skillsConfiguration')}
            </h3>
            <p className="text-sm text-gray-500 mb-5">
              When enabled, relevant skills are automatically matched to the task description and injected before the agent starts.
              Agents can also discover and save new skills using the <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">{t('agentDetails.saveSkill')}</code> tool.
            </p>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-700">{t('agentDetails.proceduralSkills')}</p>
                <p className="text-xs text-gray-400 mt-0.5">{t('agentDetails.scopeThisAgentWorkspaceSpecific')}</p>
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
              <p className="text-sm text-gray-500">{t('agentDetails.selectAWorkspaceToView')}</p>
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
                  {showAddSkill ? t('common.cancel') : t('agentDetails.addSkill')}
                </button>
              </div>

              {/* Add skill form */}
              {showAddSkill && (
                <div className="px-5 py-4 bg-purple-50 border-b border-purple-100 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.name')}</label>
                      <input
                        type="text"
                        value={skillForm.name}
                        onChange={e => setSkillForm(f => ({ ...f, name: e.target.value }))}
                        placeholder={t('agentDetails.eGFixPythonImport')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.tags')} <span className="text-gray-400 font-normal">({t('agentDetails.commaSeparated')})</span></label>
                      <input
                        type="text"
                        value={skillForm.tags}
                        onChange={e => setSkillForm(f => ({ ...f, tags: e.target.value }))}
                        placeholder={t('agentDetails.eGDebuggingPythonApi')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.whenToUseThisSkill')}</label>
                    <input
                      type="text"
                      value={skillForm.description}
                      onChange={e => setSkillForm(f => ({ ...f, description: e.target.value }))}
                      placeholder={t('agentDetails.eGWhenAPython')}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.steps')} <span className="text-gray-400 font-normal">({t('agentDetails.onePerLine')})</span></label>
                    <textarea
                      value={skillForm.steps}
                      onChange={e => setSkillForm(f => ({ ...f, steps: e.target.value }))}
                      rows={5}
                      placeholder={t('agentDetails.stepsPlaceholder')}
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
                      {skillSaving ? t('common.saving') : t('agentDetails.saveSkillButton')}
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
                  <p className="text-sm text-gray-500">{t('agentDetails.noSkillsYetForThis')} <strong>{selectedWorkspace}</strong>.</p>
                  <p className="text-xs text-gray-400 mt-1">{t('agentDetails.addOneAboveOrThe')} <code className="bg-gray-100 px-1 rounded">{t('agentDetails.saveSkill')}</code> {t('agentDetails.isCalled')}</p>
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
                              <span className="text-[10px] text-gray-400">{t('agentDetails.usedCount', { count: skill.use_count })}</span>
                            )}
                          </div>
                          <p className="text-xs text-gray-500 italic mb-2">{skill.description}</p>
                          {skill.tags.length > 0 && (
                            <div className="flex items-center gap-1 flex-wrap mb-2">
                              <Tag className="w-3 h-3 text-gray-300" />
                              {skill.tags.map(tag => (
                                <span key={tag} className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{tag}</span>
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
                          title={t('agentDetails.deleteSkill')}
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
                {t('agentDetails.startNode')}
              </h2>
              <button onClick={() => setShowStartNodeModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  {t('agentDetails.workspace')}
                </label>
                <select
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  value={startWorkspace}
                  onChange={(e) => setStartWorkspace(e.target.value)}
                >
                  <option value="">{t('agentDetails.none2')}</option>
                  {workspaces.map((ws) => (
                    <option key={ws.name} value={ws.name}>{ws.label || ws.id || ws.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  {t('agentDetails.label')} <span className="text-gray-400 font-normal normal-case">({t('common.optional')})</span>
                </label>
                <input
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder={t('agentDetails.eGDevWorker')}
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
                  {t('agentDetails.cancel')}
                </button>
                <button
                  type="button"
                  disabled={startingNode}
                  onClick={handleStartNode}
                  className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  {startingNode ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  {startingNode ? t('agentDetails.starting') : t('agentDetails.startNode')}
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
    </PageContainer>
  );
};

export default AgentDetails;
