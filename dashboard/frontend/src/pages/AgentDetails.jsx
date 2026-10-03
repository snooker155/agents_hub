import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import InstanceList from '../components/InstanceList';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { Activity, Radio, History, Wrench, Terminal, ExternalLink, CheckCircle, AlertCircle, Clock, Database, Save, Trash2, FileCode, Play, Square, Loader, X, FileText, BrainCircuit, Eye, EyeOff, Link2, Layers, Hash, Copy, FileSearch, Zap, BarChart2, Wifi, MessageSquare, BookOpen, Plus, ChevronDown, ChevronUp, Tag, Globe, Lock, Share2, HelpCircle, Repeat, AlertTriangle, Users, Rocket, SlidersHorizontal, ShieldCheck, FlaskConical, GitBranch } from 'lucide-react';
import { checkCombination, CAPABILITY_LABELS } from '../lib/capabilities';
import ImportedAgentPanel from '../components/ImportedAgentPanel';
import { getAgent, getAgents, getAgentDelegates, updateAgentDelegates, getAgentCapabilityOverride, updateAgentCapabilityOverride, getAgentAutoTools, getAgentEpisodicConfig, getSessions, getMessages, updateAgentMemory, eraseAgentMemory, updateAgentTools, updateAgentDescription, getAgentReasoning, getCustomBackends, getServices, getAgentDefinition, updateAgentDefinition, getTasks, getTools, getWorkspaces, getSharedMemories, getSharedMemory, getAgentWorkspaceCapacities, setWorkspaceAgentCapacity, removeWorkspaceAgentCapacity, setDefaultChatAgent, clearDefaultChatAgent, updateAgentSharing, getAgentPersonalMemory } from '../api';
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
import AgentConflictDialog from '../components/agent/AgentConflictDialog';
import useAgentDocker from '../components/agent/useAgentDocker';
import useAgentModel from '../components/agent/useAgentModel';
import useAgentSkills from '../components/agent/useAgentSkills';
import useAgentVersions from '../components/agent/useAgentVersions';
import OverviewTab from '../components/agent/OverviewTab';
import InstancesTab from '../components/agent/InstancesTab';
import SessionsTab from '../components/agent/SessionsTab';
import RunsTab from '../components/agent/RunsTab';
import MemoryTab from '../components/agent/MemoryTab';
import ToolsTab from '../components/agent/ToolsTab';
import BehaviorTab from '../components/agent/BehaviorTab';
import TasksTab from '../components/agent/TasksTab';
import StartInstanceModal from '../components/instances/StartInstanceModal';
import DeployServiceModal from '../components/services/DeployServiceModal';
import CommandsTab from '../components/agent/CommandsTab';
import ConfigTab from '../components/agent/ConfigTab';
import VersionsTab from '../components/agent/VersionsTab';
import ModelTab from '../components/agent/ModelTab';
import DockerTab from '../components/agent/DockerTab';
import SkillsTab from '../components/agent/SkillsTab';
import HandoffsCard from '../components/agent/HandoffsCard';
import LoopSettingsCard from '../components/agent/LoopSettingsCard';
import ProactiveCard from '../components/agent/ProactiveCard';
import AgentGuardrailsCard from '../components/agent/AgentGuardrailsCard';
import LiveQualityCard from '../components/agent/LiveQualityCard';
import ExperimentCard from '../components/agent/ExperimentCard';
import PageLoader from '../components/PageLoader';
import InheritanceHeaderLine from '../components/agent/inheritance/InheritanceHeaderLine';
import InheritanceTab from '../components/agent/inheritance/InheritanceTab';

