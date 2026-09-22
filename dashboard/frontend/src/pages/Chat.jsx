import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useStream } from '../components/stream';
import ViewCard from '../views/ViewCard';
import { getAgents, getWorkspace, getProjects, getAgentDefinition, stopMessage, sendTelegramMessage, listFlows, getTeams } from '../api';
import ContextMeter from '../components/ContextMeter';
import {
  PlusCircle,
  Send,
  StopCircle,
  Bot,
  User,
  Trash2,
  MessageSquare,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  AlertCircle,
  FileText,
  RefreshCw,
  X,
  Paperclip,
  FolderGit2,
  Terminal,
  Zap,
  Send as SendIcon,
  Workflow,
  BrainCircuit,
  ListChecks,
  Database,
  Repeat,
  UsersRound,
  Link2,
  ArrowUpRight,
  Upload,
  Radio,
} from 'lucide-react';
import ContextEntityPicker from '../components/ContextEntityPicker';
import ProcessGraph, { TokenPill } from '../components/ProcessGraph';
import GraphMirror from '../components/GraphMirror';
import { EMPTY_GRAPH_RUN } from '../components/graphRun';
import { SKILL_TOOL } from '../components/processUtils';
import { SlotData } from '../components/SlotValue';
import { useI18n, LANGUAGES } from '../i18n';
import { useConversationStore } from '../components/chatStore';
import { useLiveChatTurn } from '../components/chatLiveTurn';
// The bubbles, the cards, the pickers and the panels this page is made of.
import { genId } from '../components/chat/turnState';
import { MAX_ATTACHMENT_BYTES, MAX_ATTACHMENT_COUNT, MAX_REFERENCE_COUNT } from '../components/chat/limits';
import { LiveThoughts, ReasoningStep } from '../components/chat/reasoning';
import { MessageBubble, TypingIndicator } from '../components/chat/MessageBubble';
import { targetId } from '../components/chat/targets';
import { AgentDropdown, FlowDropdown, TeamDropdown } from '../components/chat/targetPickers';
import { ArtifactsPanel, ProcessPanelContent } from '../components/chat/panels';
import { BuildMessage } from '../components/chat/BuildMessage';
import { ChatPageContext } from '../components/chat/context';
import ChatSidebar from '../components/chat/ChatSidebar';
import ChatTopBar from '../components/chat/ChatTopBar';
import ChatMessageList from '../components/chat/ChatMessageList';
import ChatComposer from '../components/chat/ChatComposer';
import ChatSidePanel from '../components/chat/ChatSidePanel';
import useChatTelegram from '../components/chat/useChatTelegram';
import useChatSessionStream from '../components/chat/useChatSessionStream';
import useChatProcess from '../components/chat/useChatProcess';
import useChatComposerInput from '../components/chat/useChatComposerInput';
import useChatSend from '../components/chat/useChatSend';

// ---------------------------------------------------------------------------
// Page-local preferences
// ---------------------------------------------------------------------------
// The conversations themselves are not here: they are service records, stored
// server-side and reached through `useConversationStore` (components/chatStore.js).
// What stays in the browser is what is true of this browser only — whether a
// panel is open, which view mode was last used.
//
// Whether the agent-process panel is open. Persisted so navigating away from the
// Chat page and back (which unmounts/remounts this component) keeps it open.
const PROCESS_OPEN_KEY = 'agent_hub_chat_process_open';
// 'chat' vs 'build' view mode, persisted across navigation for the same reason.
const VIEW_MODE_KEY = 'agent_hub_chat_view_mode';

// ---------------------------------------------------------------------------
// Slash commands
// ---------------------------------------------------------------------------
// Descriptions resolve through i18n at render time — the command names
// themselves are typed by the user and stay as they are.
const GLOBAL_COMMANDS = [
  { name: '/help', descriptionKey: 'chat.commands.help', template: '/help' },
  { name: '/clear', descriptionKey: 'chat.commands.clear', template: '/clear' },
  { name: '/new', descriptionKey: 'chat.commands.new', template: '/new' },
  { name: '/config', descriptionKey: 'chat.commands.config', template: '/config' },
];


