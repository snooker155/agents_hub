import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useWorkspace } from '../components/WorkspaceContext';
import { getAgents, getNodes, getSessionInsights, getWorkspace } from '../api';
import {
  PlusCircle,
  Send,
  StopCircle,
  Bot,
  User,
  Trash2,
  MessageSquare,
  ChevronDown,
  Copy,
  Check,
  AlertCircle,
  ExternalLink,
  FileText,
  Wrench,
  RefreshCw,
  X,
  Paperclip,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Local storage persistence
// ---------------------------------------------------------------------------
const STORAGE_KEY = 'agent_hub_chats_v1';
const MAX_ATTACHMENT_BYTES = 200000;
const MAX_ATTACHMENT_COUNT = 6;

function loadConversations() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveConversations(convs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(convs));
  } catch {}
}

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

function shortText(v, max = 180) {
  const s = String(v || '');
  return s.length > max ? `${s.slice(0, max)}...` : s;
}

function fmtDurationMs(ms) {
  const n = Number(ms || 0);
  if (!n) return '0ms';
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(2)}s`;
}

function TokenPill({ label, value }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-medium">
      {label}: {value ?? 0}
    </span>
  );
}

function ProcessGraph({ messageRuns = [] }) {
  if (!messageRuns.length) {
    return <p className="text-xs text-gray-500 italic">No process graph data yet.</p>;
  }

  const Node = ({ title, children, tone = 'slate' }) => {
    const tones = {
      slate: 'border-gray-200 bg-white',
      message: 'border-indigo-100 bg-indigo-50',
      tool: 'border-amber-200 bg-amber-50',
      output: 'border-emerald-200 bg-emerald-50',
    };
    return (
      <div className={`rounded-lg border p-2.5 ${tones[tone] || tones.slate}`}>
        <div className="text-[11px] font-semibold text-gray-700 mb-1">{title}</div>
        {children}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      {messageRuns.map((mr, idx) => (
        <div key={`${mr.message_id || idx}`} className="relative pl-4">
          {idx < messageRuns.length - 1 && (
            <div className="absolute left-[7px] top-4 bottom-[-16px] w-px bg-gray-200" />
          )}
          <div className="absolute left-0 top-2 w-3 h-3 rounded-full bg-indigo-500" />

          <div className="rounded-lg border border-indigo-100 bg-indigo-50 p-3">
            <div className="flex items-center justify-between gap-2 mb-2">
              <div className="text-xs font-semibold text-indigo-800">
                Message {idx + 1}
              </div>
              <div className="text-[10px] text-indigo-700">{mr.timestamp || ''}</div>
            </div>
            <div className="flex flex-wrap gap-1 mb-2">
              <TokenPill label="in" value={mr.inbound_tokens} />
              <TokenPill label="out" value={mr.outbound_tokens} />
              <TokenPill label="total" value={mr.total_tokens} />
              <TokenPill label="tools" value={mr.tool_calls} />
              <TokenPill label="duration" value={fmtDurationMs(mr.duration_ms)} />
            </div>
          </div>

          <div className="ml-5 mt-2 space-y-2">
            <Node title="Input" tone="slate">
              <div className="text-[11px] text-gray-700 whitespace-pre-wrap">{mr.input || '(empty)'}</div>
            </Node>

            {(mr.tools || []).map((t, tIdx) => (
              <Node key={tIdx} title={`Tool Call ${tIdx + 1}: ${t.tool || 'tool'}`} tone="tool">
                {t.input && <div className="text-[11px] text-gray-700 font-mono">in: {shortText(t.input, 140)}</div>}
                {t.output && <div className="text-[11px] text-emerald-700 font-mono mt-1">out: {shortText(t.output, 140)}</div>}
              </Node>
            ))}

            <Node title="Output" tone="output">
              <div className="text-[11px] text-gray-700 whitespace-pre-wrap">{mr.output || '(streaming/no output)'}</div>
            </Node>
            {(mr.tools || []).length === 0 && (
              <div className="text-[10px] text-gray-400 pl-1">No tool calls in this message.</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Markdown-ish renderer (no external deps)
// ---------------------------------------------------------------------------
function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={copy}
      className="absolute top-2 right-2 p-1 rounded text-gray-400 hover:text-gray-200 transition-colors"
      title="Copy"
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

function renderContent(text) {
  // Split on fenced code blocks
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```')) {
      const inner = part.slice(3, -3);
      const newline = inner.indexOf('\n');
      const lang = newline > 0 ? inner.slice(0, newline).trim() : '';
      const code = newline > 0 ? inner.slice(newline + 1) : inner;
      return (
        <div key={i} className="relative my-3">
          {lang && (
            <div className="bg-gray-800 text-gray-400 text-xs px-4 py-1.5 rounded-t-lg font-mono border-b border-gray-700">
              {lang}
            </div>
          )}
          <pre
            className={`bg-gray-900 text-gray-100 text-xs font-mono p-4 overflow-x-auto ${lang ? 'rounded-b-lg' : 'rounded-lg'} whitespace-pre`}
          >
            <CopyButton text={code} />
            {code}
          </pre>
        </div>
      );
    }

    // Inline code
    const inlineParts = part.split(/(`[^`]+`)/g);
    return (
      <span key={i}>
        {inlineParts.map((ip, j) => {
          if (ip.startsWith('`') && ip.endsWith('`') && ip.length > 2) {
            return (
              <code key={j} className="bg-gray-100 text-indigo-700 px-1 py-0.5 rounded text-xs font-mono">
                {ip.slice(1, -1)}
              </code>
            );
          }
          // Render line breaks
          return ip.split('\n').map((line, k, arr) => (
            <React.Fragment key={`${j}-${k}`}>
              {line}
              {k < arr.length - 1 && <br />}
            </React.Fragment>
          ));
        })}
      </span>
    );
  });
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------
function MessageBubble({ msg }) {
  const isUser = msg.role === 'user';
  return (
    <div className={`flex gap-3 mb-6 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-white
          ${isUser ? 'bg-indigo-600' : 'bg-gray-800'}`}
      >
        {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
      </div>

      {/* Bubble */}
      <div
        className={`max-w-[72%] text-sm leading-relaxed
          ${isUser
            ? 'bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3'
            : 'bg-white border border-gray-200 text-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm'
          }
          ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
      >
        {isUser
          ? <span className="whitespace-pre-wrap">{msg.content}</span>
          : <div>{renderContent(msg.content)}</div>
        }
        {!isUser && msg.run_id && (
          <div className="mt-2 pt-2 border-t border-gray-100">
            <div className="flex items-center gap-1.5 mb-2">
              {typeof msg.inbound_tokens === 'number' && <TokenPill label="in" value={msg.inbound_tokens} />}
              {typeof msg.outbound_tokens === 'number' && <TokenPill label="out" value={msg.outbound_tokens} />}
              {typeof msg.duration_ms === 'number' && <TokenPill label="duration" value={fmtDurationMs(msg.duration_ms)} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Typing indicator
// ---------------------------------------------------------------------------
function TypingIndicator({ agentName }) {
  return (
    <div className="flex gap-3 mb-6">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm shadow-sm px-4 py-3 flex items-center gap-2">
        <span className="text-xs text-gray-400">{agentName} is thinking</span>
        <span className="flex gap-1">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent selector dropdown
// ---------------------------------------------------------------------------
function AgentDropdown({ agents, value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = agents.find((a) => a.id === value);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <Bot className="w-4 h-4 text-indigo-500" />
        <span className="font-medium">{selected?.name || 'Select agent'}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 min-w-[220px] py-1 max-h-64 overflow-y-auto">
          {agents.filter((a) => !a.is_remote).map((a) => (
            <button
              key={a.id}
              onClick={() => { onChange(a.id); setOpen(false); }}
              className={`w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50 transition-colors
                ${a.id === value ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-700'}`}
            >
              <div className="font-medium">{a.name}</div>
              {a.domain && (
                <div className="text-xs text-gray-400 mt-0.5">{a.domain}</div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Chat page
// ---------------------------------------------------------------------------
export default function Chat() {
  const { selectedWorkspace } = useWorkspace();

  const [agents, setAgents] = useState([]);
  const [workspaceAllowedAgentIds, setWorkspaceAllowedAgentIds] = useState(null);
  const [selectedAgent, setSelectedAgent] = useState('');
  const [runningNodes, setRunningNodes] = useState([]);

  const [conversations, setConversations] = useState(() => loadConversations());
  const [currentConvId, setCurrentConvId] = useState(null);

  const [input, setInput] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [attachmentError, setAttachmentError] = useState('');
  const [loading, setLoading] = useState(false);
  const [processOpen, setProcessOpen] = useState(false);
  const [activeRunId, setActiveRunId] = useState(null);
  const [processLoading, setProcessLoading] = useState(false);
  const [processError, setProcessError] = useState('');
  const [processInsights, setProcessInsights] = useState({
    messages: [],
    tools: [],
    thinking: [],
    message_runs: [],
    token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
  });

  const abortCtrlRef = useRef(null);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);

  // ---- derived state ----
  const currentConv = conversations.find((c) => c.id === currentConvId) || null;
  const messages = currentConv?.messages || [];
  const agentName = agents.find((a) => a.id === selectedAgent)?.name || selectedAgent || 'Agent';
  const selectableAgents = useMemo(() => {
    const localAgents = agents.filter((a) => !a.is_remote);
    if (!selectedWorkspace) return localAgents;
    const allowedSet = new Set(workspaceAllowedAgentIds || []);
    return localAgents.filter((a) => allowedSet.has(a.id));
  }, [agents, selectedWorkspace, workspaceAllowedAgentIds]);

  // ---- persist ----
  useEffect(() => { saveConversations(conversations); }, [conversations]);

  // ---- load agents ----
  useEffect(() => {
    getAgents()
      .then((r) => {
        setAgents(r.data || []);
      })
      .catch(() => {});
  }, []);

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
      setSelectedAgent(selectableAgents[0].id);
    }
  }, [selectableAgents, selectedAgent]);

  // ---- check running nodes for selected agent ----
  useEffect(() => {
    if (!selectedAgent) { setRunningNodes([]); return; }
    getNodes()
      .then((r) => {
        const nodes = r.data || [];
        setRunningNodes(nodes.filter(
          (n) => n.agent_id === selectedAgent && (n.status === 'running' || n.status === 'starting')
        ));
      })
      .catch(() => setRunningNodes([]));
  }, [selectedAgent]);

  const noRunningNode = selectedAgent && runningNodes.length === 0;

  // ---- focus textarea on mount and whenever loading ends ----
  useEffect(() => {
    if (!noRunningNode) textareaRef.current?.focus();
  }, []);  // mount only

  useEffect(() => {
    if (!loading && !noRunningNode) {
      // Defer by one tick so React finishes re-enabling the textarea first
      const t = setTimeout(() => textareaRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [loading, noRunningNode]);

  // ---- auto-scroll ----
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

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

  const loadProcessData = useCallback(async (runId) => {
    if (!runId) return;
    setProcessLoading(true);
    setProcessError('');
    try {
      const insightsRes = await getSessionInsights(runId);
      setProcessInsights(
        insightsRes.data || {
          messages: [],
          tools: [],
          thinking: [],
          message_runs: [],
          token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
        },
      );
    } catch (err) {
      setProcessError(err.response?.data?.detail || 'Failed to load process details.');
      setProcessInsights({
        messages: [],
        tools: [],
        thinking: [],
        message_runs: [],
        token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
      });
    } finally {
      setProcessLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!processOpen || !activeRunId || loading) return;
    loadProcessData(activeRunId);
  }, [processOpen, activeRunId, loading, loadProcessData]);

  useEffect(() => {
    if (selectedWorkspace) return;
    setPendingAttachments((prev) => prev.map((a) => ({ ...a, store_to_workspace: false })));
  }, [selectedWorkspace]);

  // ---- textarea auto-resize ----
  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
  }, []);

  const onPickFiles = useCallback(async (e) => {
    const picked = Array.from(e.target.files || []);
    if (!picked.length) return;

    setAttachmentError('');
    const availableSlots = Math.max(0, MAX_ATTACHMENT_COUNT - pendingAttachments.length);
    const toRead = picked.slice(0, availableSlots);
    const nextItems = [];

    for (const file of toRead) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachmentError(`"${file.name}" exceeds ${MAX_ATTACHMENT_BYTES} bytes.`);
        continue;
      }
      try {
        const content = await file.text();
        nextItems.push({
          id: genId(),
          filename: file.name || `attachment_${Date.now()}.txt`,
          content,
          size: file.size,
          store_to_workspace: false,
        });
      } catch {
        setAttachmentError(`Failed to read "${file.name}".`);
      }
    }

    if (picked.length > availableSlots) {
      setAttachmentError(`Only ${MAX_ATTACHMENT_COUNT} attachments are allowed per message.`);
    }

    if (nextItems.length) {
      setPendingAttachments((prev) => [...prev, ...nextItems]);
    }
    e.target.value = '';
  }, [pendingAttachments.length]);

  const removeAttachment = useCallback((id) => {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const toggleAttachmentStore = useCallback((id, checked) => {
    setPendingAttachments((prev) =>
      prev.map((a) => (a.id === id ? { ...a, store_to_workspace: checked } : a)),
    );
  }, []);

  // ---- new conversation ----
  const newConversation = useCallback(() => {
    const id = genId();
    const conv = {
      id,
      title: 'New conversation',
      agent_id: selectedAgent,
      workspace: selectedWorkspace,
      messages: [],
      created_at: new Date().toISOString(),
    };
    setConversations((prev) => [conv, ...prev]);
    setCurrentConvId(id);
    // Ensure the input is focused after switching to the new conversation.
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [selectedAgent, selectedWorkspace]);

  // ---- delete conversation ----
  const deleteConversation = useCallback((id, e) => {
    e.stopPropagation();
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (currentConvId === id) setCurrentConvId(null);
  }, [currentConvId]);

  // ---- send message ----
  const sendMessage = useCallback(async () => {
    const text = input.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if ((!text && !hasAttachments) || loading || !selectedAgent) return;

    const attachmentLine = hasAttachments
      ? `Attached files: ${pendingAttachments.map((a) => a.filename).join(', ')}`
      : '';
    const userMsgText = [text, attachmentLine].filter(Boolean).join('\n');
    const userMsg = { id: genId(), role: 'user', content: userMsgText };

    // Ensure there is an active conversation
    let convId = currentConvId;
    let historySnapshot = messages.map((m) => ({ role: m.role, content: m.content }));

    if (!convId) {
      convId = genId();
      const basis = text || attachmentLine || 'New chat';
      const title = basis.length > 50 ? basis.slice(0, 50) + '…' : basis;
      const newConv = {
        id: convId,
        title,
        agent_id: selectedAgent,
        workspace: selectedWorkspace,
        messages: [],
        created_at: new Date().toISOString(),
      };
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConvId(convId);
      historySnapshot = [];
    }

    // Append user message
    setConversations((prev) =>
      prev.map((c) =>
        c.id === convId
          ? {
              ...c,
              messages: [...c.messages, userMsg],
              title: c.messages.length === 0
                ? ((text || attachmentLine).length > 50 ? (text || attachmentLine).slice(0, 50) + '…' : (text || attachmentLine))
                : c.title,
            }
          : c,
      ),
    );

    setInput('');
    setPendingAttachments([]);
    setAttachmentError('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setLoading(true);

    const ctrl = new AbortController();
    abortCtrlRef.current = ctrl;

    const convTitle = conversations.find((c) => c.id === convId)?.title || (text || attachmentLine).slice(0, 60);

    try {
      const assistantId = genId();
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId
            ? {
                ...c,
                messages: [
                  ...c.messages,
                  {
                    id: assistantId,
                    role: 'agent',
                    content: '',
                    error: false,
                    run_id: null,
                    inbound_tokens: null,
                    outbound_tokens: null,
                    total_tokens: null,
                    tool_calls: null,
                    duration_ms: null,
                  },
                ],
              }
            : c,
        ),
      );

      const response = await fetch('http://localhost:8000/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          agent_id: selectedAgent,
          message: text,
          workspace: selectedWorkspace || null,
          history: historySnapshot,
          conversation_id: convId,
          conversation_title: convTitle,
          attachments: pendingAttachments.map((a) => ({
            filename: a.filename,
            content: a.content,
            store_to_workspace: Boolean(a.store_to_workspace && selectedWorkspace),
          })),
        }),
      });
      if (!response.ok || !response.body) {
        const detail = await response.text();
        throw new Error(detail || 'Failed to open chat stream');
      }

      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let buffer = '';
      let finalPayload = null;
      let runId = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';

        for (const chunk of chunks) {
          const line = chunk
            .split('\n')
            .map((l) => l.trim())
            .find((l) => l.startsWith('data: '));
          if (!line) continue;

          let event = null;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }
          if (!event || !event.type) continue;

          if (event.type === 'meta' && event.run_id) {
            runId = event.run_id;
            setActiveRunId(runId);
            if (processOpen) {
              setProcessInsights({
                messages: [...historySnapshot, { role: 'user', content: userMsgText }],
                tools: [],
                thinking: ['Streaming started for this agent call.'],
                message_runs: [
                  {
                    message_id: runId,
                    timestamp: new Date().toISOString(),
                    input: userMsgText,
                    output: '',
                    tools: [],
                    thinking: ['Streaming started for this agent call.'],
                    inbound_tokens: 0,
                    outbound_tokens: 0,
                    total_tokens: 0,
                    tool_calls: 0,
                    duration_ms: 0,
                  },
                ],
                token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
              });
            }
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) => (m.id === assistantId ? { ...m, run_id: runId } : m)),
                },
              ),
            );
          } else if (event.type === 'thinking' && processOpen) {
            setProcessInsights((prev) => ({
              ...prev,
              thinking: [...(prev.thinking || []), event.message || 'Model step'],
              message_runs: (prev.message_runs || []).map((mr, idx) =>
                idx === (prev.message_runs || []).length - 1
                  ? { ...mr, thinking: [...(mr.thinking || []), event.message || 'Model step'] }
                  : mr
              ),
            }));
          } else if (event.type === 'tool_start' && processOpen) {
            setProcessInsights((prev) => ({
              ...prev,
              tools: [
                ...(prev.tools || []),
                {
                  step: event.step,
                  tool: event.tool,
                  input: event.input,
                  output: null,
                  running: true,
                },
              ],
              message_runs: (prev.message_runs || []).map((mr, idx) =>
                idx === (prev.message_runs || []).length - 1
                  ? {
                      ...mr,
                      tools: [
                        ...(mr.tools || []),
                        {
                          step: event.step,
                          tool: event.tool,
                          input: event.input,
                          output: null,
                          running: true,
                        },
                      ],
                      tool_calls: ((mr.tool_calls || 0) + 1),
                    }
                  : mr
              ),
            }));
          } else if (event.type === 'tool_end' && processOpen) {
            setProcessInsights((prev) => {
              const tools = [...(prev.tools || [])];
              for (let i = tools.length - 1; i >= 0; i -= 1) {
                if (tools[i].running) {
                  tools[i] = { ...tools[i], output: event.output, running: false };
                  break;
                }
              }
              const message_runs = (prev.message_runs || []).map((mr, idx) => {
                if (idx !== (prev.message_runs || []).length - 1) return mr;
                const mrTools = [...(mr.tools || [])];
                for (let i = mrTools.length - 1; i >= 0; i -= 1) {
                  if (mrTools[i].running) {
                    mrTools[i] = { ...mrTools[i], output: event.output, running: false };
                    break;
                  }
                }
                return { ...mr, tools: mrTools };
              });
              return { ...prev, tools, message_runs };
            });
          } else if (event.type === 'token') {
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId ? { ...m, content: `${m.content || ''}${event.token || ''}` } : m
                  ),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? { ...mr, output: `${mr.output || ''}${event.token || ''}` }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'done') {
            finalPayload = event;
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId
                      ? {
                          ...m,
                          content: (m.content || event.response || '').trim() || event.response || '',
                          error: !event.ok,
                          run_id: runId || event.run_id || null,
                          inbound_tokens: event.usage?.inbound_tokens ?? m.inbound_tokens ?? null,
                          outbound_tokens: event.usage?.outbound_tokens ?? m.outbound_tokens ?? null,
                          total_tokens: event.usage?.total_tokens ?? m.total_tokens ?? null,
                          tool_calls: event.tool_calls ?? m.tool_calls ?? null,
                          duration_ms: event.duration_ms ?? m.duration_ms ?? null,
                        }
                      : m
                  ),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => {
                const inTok = event.usage?.inbound_tokens || 0;
                const outTok = event.usage?.outbound_tokens || 0;
                const totTok = event.usage?.total_tokens || (inTok + outTok);
                return {
                  ...prev,
                  token_usage: {
                    inbound_tokens: (prev.token_usage?.inbound_tokens || 0) + inTok,
                    outbound_tokens: (prev.token_usage?.outbound_tokens || 0) + outTok,
                    total_tokens: (prev.token_usage?.total_tokens || 0) + totTok,
                  },
                  message_runs: (prev.message_runs || []).map((mr, idx) =>
                    idx === (prev.message_runs || []).length - 1
                      ? {
                          ...mr,
                          output: event.response || mr.output || '',
                          inbound_tokens: inTok,
                          outbound_tokens: outTok,
                          total_tokens: totTok,
                          tool_calls: event.tool_calls ?? mr.tool_calls ?? 0,
                          duration_ms: event.duration_ms ?? mr.duration_ms ?? 0,
                        }
                      : mr
                  ),
                };
              });
            }
          }
        }
      }

      if (!finalPayload) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id !== convId ? c : {
              ...c,
              messages: c.messages.map((m) =>
                m.id === assistantId && !m.content
                  ? { ...m, content: 'No streamed output received.', error: true }
                  : m
              ),
            },
          ),
        );
      } else if ((runId || finalPayload.run_id) && processOpen) {
        loadProcessData(runId || finalPayload.run_id);
      }
    } catch (err) {
      if (err?.name === 'AbortError') return;

      const errMsg = {
        id: genId(),
        role: 'agent',
        content: err.response?.data?.detail || err.message || 'Failed to get a response.',
        error: true,
        run_id: null,
      };
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId ? { ...c, messages: [...c.messages, errMsg] } : c,
        ),
      );
    } finally {
      setLoading(false);
      abortCtrlRef.current = null;
    }
  }, [input, loading, selectedAgent, currentConvId, messages, selectedWorkspace, conversations, processOpen, loadProcessData, pendingAttachments]);

  const stopGeneration = () => {
    abortCtrlRef.current?.abort();
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    // -m-8 negates the Layout's p-8; height fills viewport minus 4rem header
    <div className="-m-8 flex overflow-hidden" style={{ height: 'calc(100vh - 4rem)' }}>

      {/* ── Sidebar ── */}
      <div className="w-60 flex-shrink-0 bg-gray-50 border-l border-r border-gray-200 flex flex-col">
        <div className="p-3">
          <button
            onClick={newConversation}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
          >
            <PlusCircle className="w-4 h-4" />
            New chat
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          {conversations.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-10 px-3 leading-relaxed">
              No conversations yet.
              <br />
              Click <strong>New chat</strong> to start.
            </p>
          )}
          {conversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => {
                setCurrentConvId(conv.id);
                if (conv.agent_id && selectableAgents.some((a) => a.id === conv.agent_id)) {
                  setSelectedAgent(conv.agent_id);
                }
              }}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs group flex items-start gap-2 transition-colors
                ${currentConvId === conv.id
                  ? 'bg-indigo-50 text-indigo-700'
                  : 'text-gray-600 hover:bg-white hover:shadow-sm'
                }`}
            >
              <MessageSquare className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 opacity-60" />
              <span className="flex-1 truncate leading-5">{conv.title}</span>
              <button
                onClick={(e) => deleteConversation(conv.id, e)}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-red-500 flex-shrink-0 transition-opacity"
                title="Delete"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </button>
          ))}
        </div>
      </div>

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col min-w-0 bg-gray-50">

        {/* Top bar */}
        <div className="flex-shrink-0 bg-white border-b border-gray-200 px-5 h-[60px] flex items-center gap-4">
          <AgentDropdown agents={selectableAgents} value={selectedAgent} onChange={(id) => {
            setSelectedAgent(id);
            // update current conv's agent
            if (currentConvId) {
              setConversations((prev) =>
                prev.map((c) => c.id === currentConvId ? { ...c, agent_id: id } : c),
              );
            }
          }} />

          {selectedWorkspace && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">WS</span>
              <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                {selectedWorkspace}
              </span>
            </div>
          )}

          {selectedWorkspace && selectableAgents.length === 0 && (
            <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-1.5 text-xs font-medium">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
              No authorized agents in this workspace.
            </div>
          )}

          {noRunningNode && (
            <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 text-amber-700 rounded-lg px-3 py-1.5 text-xs font-medium">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
              No running node for this agent —{' '}
              <a href="/nodes" className="underline hover:text-amber-900 flex items-center gap-0.5">
                start one <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          )}

          <button
            onClick={() => setProcessOpen((v) => !v)}
            className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
          >
            {processOpen ? (
              <>
                <X className="w-3.5 h-3.5" />
                Hide process
              </>
            ) : (
              <>
                <FileText className="w-3.5 h-3.5" />
                Show process
              </>
            )}
          </button>

          <div className="text-xs text-gray-400">
            {messages.length > 0 && `${messages.length} message${messages.length !== 1 ? 's' : ''}`}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto px-6 py-8">
            {messages.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center h-full min-h-[40vh] text-center">
                <div className="w-16 h-16 bg-indigo-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                  <Bot className="w-8 h-8 text-indigo-600" />
                </div>
                <h2 className="text-xl font-semibold text-gray-800 mb-2">
                  {agentName}
                </h2>
                <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                  Send a message to start a conversation.
                  {selectedWorkspace && ` The agent will work in the <strong>${selectedWorkspace}</strong> workspace.`}
                </p>
              </div>
            )}

            {messages.map((msg) => (
              <MessageBubble key={msg.id} msg={msg} />
            ))}

            {loading && <TypingIndicator agentName={agentName} />}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input area */}
        <div className="flex-shrink-0 bg-white border-t border-gray-200 px-5 py-4">
          <div className="max-w-3xl mx-auto">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={onPickFiles}
            />
            {pendingAttachments.length > 0 && (
              <div className="mb-2 space-y-2">
                {pendingAttachments.map((att) => (
                  <div key={att.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-gray-200 bg-gray-50">
                    <div className="min-w-0">
                      <div className="text-xs text-gray-700 font-medium truncate">{att.filename}</div>
                      <div className="text-[11px] text-gray-500">{att.size} bytes</div>
                    </div>
                    <div className="flex items-center gap-3">
                      <label className={`flex items-center gap-1.5 text-xs ${selectedWorkspace ? 'text-gray-600' : 'text-gray-400'}`}>
                        <input
                          type="checkbox"
                          checked={Boolean(att.store_to_workspace)}
                          onChange={(e) => toggleAttachmentStore(att.id, e.target.checked)}
                          disabled={!selectedWorkspace || loading}
                        />
                        Store in workspace
                      </label>
                      <button
                        type="button"
                        onClick={() => removeAttachment(att.id)}
                        className="text-gray-400 hover:text-red-600"
                        title="Remove attachment"
                        disabled={loading}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
                {!selectedWorkspace && (
                  <p className="text-[11px] text-amber-600">Select a workspace if you want to store attachments there.</p>
                )}
              </div>
            )}

            <div
              className="flex items-center gap-3 bg-white border border-gray-300 rounded-2xl px-4 py-3
                focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100
                shadow-sm transition-all"
            >
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors disabled:opacity-40"
                title="Attach files"
                disabled={loading || !selectedAgent}
              >
                <Paperclip className="w-4 h-4" />
              </button>

              <textarea
                ref={textareaRef}
                className="flex-1 resize-none text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent leading-relaxed disabled:opacity-50"
                placeholder={!selectedAgent
                  ? 'No authorized agent available in this workspace…'
                  : (noRunningNode ? 'Start a node to enable chat…' : `Message ${agentName}…`)}
                rows={1}
                value={input}
                disabled={loading || !!noRunningNode || !selectedAgent}
                onChange={(e) => { setInput(e.target.value); resizeTextarea(); }}
                onKeyDown={handleKeyDown}
              />

              {loading ? (
                <button
                  onClick={stopGeneration}
                  title="Stop"
                  className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-100 text-red-600 hover:bg-red-200 transition-colors"
                >
                  <StopCircle className="w-4 h-4" />
                </button>
              ) : (
                <button
                  onClick={sendMessage}
                  disabled={(!input.trim() && pendingAttachments.length === 0) || !selectedAgent || !!noRunningNode}
                  title="Send (Enter)"
                  className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full
                    bg-indigo-600 text-white hover:bg-indigo-700
                    disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {attachmentError && (
              <p className="text-xs text-red-600 mt-2">{attachmentError}</p>
            )}
            <p className="text-center text-xs text-gray-400 mt-2">
              Enter to send · Shift+Enter for new line
            </p>
          </div>
        </div>
      </div>

      {/* ── Process side panel ── */}
      {processOpen && (
        <div className="w-[420px] flex-shrink-0 bg-white border-l border-gray-200 flex flex-col">
          <div className="px-4 h-[60px] border-b border-gray-200 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-gray-800">Agent Process</h3>
              <p className="text-[11px] text-gray-500 font-mono mt-0.5">
                {activeRunId || 'No run selected'}
              </p>
            </div>
            <div className="flex items-center gap-1">
              {activeRunId && (
                <button
                  onClick={() => loadProcessData(activeRunId)}
                  className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded"
                  title="Refresh process"
                >
                  <RefreshCw className="w-4 h-4" />
                </button>
              )}
              <button
                onClick={() => setProcessOpen(false)}
                className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
                title="Close panel"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {!activeRunId ? (
            <div className="p-4 text-sm text-gray-500">
              Send a message, then open process for that agent call.
            </div>
          ) : processLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 p-4">
              <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />
              Loading process details...
            </div>
          ) : processError ? (
            <div className="p-4 text-sm text-red-600">{processError}</div>
          ) : (
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <div className="bg-white border border-gray-200 rounded-lg p-3">
                <div className="text-sm font-semibold text-gray-800 mb-2">Graph View</div>
                <div className="flex flex-wrap gap-1 mb-3">
                  <TokenPill label="session in" value={processInsights?.token_usage?.inbound_tokens || 0} />
                  <TokenPill label="session out" value={processInsights?.token_usage?.outbound_tokens || 0} />
                  <TokenPill label="session total" value={processInsights?.token_usage?.total_tokens || 0} />
                </div>
                <ProcessGraph messageRuns={processInsights.message_runs || []} />
              </div>

              <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-2">
                  <Bot className="w-4 h-4 text-indigo-500" />
                  Thinking Process
                </div>
                {(processInsights.thinking || []).length === 0 ? (
                  <p className="text-xs text-gray-500 italic">No thinking trace available.</p>
                ) : (
                  <div className="space-y-2">
                    {(processInsights.thinking || []).map((line, idx) => (
                      <div key={idx} className="text-xs text-gray-700 bg-white border border-gray-100 rounded p-2">
                        {line}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-2">
                  <Wrench className="w-4 h-4 text-indigo-500" />
                  Tool Calls
                </div>
                {(processInsights.tools || []).length === 0 ? (
                  <p className="text-xs text-gray-500 italic">No tool calls captured.</p>
                ) : (
                  <div className="space-y-2">
                    {(processInsights.tools || []).map((t, idx) => (
                      <div key={idx} className="text-xs text-gray-700 bg-white border border-gray-100 rounded p-2">
                        <div className="font-medium text-gray-800">Step {t.step || idx + 1}: {t.tool || 'tool'}</div>
                        {t.input && <div className="font-mono text-[11px] text-gray-600 mt-1">in: {shortText(t.input, 130)}</div>}
                        {t.output && <div className="font-mono text-[11px] text-emerald-700 mt-1">out: {shortText(t.output, 120)}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-2">
                  <MessageSquare className="w-4 h-4 text-indigo-500" />
                  Agent Call Messages
                </div>
                {(processInsights.messages || []).length === 0 ? (
                  <p className="text-xs text-gray-500 italic">No message history captured for this run.</p>
                ) : (
                  <div className="space-y-2">
                    {(processInsights.messages || []).map((m, idx) => (
                      <div key={idx} className="text-xs text-gray-700 bg-white border border-gray-100 rounded p-2">
                        <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">{m.role}</div>
                        <div className="whitespace-pre-wrap">{m.content}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

            </div>
          )}
        </div>
      )}
    </div>
  );
}
