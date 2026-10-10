import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useStream } from '../components/stream';
import ViewCard from '../views/ViewCard';
import { addPersonalMemoryNote, getAgents, getProjects, getAgentDefinition, stopMessage, sendTelegramMessage, listFlows, getTeams } from '../api';
import { loadWorkspaceSummary } from '../api/workspaceSummary';
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
import { liveHandoffBubbles } from '../components/chat/handoff';
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
import { ChatMathActionsContext } from '../components/chat/chatMarkdownContext';
import ChatSidebar from '../components/chat/ChatSidebar';
import ChatTopBar from '../components/chat/ChatTopBar';
import ChatMessageList from '../components/chat/ChatMessageList';
import ChatComposer from '../components/chat/ChatComposer';
import ChatSidePanel from '../components/chat/ChatSidePanel';
import useChatTelegram from '../components/chat/useChatTelegram';
import useChatSessionStream from '../components/chat/useChatSessionStream';
import useChatProcess from '../components/chat/useChatProcess';
import { useConversationCode } from '../components/chat/useConversationCode';
import { conversationViews } from '../components/chat/turnViews';
import { replyCodeBlocks } from '../components/chat/replyCode';
import useChatComposerInput from '../components/chat/useChatComposerInput';
import useChatSend from '../components/chat/useChatSend';
import useChatTurns from '../components/chat/useChatTurns';
import useChatSteering from '../components/chat/useChatSteering';
import useRunningChats from '../components/chat/useRunningChats';