// The tabs, the container-logs overlay and the pieces they share live in
// `components/agent/`; this file is what loads the agent and what the tabs
// are given (see `agent/context.js`). Nothing below renders a tab itself.
// How many of the agent's newest sessions and runs its tabs show; the full
// lists are the Sessions and Messages pages.
const AGENT_PAGE_SIZE = 100;

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
  // The newest page of this agent's sessions and runs ({items, total}).
  const [sessions, setSessions] = useState({ items: [], total: 0 });
  const [runs, setRuns] = useState({ items: [], total: 0 });
  const [tasks, setTasks] = useState([]);
  // Live resident instances of this agent (started with Run), from the same
  // grouped count query the Agents grid uses (GET /instances/summary).
  // The workspace's services: the agent's copies and their limits live there.
  const [services, setServices] = useState([]);
  const [showStartInstance, setShowStartInstance] = useState(false);
  const [showDeploy, setShowDeploy] = useState(false);
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
  // Pool ids in memoryPools whose binding is read only (agents.registry
  // memory_pool_read_only_ids): recall still works, remember/forget/
  // record_episode/link/the block tools refuse on that one pool.
  const [memoryReadOnly, setMemoryReadOnly] = useState(() => new Set());
  const memoryDraftDirty = useRef(false);
  const markMemoryDraftDirty = useCallback(() => { memoryDraftDirty.current = true; }, []);
  const [isUpdatingMemory, setIsUpdatingMemory] = useState(false);
  const [sharedMemories, setSharedMemories] = useState([]);
  const [connectedPool, setConnectedPool] = useState(null);
  const [loadingPool, setLoadingPool] = useState(false);
  // This agent's personal memory in the selected workspace (GET /agents/{id}/personal-memory).
  const [personalMemory, setPersonalMemory] = useState(null);
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
  // The capability violation the last delegation save was refused for (409),
  // or the warning the saved allowlist still forms. Delegation paths cannot be
  // evaluated client-side, so this is the only source for the delegation card.
  const [delegatesViolation, setDelegatesViolation] = useState(null);
  // Same for the tools save: what the server still sees after a save that
  // went through on the override, in warn mode or by grandfathering.
  const [toolsServerWarning, setToolsServerWarning] = useState(null);

  // Capability guard escape hatch for this agent, and what the guard would do
  // with it: its global mode and whether the override counts at build time
  // (not in block mode with the container requirement on and local execution).
  const [capabilityOverride, setCapabilityOverride] = useState(false);
  const [capabilityOverrideSaving, setCapabilityOverrideSaving] = useState(false);
  const [capabilityGuardInfo, setCapabilityGuardInfo] = useState({ guard_mode: 'block', honoured_at_build: true, override_requires_container: true });
  // Tools the factory adds at build time on top of the record (handoff,
  // reasoning, skills, ask_user, memory pool), shown read-only on the Tools tab.
  const [autoTools, setAutoTools] = useState([]);

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
      const page = { agent_id: id, limit: AGENT_PAGE_SIZE, ...(workspaceFilter ? { workspace: workspaceFilter } : {}) };
      const empty = { data: { items: [], total: 0 } };
      const [agentResp, sessionsResp, runsResp, tasksResp, workspacesResp, wsCapResp, servicesResp, overrideResp, autoToolsResp, personalResp] = await Promise.all([
        getAgent(id, defaultChatWorkspace),
        getSessions(page).catch(() => empty),
        getMessages(page).catch(() => empty),
        getTasks(workspaceFilter),
        getWorkspaces(),
        getAgentWorkspaceCapacities(id).catch(() => ({ data: {} })),
        getServices({ workspace: workspaceFilter || undefined }).catch(() => ({ data: { items: [] } })),
        getAgentCapabilityOverride(id).catch(() => ({ data: null })),
        getAgentAutoTools(id, workspaceFilter || undefined).catch(() => ({ data: { tools: [] } })),
        getAgentPersonalMemory(id, selectedWorkspace || undefined).catch(() => ({ data: null })),
      ]);
      setAgent(agentResp.data);
      setPersonalMemory(personalResp.data || null);
      setAutoTools(Array.isArray(autoToolsResp.data?.tools) ? autoToolsResp.data.tools : []);
      if (overrideResp.data) {
        setCapabilityOverride(!!overrideResp.data.capability_override);
        setCapabilityGuardInfo(overrideResp.data);
        // What the saved record forms today, before any edit on this page.
        setDelegatesViolation(overrideResp.data.capability_warning || null);
      } else {
        setCapabilityOverride(!!agentResp.data?.capability_override);
      }
      setIsDefaultChat(!!agentResp.data?.is_default_chat_agent);
      setResponseFormat(agentResp.data?.response_format || 'none');
      setClarifyGate(!!agentResp.data?.clarify_gate);
      setSelfDelegation(!!agentResp.data?.allow_self_delegation);
      setSessions({ items: sessionsResp.data?.items || [], total: sessionsResp.data?.total || 0 });
      setRuns({ items: runsResp.data?.items || [], total: runsResp.data?.total || 0 });
      setServices(servicesResp.data?.items || []);
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
          const raw = Array.isArray(md) ? md : (md ? [md] : []);
          const ids = raw.map(e => (e && typeof e === 'object') ? String(e.id || '') : String(e)).filter(Boolean);
          const ro = new Set(raw.filter(e => e && typeof e === 'object' && e.read_only).map(e => String(e.id)));
          setMemoryPools(ids);
          setMemoryReadOnly(ro);
          setMemoryData('');
        } else {
          setMemoryPools([]);
          setMemoryReadOnly(new Set());
          setMemoryData(typeof md === 'string' ? md : JSON.stringify(md || '', null, 2));
        }
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
        // Primary pool first; a pool marked read only is sent as
        // {id, read_only: true} instead of a plain id (routes/agents.py
        // update_agent_memory), everything else as a plain id string.
        const entries = memoryPools.map(pid => (memoryReadOnly.has(pid) ? { id: pid, read_only: true } : pid));
        data = (entries.length === 1 && typeof entries[0] !== 'object') ? entries[0] : entries;
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
    setMemoryReadOnly(prev => {
      if (!prev.has(pid)) return prev;
      const next = new Set(prev);
      next.delete(pid);
      return next;
    });
  };

  const toggleReadOnly = (pid) => {
    memoryDraftDirty.current = true;
    setMemoryReadOnly(prev => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid); else next.add(pid);
      return next;
    });
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
      const { data } = await updateAgentTools(id, { tools: selectedTools });
      setToolsServerWarning(data?.capability_warning || null);
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

  const handleToggleCapabilityOverride = async (next) => {
    const prev = capabilityOverride;
    setCapabilityOverride(next);
    setCapabilityOverrideSaving(true);
    try {
      const { data } = await updateAgentCapabilityOverride(id, next);
      setCapabilityOverride(!!data?.capability_override);
      setCapabilityGuardInfo(data);
      setDelegatesViolation(data?.capability_warning || null);
      setAgent((a) => (a ? { ...a, capability_override: !!data?.capability_override } : a));
    } catch (error) {
      setCapabilityOverride(prev);
      const detail = error.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : detail?.message || t('agentDetails.errors.capabilityOverride'));
    } finally {
      setCapabilityOverrideSaving(false);
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
      // The allowlist was saved, but it may still close a blocked combination
      // through a delegate (override, warn mode or grandfathered): keep that
      // visible as a warning rather than pretending the exposure is gone.
      setDelegatesViolation(data?.capability_warning || null);
      setDelegatesMessage(
        (data?.delegates || []).length
          ? t('agentDetails.delegationRestricted')
          : t('agentDetails.delegationUnrestricted')
      );
    } catch (error) {
      // A 409 carries the structured violation (which delegate brought which
      // capability); anything else is a plain string or nothing at all.
      const detail = error.response?.data?.detail;
      if (detail && typeof detail === 'object' && detail.error === 'capability_violation') {
        setDelegatesViolation(detail);
        setDelegatesMessage(t('agentDetails.delegationRefused'));
      } else {
        setDelegatesMessage(typeof detail === 'string' && detail ? detail : t('agentDetails.errors.updateDelegation'));
      }
      toast.error(t('agentDetails.errors.updateDelegation'));
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


  if (loading) return <PageLoader size="lg" label={t('agentDetails.loadingAgentDetails')} />;
  if (!agent) return <div className="text-center py-10">{t('agentDetails.agentNotFound')}</div>;

  const activeRun = runs.items.find(r => r.status === 'running');
  const agentTools = Array.isArray(agent?.tools) ? agent.tools : [];
  const mergedTools = [...new Set([...agentTools])];
  const visibleToolIds = [...new Set([...availableTools, ...mergedTools, ...selectedTools])];
  const toolsDirty = toolsDraftDirty.current || ([...selectedTools].sort().join('|') !== [...mergedTools].sort().join('|'));
  // Blocked capability combination formed by the current selection, evaluated
  // client-side so it appears while toggling. The server refuses the save
  // regardless (409) unless the agent carries capability_override.
  const capabilityViolation = checkCombination(selectedTools, toolsMeta);
  const capabilityOverridden = capabilityOverride;
  // Whether a blocked combination is saved anyway: the per-agent override or
  // a global guard mode other than block (Settings, agent execution).
  const capabilitySoftened = capabilityOverride || (capabilityGuardInfo.guard_mode || 'block') !== 'block';

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
  // Where the agent's copies live: its own services, else the workspace's
  // runner (created by the first Run). Copies and their limit come from
  // there; the workspace's number for the agent is how many services of it
  // the workspace allows.
  const ownServices = services.filter((svc) => svc.kind === 'agent' && svc.agent_id === id);
  const runnerService = services.find((svc) => svc.kind === 'runner' && svc.is_default)
    || services.find((svc) => svc.kind === 'runner') || null;
  const servingServices = ownServices.length ? ownServices : (runnerService ? [runnerService] : []);
  const liveInstanceCount = servingServices.reduce((n, svc) => n + (svc.replicas?.live || 0), 0);
  const instanceCap = servingServices.reduce((n, svc) => n + (svc.replicas_max || 0), 0);
  const instancesAtCap = servingServices.length > 0 && liveInstanceCount >= instanceCap;
  const servingPaused = servingServices.length > 0 && servingServices.every((svc) => svc.status !== 'active');
  const agentTasks = (tasks || []).filter((t) => t.assigned_agent_type === id);
  const runningTasks = agentTasks.filter((t) => t.agent_state === 'running');
  const agentSingleCapacity = agent?.capacity || 1;
  const isDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';
  // How many services of this agent the workspace allows; none in the default workspace.
  const wsServiceLimit = isDefaultWorkspace ? Infinity : (wsCapacities[selectedWorkspace] ?? 1);
  const servicesAtLimit = wsServiceLimit !== Infinity && ownServices.length >= wsServiceLimit;
  // sessions = live copies × slots per copy
  const maxSessions = liveInstanceCount * agentSingleCapacity;
  const atCapacity = maxSessions > 0 && runningTasks.length >= maxSessions;
  const noLiveInstance = liveInstanceCount === 0;
  const sessionLoadFactor = maxSessions > 0
    ? Math.min(100, Math.round((runningTasks.length / maxSessions) * 100))
    : 0;

  // Published once for the tabs and the overlays; see `agent/context.js`.
  const page = {
    ...docker, ...model, ...skills, ...versions,
    PLAN_FORMATS, THINKING_LEVELS, THINK_MODES, activeRun, addExtraPool, agent,
    agentDefinition, agentTasks, allAgents, availableTools,
    capabilityOverridden, capabilityViolation, capabilityOverride, capabilityOverrideSaving,
    capabilityGuardInfo, capabilitySoftened, handleToggleCapabilityOverride,
    delegatesViolation, toolsServerWarning, autoTools, clarifyGate, clarifyGateSaving, connectedPool,
    customBackends, defChat, defDraft, defError, defSaving, defaultChatMessage,
    defaultChatSaving, definitionChat, delegates, delegatesDirty, markDelegatesDirty,
    markMemoryDraftDirty, delegatesMessage, delegatesSaving, descDraft, descSaving,
    episodicEffective, episodicMode, episodicSaving, fetchData, formatCategory,
    handleDefinitionDraftChange, handleEraseMemory, handleResetDefinitionField,
    handleSaveDefinitionField, handleSaveDelegates, handleSaveDescription, handleSaveTools,
    handleSaveWsCapacity, handleToggleDefaultChat, handleToggleShared, handleUpdateMemory,
    sessions, runs, id, isDefaultChat, isUpdatingMemory, liveUpdates, loadingPool, memoryData,
    personalMemory, setPersonalMemory,
    memoryDraftDirty, memoryPools, memoryReadOnly, toggleReadOnly, memoryType, servicesAtLimit, ownServices, reasoningSettings,
    regularToolIds, removePool, responseFormat, responseFormatSaving, selectedTools,
    selectedWorkspace, selfDelegation, selfDelegationSaving, setActiveTab, setCategoryTools,
    setClarifyGate, setClarifyGateSaving, setConnectedPool, setDelegates, setDelegatesMessage,
    setDescDraft, setEpisodicEffective, setEpisodicMode, setEpisodicSaving, setMemoryData,
    setMemoryType, setPrimaryPool, setReasoningSettings, setResponseFormat,
    setResponseFormatSaving, setSelectedTools, setSelfDelegation, setSelfDelegationSaving,
    setToolsSaving, setWsCapacityEdits, shared, sharedMemories, sharingMessage, sharingSaving,
    skills, t, tasks, toast, toggleDelegate, toggleTool, toolCategories, toolsDirty,
    toolsMessage, toolsMeta, toolsSaving, workspaceFilter, workspaces, wsCapacities,
    wsCapacityEdits, wsCapacitySaving, wsServiceLimit,
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
        actions={(
          <div className="flex items-center gap-2">
            {/* Deploy: keep the agent running as replicas of a service, which
                is also where its chat turns go (docs/services.md). Run: one
                copy of your own to talk to. */}
            <button
              type="button"
              onClick={() => setShowDeploy(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-indigo-700 bg-white border border-indigo-200 rounded-lg hover:bg-indigo-50"
            >
              <Rocket className="w-4 h-4" />
              {t('agentDetails.deploy')}
            </button>
            <button
              type="button"
              onClick={() => setShowStartInstance(true)}
              disabled={servingPaused}
              title={servingPaused ? t('agentDetails.servicePausedHint') : undefined}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Play className="w-4 h-4" />
              {t('agentDetails.run')}
            </button>
          </div>
        )}
      />

      <InheritanceHeaderLine agent={agent} onManage={() => setActiveTab('inheritance')} />

      <div className="bg-white p-6 shadow-md rounded-lg border-t-4 border-indigo-600 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Copies: the replicas of the services the agent runs in, against their limit */}
          <div className={`border rounded-lg px-3 py-2 ${instancesAtCap ? 'bg-orange-50 border-orange-300' : liveInstanceCount > 0 ? 'bg-indigo-50 border-indigo-100' : 'bg-gray-50 border-gray-200'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${instancesAtCap ? 'text-orange-500' : liveInstanceCount > 0 ? 'text-indigo-500' : 'text-gray-400'}`}>{t('agentDetails.instances')}</div>
            <div className={`text-sm font-semibold ${instancesAtCap ? 'text-orange-900' : liveInstanceCount > 0 ? 'text-indigo-900' : 'text-gray-600'}`}>
              {liveInstanceCount} / {servingServices.length ? instanceCap : '—'} {t('agentDetails.live')}
            </div>
            <div className={`text-[10px] mt-0.5 ${instancesAtCap ? 'text-orange-600 font-medium' : 'text-gray-500'}`}>
              {instancesAtCap ? t('agentDetails.replicaLimitReached') : t('agentDetails.replicasOfServices')}
            </div>
          </div>

          {/* Services: the agent's own against the workspace's limit, or the runner it shares */}
          <div className={`border rounded-lg px-3 py-2 min-w-0 ${servingPaused ? 'bg-amber-50 border-amber-200' : servicesAtLimit ? 'bg-orange-50 border-orange-300' : ownServices.length ? 'bg-indigo-50 border-indigo-100' : 'bg-gray-50 border-gray-200'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${servingPaused ? 'text-amber-500' : servicesAtLimit ? 'text-orange-500' : ownServices.length ? 'text-indigo-500' : 'text-gray-400'}`}>{t('agentDetails.services')}</div>
            <div className={`text-sm font-semibold truncate ${servingPaused ? 'text-amber-900' : servicesAtLimit ? 'text-orange-900' : ownServices.length ? 'text-indigo-900' : 'text-gray-600'}`}>
              {ownServices.length} / {wsServiceLimit === Infinity ? '∞' : wsServiceLimit}
            </div>
            <div className={`text-[10px] mt-0.5 truncate ${servingPaused ? 'text-amber-700' : 'text-gray-500'}`}
                 title={servingServices.map((svc) => svc.name).join(', ')}>
              {servingPaused
                ? t('agentDetails.servicePaused')
                : ownServices.length
                  ? ownServices.map((svc) => (
                    <Link key={svc.service_id} to={`/services/${svc.service_id}`} className="hover:text-indigo-600 hover:underline mr-1.5">{svc.name}</Link>
                  ))
                  : runnerService
                    ? <Link to={`/services/${runnerService.service_id}`} className="hover:text-indigo-600 hover:underline">{t('agentDetails.runnerServing', { name: runnerService.name })}</Link>
                    : t('agentDetails.noServiceYet')}
            </div>
          </div>

          {/* Sessions: open runs against the slots the live copies give */}
          <div className={`border rounded-lg px-3 py-2 ${atCapacity ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-100'}`}>
            <div className={`text-[10px] uppercase tracking-wider font-semibold ${atCapacity ? 'text-red-500' : 'text-green-500'}`}>{t('agentDetails.sessions')}</div>
            {noLiveInstance ? (
              servingPaused ? (
                <div className="text-xs text-amber-700 font-medium mt-0.5">{t('agentDetails.servicePausedHint')}</div>
              ) : (
                <button type="button" onClick={() => setShowStartInstance(true)}
                        className="text-xs text-amber-700 font-medium mt-0.5 hover:underline text-left">
                  {t('agentDetails.noInstanceRunningStartA')}
                </button>
              )
            ) : (
              <>
                <div className={`text-sm font-semibold mb-1 ${atCapacity ? 'text-red-900' : 'text-green-900'}`}>
                  {t('agentDetails.sessionsOpen', { used: runningTasks.length, max: maxSessions })}
                </div>
                <div className={`h-1.5 rounded-full overflow-hidden ${atCapacity ? 'bg-red-200' : 'bg-green-200'}`}>
                  <div
                    className={`h-full rounded-full ${atCapacity ? 'bg-red-500' : sessionLoadFactor > 80 ? 'bg-orange-500' : 'bg-indigo-500'}`}
                    style={{ width: `${sessionLoadFactor}%` }}
                  />
                </div>
                <div className={`text-[10px] mt-0.5 ${atCapacity ? 'text-red-600 font-semibold' : 'text-green-700'}`}>
                  {atCapacity ? t('agentDetails.atCapacityNoSlots') : t('agentDetails.load', { pct: `${sessionLoadFactor}%` })}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="border-b border-gray-200 flex items-end justify-between gap-4">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'overview', label: t('agentDetails.tabs.overview'), icon: Activity },
            { id: 'inheritance', label: t('agentDetails.tabs.inheritance'), icon: GitBranch },
            { id: 'config', label: t('agentDetails.tabs.config'), icon: FileCode },
            { id: 'versions', label: t('agentDetails.tabs.versions'), icon: History },
            { id: 'model', label: t('agentDetails.tabs.model'), icon: BrainCircuit },
            { id: 'tools', label: t('agentDetails.tabs.tools'), icon: Wrench },
            { id: 'behavior', label: t('agentDetails.tabs.behavior'), icon: SlidersHorizontal },
            { id: 'memory', label: t('agentDetails.tabs.memory'), icon: Database },
            { id: 'skills', label: t('agentDetails.tabs.skills'), icon: BookOpen },
            { id: 'commands', label: t('agentDetails.tabs.commands'), icon: Terminal },
            // The agent's own pulse, its guardrails and its A/B experiments
            // (docs/proactive.md, docs/guardrails.md, docs/experiments.md).
            { id: 'pulse', label: t('agentDetails.tabs.pulse'), icon: Activity },
            { id: 'guardrails', label: t('agentDetails.tabs.guardrails'), icon: ShieldCheck },
            { id: 'experiments', label: t('agentDetails.tabs.experiments'), icon: FlaskConical },
            { id: 'tasks', label: t('agentDetails.tabs.tasks'), icon: Clock },
            { id: 'runs', label: t('agentDetails.tabs.runs'), icon: FileText },
            { id: 'sessions', label: t('agentDetails.tabs.sessions'), icon: History },
            // The live copies of *this* agent; the Runs tab is their journal.
            { id: 'instances', label: t('agentDetails.tabs.instances'), icon: Radio },
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
      {activeTab === 'inheritance' && (
        <InheritanceTab
          agentId={id}
          agent={agent}
          onChanged={fetchData}
          onEditOwnInstructions={() => setActiveTab('config')}
        />
      )}
      {activeTab === 'instances' && <InstancesTab />}
      {activeTab === 'sessions' && <SessionsTab />}
      {activeTab === 'runs' && <RunsTab />}
      {activeTab === 'memory' && <MemoryTab />}
      {activeTab === 'tools' && (
        <>
          <ToolsTab />
          {/* An imported (remote) agent runs its own loop elsewhere: it has no
              handoff tool to give, though it can still receive a handoff. */}
          {agent?.type !== 'remote' && <HandoffsCard agentId={id} agent={agent} onSaved={fetchData} />}
        </>
      )}
      {activeTab === 'behavior' && <BehaviorTab />}
      {activeTab === 'tasks' && <TasksTab />}
      {activeTab === 'commands' && <CommandsTab />}
      {activeTab === 'config' && <ConfigTab />}
      {activeTab === 'versions' && <VersionsTab />}
      {/* The agent's pulse: an imported agent runs elsewhere and is not
          scheduled from here. */}
      {activeTab === 'pulse' && (
        agent?.type !== 'remote'
          ? <ProactiveCard agentId={id} onSaved={fetchData} />
          : <p className="text-sm text-gray-500 mt-6">{t('agentDetails.pulseRemote')}</p>
      )}
      {activeTab === 'guardrails' && <AgentGuardrailsCard agentId={id} agent={agent} onSaved={fetchData} />}
      {activeTab === 'experiments' && <ExperimentCard agentId={id} />}
      {activeTab === 'model' && (
        <>
          <ModelTab />
          <LoopSettingsCard agentId={id} agent={agent} onSaved={fetchData} />
        </>
      )}
      {activeTab === 'docker' && <DockerTab />}
      {activeTab === 'skills' && <SkillsTab />}

      <AgentModals />
      {/* A save that meets somebody else's newer edit asks: reload or overwrite. */}
      <AgentConflictDialog agentId={id} />
      <StartInstanceModal
        open={showStartInstance}
        onClose={() => setShowStartInstance(false)}
        agentId={id}
        defaultWorkspace={selectedWorkspace}
      />
      <DeployServiceModal
        open={showDeploy}
        onClose={() => setShowDeploy(false)}
        agentId={id}
        defaultWorkspace={selectedWorkspace}
      />
    </PageContainer>
    </AgentPageContext.Provider>
  );
};

export default AgentDetails;