// ---------------------------------------------------------------------------
// Main Chat page
// ---------------------------------------------------------------------------
export default function Chat() {
  const { t } = useI18n();
  const { convId: urlConvId } = useParams();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  // This tab's id on the shared SSE stream: sent with a turn so the
  // broadcast can name its author, and with a save for the same reason.
  const { clientId } = useStream();
  const [agents, setAgents] = useState([]);
  const [workspaceAllowedAgentIds, setWorkspaceAllowedAgentIds] = useState(null);
  const [selectedAgent, setSelectedAgent] = useState('');
  // Flow chat support: when targetMode === 'flow', messages run through the selected flow
  // (each user turn is processed by every node in the DAG in topological order).
  const [flows, setFlows] = useState([]);
  const [selectedFlow, setSelectedFlow] = useState('');
  // Team chat support: when targetMode === 'team', the message is handed to a
  // roster of agents that talk to each other; each board entry becomes a bubble.
  const [teams, setTeams] = useState([]);
  const [selectedTeam, setSelectedTeam] = useState('');
  const [targetMode, setTargetMode] = useState('agent'); // 'agent' | 'flow' | 'team'

  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');

  const [currentConvId, setCurrentConvId] = useState(urlConvId || null);
  // Whether a turn is being sent from this tab. Declared here because both the
  // conversation store and the live-turn mirror below are steered by it.
  const [loading, setLoading] = useState(false);
  // The conversation list is server state, not browser state: the store loads
  // it, fetches the open chat's transcript, and writes changes back. What this
  // page sees is the array it has always mutated.
  // `paused` while a turn is running here: a reload triggered by someone else's
  // save would drop the turn this tab is in the middle of writing.
  const {
    conversations, setConversations, removeConversation, syncError,
  } = useConversationStore(currentConvId, { paused: loading });
  const [telegramBindings, setTelegramBindings] = useState([]);
  const [telegramSending, setTelegramSending] = useState(false);
  const [telegramError, setTelegramError] = useState('');

  const [input, setInput] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [attachmentError, setAttachmentError] = useState('');
  // Hub records attached to the next message — {kind, id, label, icon, url}.
  // Separate from pendingAttachments: a reference is a pointer the server
  // resolves at send time, not a payload the browser carries.
  const [pendingReferences, setPendingReferences] = useState([]);
  // The paperclip's menu (a file, or one of the entity kinds) and the kind the
  // picker modal is open on (null = closed).
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [pickerKind, setPickerKind] = useState(null);
  const [contextKinds, setContextKinds] = useState([]);
  const [processOpen, setProcessOpen] = useState(() => {
    try { return localStorage.getItem(PROCESS_OPEN_KEY) === '1'; } catch { return false; }
  });
  // 'chat' = clean message bubbles (default). 'build' = full inline transcript
  // (messages + thinking + plan + tool calls) with an Artifacts (diffs) column.
  const [viewMode, setViewMode] = useState(() => {
    try { return localStorage.getItem(VIEW_MODE_KEY) === 'build' ? 'build' : 'chat'; } catch { return 'chat'; }
  });
  // Latest cumulative diff per file path for the current conversation.
  // Shape: { [path]: { op, path, diff, additions, deletions, binary, truncated, run_id } }
  const [artifacts, setArtifacts] = useState({});
  const [activeRunId, setActiveRunId] = useState(null);
  const [processLoading, setProcessLoading] = useState(false);
  const [processError, setProcessError] = useState('');
  // Where the current turn is inside an imported agent's own graph, folded by
  // components/graphRun.js. Lights the mirror while the run is in flight; the
  // record of the path is the timeline stored with the message.
  const [graphRun, setGraphRun] = useState(EMPTY_GRAPH_RUN);
  const [processInsights, setProcessInsights] = useState({
    messages: [],
    tools: [],
    thinking: [],
    message_runs: [],
    token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
    context_peak: 0,
    context_window_tokens: 0,
  });

  // Persist the process-panel open state so it survives leaving and returning to
  // the Chat page (the component unmounts on navigation, resetting React state).
  useEffect(() => {
    try { localStorage.setItem(PROCESS_OPEN_KEY, processOpen ? '1' : '0'); } catch { /* storage unavailable */ }
  }, [processOpen]);

  useEffect(() => {
    try { localStorage.setItem(VIEW_MODE_KEY, viewMode); } catch { /* storage unavailable */ }
  }, [viewMode]);

  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandMenuIndex, setCommandMenuIndex] = useState(0);

  // session_id from the backend — used to subscribe to continuation SSE
  const [sessionId, setSessionId] = useState(null);

  const abortCtrlRef = useRef(null);
  const continuationMsgIdRef = useRef(null);   // active continuation bubble on the session channel
  const messagesEndRef = useRef(null);
  const prevConvIdRef = useRef(undefined);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const loadedRunIdsRef = useRef(new Set());   // tracks which run_ids have been fetched
  const processInsightsRef = useRef(processInsights); // used inside loadProcessData to check if silent

  // ---- derived state ----
  // Top (normal) chat list: never show telegram-origin convs here — they live
  // in the dedicated Telegram panel below.
  const visibleConversations = useMemo(() => {
    const noTelegram = conversations.filter((c) => c.origin !== 'telegram');
    if (!selectedWorkspace || selectedWorkspace === 'default') return noTelegram;
    return noTelegram.filter((c) => c.workspace === selectedWorkspace);
  }, [conversations, selectedWorkspace]);

  // Strict workspace isolation: a Telegram binding is shown only when its
  // workspace exactly matches the active workspace. The `default` selection
  // matches only bindings that explicitly point at `default` (no fallback to
  // "show everything"), so each workspace owns its slice of the Telegram thread.
  const visibleTelegramBindings = useMemo(() => {
    if (!selectedWorkspace) return [];
    return telegramBindings.filter((b) => (b.workspace || null) === selectedWorkspace);
  }, [telegramBindings, selectedWorkspace]);

  const currentTelegramBinding = useMemo(() => {
    if (!currentConvId) return null;
    return telegramBindings.find((b) => b.conversation_id === currentConvId) || null;
  }, [telegramBindings, currentConvId]);

  const currentConv = conversations.find((c) => c.id === currentConvId) || null;
  // Memoised: the `|| []` fallback would otherwise be a new array on every
  // render, re-running every effect that watches the transcript.
  const messages = useMemo(() => currentConv?.messages || [], [currentConv]);
  // Context fill for the open conversation: whatever the most recent turn that
  // reported it left behind. Read off the transcript rather than tracked live,
  // so it is still right after a reload or a switch between conversations, and
  // empties by itself when /clear empties the messages.
  const contextUsage = useMemo(() => {
    const msgs = currentConv?.messages || [];
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m?.context_window || m?.context_overflow) {
        return {
          used: m.context_used || 0,
          window: m.context_window || 0,
          overflow: Boolean(m.context_overflow),
        };
      }
    }
    return { used: 0, window: 0, overflow: false };
  }, [currentConv]);
  const conversationRunIds = useMemo(() => {
    const ids = new Set();
    for (const m of (currentConv?.messages || [])) {
      if (m?.role === 'agent' && m?.run_id) ids.add(String(m.run_id));
    }
    return ids;
  }, [currentConv]);

  // A turn someone else is running in this same conversation — another tab,
  // another device, Telegram, an agent writing to its own inbox. It is mirrored
  // live and never saved: the tab that ran it writes the transcript, and this
  // mirror steps aside as soon as that lands (hence the run ids above).
  // `muted` while this tab is the one sending: it renders its own stream and
  // would otherwise draw every token twice.
  const liveTurn = useLiveChatTurn(currentConvId, {
    muted: loading,
    resolvedRunIds: conversationRunIds,
  });
  const liveMessages = useMemo(() => {
    if (!liveTurn) return [];
    const bubbles = [];
    if ((liveTurn.user || '').trim()) {
      bubbles.push({ id: 'live-user', role: 'user', content: liveTurn.user });
    }
    bubbles.push({
      id: 'live-agent',
      role: 'agent',
      agent_id: liveTurn.agentId || selectedAgent,
      content: liveTurn.text,
      reasoning: liveTurn.thinking,
      thinking_live: liveTurn.thinkingLive,
      running_tool: liveTurn.tools.find((x) => x.output === null && x.error === null)?.tool || null,
      error: liveTurn.status === 'failed',
      run_id: liveTurn.runId,
    });
    return bubbles;
  }, [liveTurn, selectedAgent]);
  // What the feed renders: the stored transcript, plus the mirrored turn while
  // one is in flight. The mirror is appended here and nowhere else, so nothing
  // downstream of `messages` (persistence, context fill, run ids) ever sees it.
  const renderedMessages = useMemo(
    () => (liveMessages.length ? [...messages, ...liveMessages] : messages),
    [messages, liveMessages],
  );
  // Build-view timelines for reloaded messages. The live `msg.timeline` (tool
  // calls + thoughts) is not stored with the chat (TRANSIENT_MSG_FIELDS in
  // components/chatStore.js), so a conversation reopened from the store has none. Reconstruct it per run_id from
  // the server-fetched process insights — same reasoning+tools merge the Process
  // graph uses — so the Build view shows tools and thoughts again, not just the
  // final text. Keyed by run_id; consumed only in build view.
  const runTimelineByRunId = useMemo(() => {
    const map = {};
    for (const mr of (processInsights.message_runs || [])) {
      const rid = String(mr?.run_id || '');
      if (!rid) continue;
      const merged = [
        ...((mr.reasoning || []).map((r) => ({ step: r.step, entry: { type: 'reasoning', kind: r.kind || 'think', step: r.step, content: r.content } }))),
        ...((mr.tools || []).map((t) => ({ step: t.step, entry: { type: 'tool', step: t.step, tool: t.tool, input: t.input, output: t.output } }))),
      ].sort((a, b) => (a.step ?? Infinity) - (b.step ?? Infinity));
      const timeline = merged.map((m) => m.entry);
      // The final response text follows the tool/thought steps.
      if ((mr.output || '').trim()) timeline.push({ type: 'text', text: mr.output });
      map[rid] = timeline;
    }
    return map;
  }, [processInsights]);

  const agentName = agents.find((a) => a.id === selectedAgent)?.name || selectedAgent || 'Agent';
  // Whether the composer has something to send to, and what it says while it
  // waits. Derived once for all three target modes so a new mode cannot be
  // added to one control and forgotten in the next.
  const hasTarget = Boolean(targetId(targetMode, { selectedAgent, selectedFlow, selectedTeam }));
  const composerPlaceholder = (() => {
    if (targetMode === 'flow') {
      return selectedFlow
        ? t('chat.placeholders.flow', { name: flows.find((f) => f.id === selectedFlow)?.name || '' })
        : t('chat.placeholders.pickFlow');
    }
    if (targetMode === 'team') {
      return selectedTeam
        ? t('chat.placeholders.team', { name: teams.find((tm) => tm.team_id === selectedTeam)?.name || '' })
        : t('chat.placeholders.pickTeam');
    }
    return selectedAgent
      ? t('chat.placeholders.agent', { name: agents.find((a) => a.id === selectedAgent)?.name || selectedAgent })
      : t('chat.placeholders.noAgent');
  })();
  const _agentObj = agents.find((a) => a.id === selectedAgent) || {};
  // Present only for an imported agent that publishes its own shape; every
  // other agent leaves the mirror out of the panel entirely.
  const agentTopology = _agentObj.remote?.topology || null;
  // The highlight belongs to the run being watched, not to the page: switching
  // agent or conversation leaves a lit node that nothing is running in.
  useEffect(() => { setGraphRun(EMPTY_GRAPH_RUN); }, [selectedAgent, currentConvId]);
  // provider/model are top-level fields on AgentSpec
  const agentProvider = _agentObj.provider || 'inherit';
  const agentModel = _agentObj.model || '';
  const selectableAgents = useMemo(() => {
    if (!selectedWorkspace) return agents;
    const allowedSet = new Set(workspaceAllowedAgentIds || []);
    return agents.filter((a) => allowedSet.has(a.id));
  }, [agents, selectedWorkspace, workspaceAllowedAgentIds]);

  const allCommands = useMemo(() => {
    const agentCmds = agents.find((a) => a.id === selectedAgent)?.commands || [];
    return [...GLOBAL_COMMANDS, ...agentCmds];
  }, [agents, selectedAgent]);

  const commandSuggestions = useMemo(() => {
    if (!commandMenuOpen) return [];
    const query = input.toLowerCase();
    return allCommands.filter((cmd) => cmd.name.toLowerCase().startsWith(query));
  }, [commandMenuOpen, input, allCommands]);

  // ---- sync URL → state ----
  useEffect(() => {
    setCurrentConvId(urlConvId || null);
  }, [urlConvId]);

  // ---- close the active conversation when its workspace doesn't match ----
  // Each workspace owns its own chat history; switching workspace should drop
  // the current conv and land on the start page rather than show a chat that
  // belongs to a different workspace.
  useEffect(() => {
    if (!currentConvId) return;
    if (!selectedWorkspace) return;
    // The Telegram binding takes priority — it carries the authoritative workspace
    // for any conv promoted from a binding (the local conv may not exist yet).
    const tgBinding = telegramBindings.find((b) => b.conversation_id === currentConvId);
    if (tgBinding) {
      if ((tgBinding.workspace || null) !== selectedWorkspace) {
        navigate('/chat');
      }
      return;
    }
    const conv = conversations.find((c) => c.id === currentConvId);
    if (!conv) return;
    const convWs = conv.workspace || null;
    // For non-Telegram conversations, the `default` selection means "no filter",
    // matching the current visibleConversations behaviour.
    if (selectedWorkspace === 'default') return;
    if (convWs !== selectedWorkspace) {
      navigate('/chat');
    }
  }, [selectedWorkspace, currentConvId, telegramBindings, conversations, navigate]);

  // Persistence lives in useConversationStore: changes to `conversations` are
  // debounced into `PUT /api/chats/{id}` rather than rewritten into localStorage.

  // ---- load agents ----
  useEffect(() => {
    getAgents(selectedWorkspace || 'default')
      .then((r) => { setAgents(r.data || []); })
      .catch(() => {});
  }, [selectedWorkspace]);

  // ---- load flows (re-runs when workspace changes so we see workspace-bound flows) ----
  useEffect(() => {
    listFlows(selectedWorkspace || undefined)
      .then((r) => setFlows(r.data || []))
      .catch(() => setFlows([]));
  }, [selectedWorkspace]);

  // ---- load teams (workspace-scoped, same as flows) ----
  useEffect(() => {
    getTeams(selectedWorkspace || undefined)
      .then((r) => setTeams(r.data?.teams || []))
      .catch(() => setTeams([]));
  }, [selectedWorkspace]);

  useChatTelegram({
    currentTelegramBinding, messages, setConversations, setSessionId,
    setTelegramBindings,
  });
  // ---- load projects for selected workspace ----
  useEffect(() => {
    if (!selectedWorkspace) { setProjects([]); setSelectedProject(''); return; }
    getProjects(selectedWorkspace)
      .then((r) => setProjects(r.data || []))
      .catch(() => setProjects([]));
  }, [selectedWorkspace]);

  // ---- load allowed agents for selected workspace ----
  useEffect(() => {
    if (!selectedWorkspace) {
      setWorkspaceAllowedAgentIds(null);
      return;
    }
    getWorkspace(selectedWorkspace)
      .then((r) => {
        setWorkspaceAllowedAgentIds(r.data?.metadata?.allowed_agents || []);
      })
      .catch(() => {
        setWorkspaceAllowedAgentIds([]);
      });
  }, [selectedWorkspace]);

  // ---- ensure selected agent is valid for current workspace ----
  useEffect(() => {
    if (!selectableAgents.length) {
      if (selectedAgent) setSelectedAgent('');
      return;
    }
    const stillValid = selectableAgents.some((a) => a.id === selectedAgent);
    if (!stillValid) {
      const defaultAgent = selectableAgents.find((a) => a.is_default_chat_agent);
      setSelectedAgent((defaultAgent || selectableAgents[0]).id);
    }
  }, [selectableAgents, selectedAgent]);

  // ---- focus textarea on mount and whenever loading ends ----
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);  // mount only

  useEffect(() => {
    if (!loading) {
      // Defer by one tick so React finishes re-enabling the textarea first
      const t = setTimeout(() => textareaRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [loading]);

  // ---- auto-scroll ----
  useEffect(() => {
    const isSwitching = prevConvIdRef.current !== currentConvId;
    prevConvIdRef.current = currentConvId;
    messagesEndRef.current?.scrollIntoView({ behavior: isSwitching ? 'instant' : 'smooth' });
  }, [messages, loading, currentConvId]);

  // Select latest run when switching conversations
  useEffect(() => {
    if (!currentConv) {
      setActiveRunId(null);
      return;
    }
    const latestRunMsg = [...(currentConv.messages || [])]
      .reverse()
      .find((m) => m.role === 'agent' && m.run_id);
    setActiveRunId(latestRunMsg?.run_id || null);
  }, [currentConvId, currentConv]);

  useChatSessionStream({
    continuationMsgIdRef, currentConvId, mergeArtifact, messages, sessionId,
    setActiveRunId, setConversations,
  });

  const { loadProcessData } = useChatProcess({
    activeRunId, continuationMsgIdRef, conversationRunIds, currentConvId, loadedRunIdsRef,
    loading, processInsights, processInsightsRef, processOpen, setArtifacts,
    setProcessError, setProcessInsights, setProcessLoading, setSessionId, t, viewMode,
  });

  const {
    resizeTextarea, onPickFiles, removeAttachment, toggleAttachmentStore,
    addReferences, removeReference,
  } = useChatComposerInput({
    attachMenuOpen, contextKinds, pendingAttachments, selectedWorkspace,
    setAttachmentError, setContextKinds, setPendingAttachments, setPendingReferences,
    textareaRef,
  });

  // Merge a streamed/persisted artifact into the per-path map (last write wins).
  const mergeArtifact = useCallback((art) => {
    if (!art || !art.path) return;
    setArtifacts((prev) => ({ ...prev, [art.path]: { ...art } }));
  }, []);

  // ---- new conversation ----
  // Don't create a conversation record yet — just land on the empty start page.
  // sendMessage lazily creates the conversation when the first message is sent,
  // so repeated "New chat" clicks never pile up empty conversations in the list.
  const newConversation = useCallback(() => {
    setCurrentConvId(null);
    setInput('');
    navigate('/chat');
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [navigate]);

  // ---- delete conversation ----
  const deleteConversation = useCallback((id, e) => {
    e.stopPropagation();
    const conv = conversations.find((c) => c.id === id);
    const label = conv?.title || t('chat.thisConversation');
    if (!window.confirm(t('chat.confirmDeleteConversation', { label }))) return;
    removeConversation(id);
    if (currentConvId === id) navigate('/chat');
  }, [conversations, currentConvId, navigate, removeConversation, t]);

  // ---- slash command selection ----
  const selectCommand = useCallback(async (cmd) => {
    setCommandMenuOpen(false);
    setCommandMenuIndex(0);
    if (cmd.name === '/clear') {
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [] } : c));
      }
      setInput('');
      return;
    }
    if (cmd.name === '/new') {
      setCurrentConvId(null);
      setInput('');
      setTimeout(() => textareaRef.current?.focus(), 0);
      return;
    }
    if (cmd.name === '/help') {
      const lines = allCommands
        .map((c) => `**${c.name}** — ${c.descriptionKey ? t(c.descriptionKey) : c.description}`)
        .join('\n');
      const helpMsg = { id: genId(), role: 'agent', content: `${t('chat.availableCommands')}\n\n${lines}`, error: false };
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [...c.messages, helpMsg] } : c));
      }
      setInput('');
      return;
    }
    if (cmd.name === '/config') {
      const agentObj = agents.find((a) => a.id === selectedAgent) || {};
      const inherit = t('chat.inheritGlobal');
      const provider = agentObj.provider || inherit;
      const model = agentObj.model || inherit;
      const baseUrl = agentObj.base_url || '—';
      const temperature = agentObj.temperature != null ? agentObj.temperature : inherit;
      const maxTokens = agentObj.max_tokens != null ? agentObj.max_tokens : inherit;
      const tools = (agentObj.tools || []).length > 0 ? (agentObj.tools || []).join(', ') : '—';
      const streaming = agentObj.streaming ? t('common.yes') : t('common.no');
      const verbose = agentObj.verbose ? t('common.yes') : t('common.no');
      setInput('');
      let systemPrompt = agentObj.system_prompt || '';
      try {
        const defResp = await getAgentDefinition(selectedAgent);
        systemPrompt = defResp.data?.system_prompt || systemPrompt;
      } catch { /* keep the default system prompt */ }
      const lines = [
        `**${t('chat.config.agent')}:** ${agentObj.name || selectedAgent} (\`${agentObj.id || selectedAgent}\`)`,
        `**${t('chat.config.description')}:** ${agentObj.description || '—'}`,
        `**${t('chat.config.domain')}:** ${agentObj.domain || '—'}`,
        ``,
        `**${t('chat.config.provider')}:** ${provider}`,
        `**${t('chat.config.model')}:** ${model}`,
        `**${t('chat.config.baseUrl')}:** ${baseUrl}`,
        `**${t('chat.config.temperature')}:** ${temperature}`,
        `**${t('chat.config.maxTokens')}:** ${maxTokens}`,
        `**${t('chat.config.streaming')}:** ${streaming}`,
        `**${t('chat.config.verbose')}:** ${verbose}`,
        ``,
        `**${t('chat.config.tools')}:** ${tools}`,
        ``,
        `**${t('chat.config.systemPrompt')}:**\n${systemPrompt || '—'}`,
      ].join('\n');
      const configMsg = { id: genId(), role: 'agent', content: lines, error: false };
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [...c.messages, configMsg] } : c));
      }
      return;
    }
    setInput(cmd.template);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [agents, allCommands, currentConvId, selectedAgent, setConversations, t]);

  const { sendMessage } = useChatSend({
    abortCtrlRef, clientId, conversations, currentConvId, input, loadProcessData, loading,
    mergeArtifact, messages, navigate, pendingAttachments, pendingReferences, processOpen,
    selectCommand, selectedAgent, selectedFlow, selectedProject, selectedTeam,
    selectedWorkspace, setActiveRunId, setAttachmentError, setConversations,
    setCurrentConvId, setGraphRun, setInput, setLoading, setPendingAttachments,
    setPendingReferences, setProcessInsights, setSessionId, t, targetMode, textareaRef,
  });
  const jumpToArtifact = useCallback((path) => {
    setTimeout(() => {
      const el = document.querySelector(`[data-artifact-path="${CSS.escape(path)}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 50);
  }, []);

  const stopGeneration = () => {
    abortCtrlRef.current?.abort();
    if (activeRunId) stopMessage(activeRunId).catch(() => {});
    setLoading(false);
  };

  // True only when the conversation's binding workspace matches the active workspace.
  // Reply-as-bot is forbidden across workspaces to keep the Telegram surface
  // anchored to whichever workspace the operator is actually working in.
  const telegramReplyAllowed = Boolean(
    currentTelegramBinding
    && selectedWorkspace
    && (currentTelegramBinding.workspace || null) === selectedWorkspace
  );

  // Send the composer text back to a Telegram chat as the bot (debug surface).
  const sendAsBot = useCallback(async () => {
    if (!currentTelegramBinding) return;
    if (!telegramReplyAllowed) return;
    const text = input.trim();
    if (!text) return;
    setTelegramSending(true);
    setTelegramError('');
    try {
      await sendTelegramMessage(currentTelegramBinding.chat_id, text);
      const convId = currentTelegramBinding.conversation_id;
      setConversations((prev) =>
        prev.map((c) => c.id !== convId ? c : {
          ...c,
          messages: [...(c.messages || []), {
            id: genId(),
            role: 'user',
            content: text,
            origin: 'telegram-bot',
            createdAt: new Date().toISOString(),
          }],
        })
      );
      setInput('');
    } catch (e) {
      setTelegramError(e.response?.data?.detail || e.message || t('chat.sendFailed'));
    } finally {
      setTelegramSending(false);
    }
  }, [currentTelegramBinding, telegramReplyAllowed, input, setConversations, t]);

  const handleKeyDown = (e) => {
    if (commandMenuOpen && commandSuggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCommandMenuIndex((i) => Math.min(i + 1, commandSuggestions.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCommandMenuIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && commandSuggestions.length > 0)) {
        e.preventDefault();
        selectCommand(commandSuggestions[commandMenuIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setCommandMenuOpen(false);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (currentTelegramBinding) {
        sendAsBot();
      } else {
        sendMessage();
      }
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  // Published once for the five panes below; see `chat/context.js`.
  const page = {
    activeRunId, addReferences, agentModel, agentName, agentProvider, agentTopology, agents,
    artifacts, attachMenuOpen, attachmentError, commandMenuIndex, commandMenuOpen,
    commandSuggestions, composerPlaceholder, contextKinds, contextUsage, conversations,
    currentConv, currentConvId, currentTelegramBinding, deleteConversation, fileInputRef,
    flows, graphRun, handleKeyDown, hasTarget, input, jumpToArtifact, liveMessages, liveTurn,
    loadProcessData, loading, messages, messagesEndRef, navigate, newConversation, onPickFiles,
    pendingAttachments, pendingReferences, pickerKind, processError, processInsights,
    processLoading, processOpen, projects, removeAttachment, removeReference, renderedMessages,
    resizeTextarea, runTimelineByRunId, selectCommand, selectableAgents, selectedAgent,
    selectedFlow, selectedProject, selectedTeam, selectedWorkspace, sendAsBot, sendMessage,
    setAttachMenuOpen, setCommandMenuIndex, setCommandMenuOpen, setConversations, setInput,
    setPickerKind, setProcessOpen, setSelectedAgent, setSelectedFlow, setSelectedProject,
    setSelectedTeam, setTargetMode, setViewMode, stopGeneration, syncError, t, targetMode,
    teams, telegramError, telegramReplyAllowed, telegramSending, textareaRef,
    toggleAttachmentStore, viewMode, visibleConversations, visibleTelegramBindings
  };

  return (
    <ChatPageContext.Provider value={page}>
    <div className="h-full flex overflow-hidden">
      <ChatSidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <ChatTopBar />
        <ChatMessageList />
        <ChatComposer />
      </div>
      <ChatSidePanel />
    </div>
    </ChatPageContext.Provider>
  );
}