const EMPTY_MESSAGES = [];

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
// Whether the code panel is open, persisted for the same reason.
const CODE_OPEN_KEY = 'agent_hub_chat_code_open';
// 'chat' vs 'build' view mode, persisted across navigation for the same reason.
const VIEW_MODE_KEY = 'agent_hub_chat_view_mode';
// Whether the artifacts panel (files and views the turns produced) is open.
const ARTIFACTS_OPEN_KEY = 'agent_hub_chat_build_panel_open';

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
  // broadcast can name its author, and with a save for the same reason. The
  // turns themselves arrive over that stream (chat/send/channelStream.js).
  const stream = useStream();
  const { clientId } = stream;
  const [agents, setAgents] = useState([]);
  const [workspaceAllowedAgentIds, setWorkspaceAllowedAgentIds] = useState(null);
  // Whether the workspace has personal memory (memory/personal.py) at all.
  const [personalMemoryOn, setPersonalMemoryOn] = useState(true);
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
  // The open conversation as of the last commit, for a turn's event handlers,
  // which outlive the render they were made in (see useChatSend).
  const currentConvIdRef = useRef(currentConvId);
  useLayoutEffect(() => { currentConvIdRef.current = currentConvId; }, [currentConvId]);
  // On a phone the conversation list is a drawer (ChatSidebar), open from the
  // top bar and closed again by picking or starting a conversation.
  const [listOpen, setListOpen] = useState(false);
  // Closed again whenever the URL moves to another conversation (adjusted while
  // rendering, not in an effect).
  const [listOpenFor, setListOpenFor] = useState(urlConvId);
  if (urlConvId !== listOpenFor) {
    setListOpenFor(urlConvId);
    setListOpen(false);
  }
  // The turns this tab is sending, one per conversation at most, so several
  // conversations can be answering at once (components/chat/useChatTurns.js).
  // `loading` is the open conversation's: declared here because both the
  // conversation store and the live-turn mirror below are steered by it.
  const {
    turns, begin: beginTurn, note: noteTurn, end: endTurn, get: getTurn, isRunning: isTurnRunning,
  } = useChatTurns();
  const loading = Boolean(currentConvId && turns[currentConvId]);
  // Every conversation being answered now, by anyone (the list marks them);
  // `turns` adds this tab's own the moment they are sent.
  const runningChats = useRunningChats();
  // The conversation list is server state, not browser state: the store loads
  // it, fetches the open chat's transcript, and writes changes back. What this
  // page sees is the array it has always mutated.
  // `paused` for a conversation with a turn running here: a reload triggered by
  // someone else's save would drop the turn this tab is in the middle of writing.
  const {
    conversations, setConversations, removeConversation, syncError,
  } = useConversationStore(currentConvId, { paused: isTurnRunning });
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
  // Artifacts or Code is the column beside the transcript (one at a time, see
  // ChatSidePanel); Process, in chat view only, is a column of its own.
  const [codeOpen, setCodeOpen] = useState(() => {
    try { return localStorage.getItem(CODE_OPEN_KEY) === '1'; } catch { return false; }
  });
  // The snippet the Code panel should show next: set by "Open in Code panel"
  // on a code block in a reply, `{ view, nonce }` so opening the same one
  // twice still selects it.
  const [codeFocus, setCodeFocus] = useState(null);
  // 'chat' = clean message bubbles (default). 'build' = full inline transcript
  // (messages + thinking + plan + tool calls).
  const [viewMode, setViewMode] = useState(() => {
    try { return localStorage.getItem(VIEW_MODE_KEY) === 'build' ? 'build' : 'chat'; } catch { return 'chat'; }
  });
  const [artifactsOpen, setArtifactsOpen] = useState(() => {
    try { return localStorage.getItem(ARTIFACTS_OPEN_KEY) === '1'; } catch { return false; }
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
    try { localStorage.setItem(CODE_OPEN_KEY, codeOpen ? '1' : '0'); } catch { /* storage unavailable */ }
  }, [codeOpen]);

  useEffect(() => {
    try { localStorage.setItem(VIEW_MODE_KEY, viewMode); } catch { /* storage unavailable */ }
  }, [viewMode]);

  useEffect(() => {
    try { localStorage.setItem(ARTIFACTS_OPEN_KEY, artifactsOpen ? '1' : '0'); } catch { /* storage unavailable */ }
  }, [artifactsOpen]);

  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandMenuIndex, setCommandMenuIndex] = useState(0);

  // session_id from the backend — used to subscribe to continuation SSE
  const [sessionId, setSessionId] = useState(null);

  const continuationMsgIdRef = useRef(null);   // active continuation bubble on the session channel
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const loadedRunIdsRef = useRef(new Set());   // tracks which run_ids have been fetched
  const processInsightsRef = useRef(processInsights); // used inside loadProcessData to check if silent

  // ---- derived state ----
  // Top (normal) chat list: never show telegram-origin convs here — they live
  // in the dedicated Telegram panel below.
  // A conversation with the Assistant stays in the workspace it started in,
  // like any other (routes/chats.py lists them the same way).
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
  // A stable fallback (EMPTY_MESSAGES): a fresh `[]` per render would re-run
  // every effect that watches the transcript.
  const currentMessages = currentConv?.messages;
  const messages = currentMessages || EMPTY_MESSAGES;
  // Context fill for the open conversation: whatever the most recent turn that
  // reported it left behind. Read off the transcript rather than tracked live,
  // so it is still right after a reload or a switch between conversations, and
  // empties by itself when /clear empties the messages.
  const contextUsage = useMemo(() => {
    const m = (currentMessages || []).findLast((x) => x?.context_window || x?.context_overflow);
    if (!m) return { used: 0, window: 0, overflow: false };
    return {
      used: m.context_used || 0,
      window: m.context_window || 0,
      overflow: Boolean(m.context_overflow),
    };
  }, [currentMessages]);
  const conversationRunIds = useMemo(() => {
    const ids = new Set();
    for (const m of (currentMessages || [])) {
      if (m?.role === 'agent' && m?.run_id) ids.add(String(m.run_id));
    }
    return ids;
  }, [currentMessages]);

  // A turn someone else is running in this same conversation — another tab,
  // another device, Telegram, an agent writing to its own inbox. It is mirrored
  // live and never saved: the tab that ran it writes the transcript, and this
  // mirror steps aside as soon as that lands (hence the run ids above).
  // `muted` while this tab is the one sending: it renders its own stream and
  // would otherwise draw every token twice.
  const liveTurnSeen = useLiveChatTurn(currentConvId, {
    muted: loading,
    resolvedRunIds: conversationRunIds,
    keepResolved: true,
  });
  // The transcript's last bubble can be a reply still being written: the page
  // sending it saves as it streams, and a page that was left mid-turn saved it
  // that way last. While the live mirror follows that run it draws the bubble
  // instead, so the reply keeps moving rather than standing at the last save.
  const unfinishedRunId = useMemo(() => {
    const last = messages[messages.length - 1];
    return last?.role === 'agent' && last.run_id && last.duration_ms == null
      && last.total_tokens == null && !last.error
      ? String(last.run_id) : null;
  }, [messages]);
  const mirrorTakesOver = Boolean(
    !loading && unfinishedRunId && liveTurnSeen?.runId && String(liveTurnSeen.runId) === unfinishedRunId,
  );
  const liveTurn = mirrorTakesOver || (liveTurnSeen && !liveTurnSeen.resolved) ? liveTurnSeen : null;
  const liveMessages = useMemo(() => {
    if (!liveTurn) return [];
    const bubbles = [];
    // Taking over a stored bubble: the person's message is stored above it.
    if (!mirrorTakesOver && (liveTurn.user || '').trim()) {
      bubbles.push({ id: 'live-user', role: 'user', content: liveTurn.user });
    }
    // A turn that changed hands: each handing agent's reply, then the agent
    // answering now, under the divider its handoff draws.
    const handoffs = liveTurn.handoffs || [];
    bubbles.push(...liveHandoffBubbles(handoffs));
    bubbles.push({
      id: 'live-agent',
      role: 'agent',
      ...(handoffs.length ? { handoff: handoffs[handoffs.length - 1] } : {}),
      agent_id: liveTurn.agentId || selectedAgent,
      content: liveTurn.text,
      reasoning: liveTurn.thinking,
      thinking_live: liveTurn.thinkingLive,
      running_tool: liveTurn.tools.find((x) => x.output === null && x.error === null)?.tool || null,
      error: liveTurn.status === 'failed',
      run_id: liveTurn.runId,
    });
    return bubbles;
  }, [liveTurn, mirrorTakesOver, selectedAgent]);
  // What the feed renders: the stored transcript, plus the mirrored turn while
  // one is in flight. The mirror is appended here and nowhere else, so nothing
  // downstream of `messages` (persistence, context fill, run ids) ever sees it.
  const renderedMessages = useMemo(() => {
    if (!liveMessages.length) return messages;
    const stored = mirrorTakesOver ? messages.slice(0, -1) : messages;
    return [...stored, ...liveMessages];
  }, [messages, liveMessages, mirrorTakesOver]);
  // Build-view timelines for messages stored before the trail was kept with
  // the chat (components/chatStore.js stores it now, clipped). Reconstructed
  // per run_id from the server-fetched process insights, the same reasoning
  // and tools merge the Process graph uses, so the Build view shows tools and
  // thoughts for those too, not just the final text. Consumed only in build view.
  const runTimelineByRunId = useMemo(() => {
    const map = {};
    for (const mr of (processInsights.message_runs || [])) {
      const rid = String(mr?.run_id || '');
      if (!rid) continue;
      const merged = [
        ...((mr.reasoning || []).map((r) => ({ step: r.step, entry: { type: 'reasoning', kind: r.kind || 'think', step: r.step, content: r.content } }))),
        ...((mr.tools || []).map((t) => ({ step: t.step, entry: { type: 'tool', step: t.step, tool: t.tool, input: t.input, output: t.output, status: t.status } }))),
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
  // Reset while rendering when either changes, not in an effect.
  const graphRunKey = `${selectedAgent}|${currentConvId}`;
  const [graphRunFor, setGraphRunFor] = useState(graphRunKey);
  if (graphRunKey !== graphRunFor) {
    setGraphRunFor(graphRunKey);
    setGraphRun(EMPTY_GRAPH_RUN);
  }
  // A snippet opened from a reply belongs to that conversation: the Code panel
  // of the next one must not pick it up again when it refetches its list.
  const [codeFocusFor, setCodeFocusFor] = useState(currentConvId);
  if (currentConvId !== codeFocusFor) {
    setCodeFocusFor(currentConvId);
    setCodeFocus(null);
  }
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

  // A chat turn running in the open conversation that this page is not
  // sending: another tab's, another device's, or this tab's own from before
  // the Chat page was left. The composer can still talk to it (useChatSteering).
  // A Telegram thread's turns are the bot's, and its composer replies as the bot.
  const liveRunId = (
    !loading && !currentTelegramBinding && targetMode === 'agent'
    && liveTurnSeen?.status === 'running' && ['chat', 'steer'].includes(liveTurnSeen.source)
  ) ? (liveTurnSeen.runId || null) : null;

  // ---- sync URL → state ----
  // Adjusted while rendering when the URL moves, not in an effect.
  const [seenUrlConvId, setSeenUrlConvId] = useState(urlConvId);
  if (urlConvId !== seenUrlConvId) {
    setSeenUrlConvId(urlConvId);
    setCurrentConvId(urlConvId || null);
  }

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
    // For non-Telegram conversations, the `default` selection means "no filter",
    // matching the current visibleConversations behaviour.
    if (selectedWorkspace === 'default') return;
    if ((conv.workspace || null) !== selectedWorkspace) {
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
  // Leaving every workspace clears what was loaded for the last one (adjusted
  // while rendering); the allowed agents below reset with it.
  const [seenWorkspace, setSeenWorkspace] = useState(selectedWorkspace);
  if (selectedWorkspace !== seenWorkspace) {
    setSeenWorkspace(selectedWorkspace);
    if (!selectedWorkspace) {
      setProjects([]);
      setSelectedProject('');
      setWorkspaceAllowedAgentIds(null);
    }
  }
  useEffect(() => {
    if (!selectedWorkspace) return;
    getProjects(selectedWorkspace)
      .then((r) => setProjects(r.data || []))
      .catch(() => setProjects([]));
  }, [selectedWorkspace]);

  // ---- load allowed agents for selected workspace ----
  useEffect(() => {
    if (!selectedWorkspace) return;
    // One summary request shared with the header and the palette
    // (api/workspaceSummary.js), not the full record with its task list.
    loadWorkspaceSummary(selectedWorkspace)
      .then((summary) => {
        setWorkspaceAllowedAgentIds(summary?.allowed_agents || []);
      })
      .catch(() => {
        setWorkspaceAllowedAgentIds([]);
      });
  }, [selectedWorkspace]);

  // ---- ensure selected agent is valid for current workspace ----
  // Adjusted while rendering, so the page never draws an agent the workspace
  // does not offer.
  if (!selectableAgents.length) {
    if (selectedAgent) setSelectedAgent('');
  } else if (!selectableAgents.some((a) => a.id === selectedAgent)) {
    const defaultAgent = selectableAgents.find((a) => a.is_default_chat_agent);
    setSelectedAgent((defaultAgent || selectableAgents[0]).id);
  }

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

  // Select latest run when switching conversations
  // Adjusted while rendering whenever the conversation or its record changes.
  const [runPickedId, setRunPickedId] = useState(undefined);
  const [runPickedConv, setRunPickedConv] = useState(undefined);
  if (runPickedId !== currentConvId || runPickedConv !== currentConv) {
    setRunPickedId(currentConvId);
    setRunPickedConv(currentConv);
    if (!currentConv) {
      setActiveRunId(null);
    } else {
      const latestRunMsg = [...(currentConv.messages || [])]
        .reverse()
        .find((m) => m.role === 'agent' && m.run_id);
      setActiveRunId(latestRunMsg?.run_id || null);
    }
  }

  // Merge a streamed/persisted artifact into the per-path map (last write wins).
  const mergeArtifact = useCallback((art) => {
    if (!art || !art.path) return;
    setArtifacts((prev) => ({ ...prev, [art.path]: { ...art } }));
  }, []);

  useChatSessionStream({
    continuationMsgIdRef, currentConvId, mergeArtifact, messages, sessionId,
    setActiveRunId, setConversations,
  });

  // The runs' data feeds both the Process panel and the artifacts (file diffs
  // arrive with a run's insights), so either panel being open asks for it.
  const runDataWanted = processOpen || artifactsOpen;
  const { loadProcessData } = useChatProcess({
    activeRunId, continuationMsgIdRef, conversationRunIds, currentConvId, loadedRunIdsRef,
    loading, processInsights, processInsightsRef, processOpen: runDataWanted, setArtifacts,
    setProcessError, setProcessInsights, setProcessLoading, setSessionId, t, viewMode,
  });
  // Back in a conversation whose turn is still running: the panels were reset
  // for it above, and the turn's run and session (said before the switch) are
  // what they follow until it ends.
  // Adjusted while rendering when the conversation changes (and on the first
  // render), reading the turn from `turns` state, which mirrors the turn ref.
  const [turnAdoptedFor, setTurnAdoptedFor] = useState(undefined);
  if (turnAdoptedFor !== (currentConvId ?? null)) {
    setTurnAdoptedFor(currentConvId ?? null);
    const turn = currentConvId ? turns[currentConvId] : null;
    if (turn?.sessionId) setSessionId(turn.sessionId);
    if (turn?.runId) setActiveRunId(turn.runId);
  }

  const { codeRows, codeListLoading, codeListError } = useConversationCode({
    conversationRunIds, currentConvId, codeFocus, t,
  });
  // What the two panel buttons count: files and views the turns produced
  // (code views belong to the Code panel, so they are counted there only).
  const artifactViews = useMemo(() => {
    const codeIds = new Set(codeRows.map((r) => r.view_id));
    return conversationViews(messages).filter((v) => v.view_kind !== 'code' && !codeIds.has(v.view_id));
  }, [messages, codeRows]);
  const artifactCount = Object.keys(artifacts).length + artifactViews.length;
  // The fenced blocks of the replies count as code too, until one is saved as
  // a view (then it is a row). Saved ids are kept for this page visit only.
  const replyBlocks = useMemo(() => replyCodeBlocks(messages), [messages]);
  const [savedReplyIds, setSavedReplyIds] = useState(() => new Set());
  const markReplySaved = useCallback((id) => {
    setSavedReplyIds((prev) => { const next = new Set(prev); next.add(id); return next; });
  }, []);
  const codeCount = codeRows.length + replyBlocks.filter((b) => !savedReplyIds.has(b.id)).length;

  const {
    resizeTextarea, onPickFiles, removeAttachment, toggleAttachmentStore,
    addReferences, removeReference, addWorkspaceFiles,
  } = useChatComposerInput({
    attachMenuOpen, contextKinds, pendingAttachments, selectedWorkspace,
    setAttachmentError, setContextKinds, setPendingAttachments, setPendingReferences,
    textareaRef,
  });

  // ---- new conversation ----
  // Don't create a conversation record yet — just land on the empty start page.
  // sendMessage lazily creates the conversation when the first message is sent,
  // so repeated "New chat" clicks never pile up empty conversations in the list.
  const newConversation = useCallback(() => {
    setCurrentConvId(null);
    setInput('');
    setListOpen(false);
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
    beginTurn, clientId, conversations, currentConvId, currentConvIdRef, endTurn, input,
    isTurnRunning, loadProcessData, mergeArtifact, navigate, noteTurn, pendingAttachments,
    pendingReferences, processOpen: runDataWanted, selectCommand, selectedAgent, selectedFlow,
    selectedProject, selectedTeam, selectedWorkspace, setActiveRunId, setAttachmentError,
    setConversations, setCurrentConvId, setGraphRun, setInput, setPendingAttachments,
    setPendingReferences, setProcessInsights, setSelectedAgent, setSessionId, stream, t,
    targetMode, textareaRef,
  });
  const jumpToArtifact = useCallback((path) => {
    setTimeout(() => {
      const el = document.querySelector(`[data-artifact-path="${CSS.escape(path)}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 50);
  }, []);

  // Stops the open conversation's turn only; the others keep running. A turn
  // stopped before it named its run is stopped when it does (channelStream).
  const stopGeneration = useCallback(() => {
    const turn = getTurn(currentConvId);
    if (!turn) {
      if (liveRunId) stopMessage(liveRunId).catch(() => {});
      return;
    }
    turn.ctrl.abort();
    const runId = turn.runId || activeRunId;
    if (runId) stopMessage(runId).catch(() => {});
    endTurn(currentConvId);
  }, [activeRunId, currentConvId, endTurn, getTurn, liveRunId]);

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

  // A code block in a reply, shown in the Code panel under "From replies".
  // Nothing is stored by opening it: it becomes a code view (versions, runs,
  // a place in a project) only when the person saves it as one there.
  const openInCodePanel = useCallback(async ({ code, runId }) => {
    const block = replyBlocks.find((b) => b.body === code && (!runId || !b.run_id || b.run_id === runId))
      || replyBlocks.find((b) => b.body === code);
    setCodeFocus({ replyId: block?.id || null, nonce: Date.now() });
    setArtifactsOpen(false);
    setCodeOpen(true);
  }, [replyBlocks]);

  // Personal memory is read from the workspace the formula is saved to: the
  // conversation's, which can differ from the one picked in the header.
  const mathWorkspace = currentConv?.workspace || selectedWorkspace || 'default';
  useEffect(() => {
    let alive = true;
    loadWorkspaceSummary(mathWorkspace)
      .then((summary) => { if (alive) setPersonalMemoryOn(summary?.personal_memory_enabled !== false); })
      .catch(() => { if (alive) setPersonalMemoryOn(false); });
    return () => { alive = false; };
  }, [mathWorkspace]);

  // A display formula in a reply, kept in the user's personal memory for this
  // conversation's workspace (memory/personal.py on the backend). Not offered
  // where the workspace has personal memory off.
  const mathActions = useMemo(() => (personalMemoryOn ? {
    save: (tex, title) => addPersonalMemoryNote({
      title, content: `$$\n${tex}\n$$`, workspace: mathWorkspace,
    }),
  } : null), [personalMemoryOn, mathWorkspace]);

  // Messages sent while a turn works (chat/useChatSteering.js). Here and not
  // in the composer, which is not rendered for every conversation.
  const steering = useChatSteering({
    conversations, currentConvId, input, liveRunId, loading, sendMessage, setConversations,
    setInput, stopGeneration, targetMode, turns,
  });

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  // Published once for the five panes below; see `chat/context.js`.
  const page = {
    activeRunId, addReferences, addWorkspaceFiles, agentModel, agentName, agentProvider, agentTopology, agents,
    artifactCount, artifactViews, artifacts, artifactsOpen, setArtifactsOpen, attachMenuOpen, attachmentError,
    codeCount, codeFocus, codeListError, listOpen, setListOpen, codeListLoading, codeOpen, codeRows, commandMenuIndex, commandMenuOpen,
    markReplySaved, replyBlocks, savedReplyIds, setCodeFocus,
    commandSuggestions, composerPlaceholder, contextKinds, contextUsage, conversationRunIds,
    conversations, currentConv, currentConvId, currentTelegramBinding, deleteConversation,
    fileInputRef, flows, graphRun, handleKeyDown, hasTarget, input, jumpToArtifact, liveMessages,
    liveTurn, loadProcessData, loading, messages, navigate, newConversation,
    onPickFiles, openInCodePanel, pendingAttachments, pendingReferences, pickerKind, processError, processInsights,
    processLoading, processOpen, projects, removeAttachment, removeReference, renderedMessages,
    resizeTextarea, runTimelineByRunId, selectCommand, selectableAgents, selectedAgent,
    selectedFlow, selectedProject, selectedTeam, selectedWorkspace, sendAsBot, sendMessage,
    setAttachMenuOpen, setCodeOpen, setCommandMenuIndex, setCommandMenuOpen, setConversations,
    setInput, setPickerKind, setProcessOpen, setSelectedAgent, setSelectedFlow, setSelectedProject,
    setSelectedTeam, setTargetMode, setViewMode, steering, stopGeneration, syncError, t, targetMode,
    teams, telegramError, turns, runningChats, telegramReplyAllowed, telegramSending, textareaRef,
    toggleAttachmentStore, viewMode, visibleConversations, visibleTelegramBindings
  };

  return (
    <ChatPageContext.Provider value={page}>
    <ChatMathActionsContext.Provider value={mathActions}>
    <div className="h-full flex overflow-hidden">
      <ChatSidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <ChatTopBar />
        {/* The composer floats over the foot of the conversation, which
            scrolls behind it (see ChatComposer). */}
        <div className="relative flex-1 min-h-0 flex flex-col">
          <ChatMessageList />
          {/* A conversation with the Assistant is read here and continued there. */}
          {currentConv?.origin !== 'assistant' && <ChatComposer />}
        </div>
      </div>
      <ChatSidePanel />
    </div>
    </ChatMathActionsContext.Provider>
    </ChatPageContext.Provider>
  );
}
