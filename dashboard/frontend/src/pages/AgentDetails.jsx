import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import InstanceList from '../components/InstanceList';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { Activity, Radio, History, Server, Wrench, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2, FileCode, Play, Square, Loader, X, FileText, BrainCircuit, Eye, EyeOff, Link2, Layers, Hash, Copy, FileSearch, Zap, BarChart2, Wifi, MessageSquare, BookOpen, Plus, ChevronDown, ChevronUp, Tag, Globe, Lock, Share2, HelpCircle, Repeat, AlertTriangle, Users } from 'lucide-react';
import { checkCombination, CAPABILITY_LABELS } from '../lib/capabilities';
import ImportedAgentPanel from '../components/ImportedAgentPanel';
import { getAgent, getAgents, getAgentDelegates, updateAgentDelegates, getAgentEpisodicConfig, getAgentHistory, getAgentLogs, updateAgentMemory, eraseAgentMemory, updateAgentTools, updateAgentDescription, getAgentReasoning, getCustomBackends, getNodes, getAgentDefinition, updateAgentDefinition, getTasks, getTools, getWorkspaces, getSharedMemories, getSharedMemory, getAgentWorkspaceCapacities, setWorkspaceAgentCapacity, removeWorkspaceAgentCapacity, setDefaultChatAgent, clearDefaultChatAgent, updateAgentSharing } from '../api';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast } from '../components/toast';
import { AgentPageContext } from '../components/agent/context';
import useDefinitionChatDescriptor from '../components/agent/useDefinitionChatDescriptor';
import { PLANNING_TOOLS, defaultReasoningSettings } from '../components/agent/constants';
import AgentModals from '../components/agent/AgentModals';
import useAgentDocker from '../components/agent/useAgentDocker';
import useAgentModel from '../components/agent/useAgentModel';
import useAgentNodes from '../components/agent/useAgentNodes';
import useAgentSkills from '../components/agent/useAgentSkills';
import useAgentVersions from '../components/agent/useAgentVersions';
import OverviewTab from '../components/agent/OverviewTab';
import InstancesTab from '../components/agent/InstancesTab';
import HistoryTab from '../components/agent/HistoryTab';
import LogsTab from '../components/agent/LogsTab';
import MemoryTab from '../components/agent/MemoryTab';
import ToolsTab from '../components/agent/ToolsTab';
import NodesTab from '../components/agent/NodesTab';
import TasksTab from '../components/agent/TasksTab';
import CommandsTab from '../components/agent/CommandsTab';
import ConfigTab from '../components/agent/ConfigTab';
import ModelTab from '../components/agent/ModelTab';
import DockerTab from '../components/agent/DockerTab';
import SkillsTab from '../components/agent/SkillsTab';
import LiveQualityCard from '../components/agent/LiveQualityCard';
import ExperimentCard from '../components/agent/ExperimentCard';

// The thirteen tabs, the three overlays and the pieces they share live in
// `components/agent/`; this file is what loads the agent and what the tabs
// are given (see `agent/context.js`). Nothing below renders a tab itself.
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
  const markMemoryDraftDirty = useCallback(() => { memoryDraftDirty.current = true; }, []);
  const [isUpdatingMemory, setIsUpdatingMemory] = useState(false);
  const [sharedMemories, setSharedMemories] = useState([]);
  const [connectedPool, setConnectedPool] = useState(null);
  const [loadingPool, setLoadingPool] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');

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
  // The tabs do not reach into the page's refs; they say what happened and the
  // page records it. `useCallback` so a tab's props stay stable.
  const markDelegatesDirty = useCallback(() => { delegatesDirty.current = true; }, []);
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

  // Three tabs own enough state to be their own concern; each loads on the tab
  // opening and hands the page back what its tab renders.
  const docker = useAgentDocker({ id, activeTab, t, toast });
  const nodeActions = useAgentNodes({ id, onChanged: fetchData, t });
  const model = useAgentModel({ id, t });
  const skills = useAgentSkills({ id, agent, activeTab, selectedWorkspace, t, toast });
  const versions = useAgentVersions({ id, activeTab, onRolledBack: fetchData, t });
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

  // Published once for the tabs and the overlays; see `agent/context.js`.
  const page = {
    ...docker, ...model, ...nodeActions, ...skills, ...versions,
    PLAN_FORMATS, THINKING_LEVELS, THINK_MODES, activeTask, addExtraPool, agent,
    agentDefinition, agentLogsData, agentTasks, allAgents, availableTools,
    capabilityOverridden, capabilityViolation, clarifyGate, clarifyGateSaving, connectedPool,
    customBackends, defChat, defDraft, defError, defSaving, defaultChatMessage,
    defaultChatSaving, definitionChat, delegates, delegatesDirty, markDelegatesDirty,
    markMemoryDraftDirty, delegatesMessage, delegatesSaving, descDraft, descSaving,
    episodicEffective, episodicMode, episodicSaving, fetchData, formatCategory,
    handleDefinitionDraftChange, handleEraseMemory, handleResetDefinitionField,
    handleSaveDefinitionField, handleSaveDelegates, handleSaveDescription, handleSaveTools,
    handleSaveWsCapacity, handleToggleDefaultChat, handleToggleShared, handleUpdateMemory,
    history, id, isDefaultChat, isUpdatingMemory, liveUpdates, loadingPool, memoryData,
    memoryDraftDirty, memoryPools, memoryType, nodes, nodesOverWsCap, reasoningSettings,
    regularToolIds, removePool, responseFormat, responseFormatSaving, selectedTools,
    selectedWorkspace, selfDelegation, selfDelegationSaving, setActiveTab, setCategoryTools,
    setClarifyGate, setClarifyGateSaving, setConnectedPool, setDelegates, setDelegatesMessage,
    setDescDraft, setEpisodicEffective, setEpisodicMode, setEpisodicSaving, setMemoryData,
    setMemoryType, setPrimaryPool, setReasoningSettings, setResponseFormat,
    setResponseFormatSaving, setSelectedTools, setSelfDelegation, setSelfDelegationSaving,
    setToolsSaving, setWsCapacityEdits, shared, sharedMemories, sharingMessage, sharingSaving,
    skills, t, tasks, toast, toggleDelegate, toggleTool, toolCategories, toolsDirty,
    toolsMessage, toolsMeta, toolsSaving, workspaceFilter, workspaces, wsCapacities,
    wsCapacityEdits, wsCapacitySaving, wsSessionCap
  };

  return (
    <AgentPageContext.Provider value={page}>
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

      {/* Live quality (online evals) sits under the overview; the experiment
          card sits under the version history it picks its arms from. */}
      {activeTab === 'overview' && (
        <>
          <OverviewTab />
          <LiveQualityCard agentId={id} />
        </>
      )}
      {activeTab === 'instances' && <InstancesTab />}
      {activeTab === 'history' && <HistoryTab />}
      {activeTab === 'logs' && <LogsTab />}
      {activeTab === 'memory' && <MemoryTab />}
      {activeTab === 'tools' && <ToolsTab />}
      {activeTab === 'nodes' && <NodesTab />}
      {activeTab === 'tasks' && <TasksTab />}
      {activeTab === 'commands' && <CommandsTab />}
      {activeTab === 'config' && (
        <>
          <ConfigTab />
          <ExperimentCard agentId={id} />
        </>
      )}
      {activeTab === 'model' && <ModelTab />}
      {activeTab === 'docker' && <DockerTab />}
      {activeTab === 'skills' && <SkillsTab />}

      <AgentModals />
    </PageContainer>
    </AgentPageContext.Provider>
  );
};

export default AgentDetails;
