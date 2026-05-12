import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import { getAgents, getMessageInsights, getWorkspace, getProjects, getAgentDefinition, stopMessage } from '../api';
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
} from 'lucide-react';

const SKILL_TOOL = 'get_skill';

// ---------------------------------------------------------------------------
// Local storage persistence
// ---------------------------------------------------------------------------
const STORAGE_KEY = 'agent_hub_chats_v1';
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024; // 5 MB
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

// ---------------------------------------------------------------------------
// Slash commands
// ---------------------------------------------------------------------------
const GLOBAL_COMMANDS = [
  { name: '/help', description: 'Show available commands', template: '/help' },
  { name: '/clear', description: 'Clear the current conversation', template: '/clear' },
  { name: '/new', description: 'Start a new conversation', template: '/new' },
  { name: '/config', description: 'Show configuration for the current agent', template: '/config' },
];

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
  const [expandedNodes, setExpandedNodes] = useState(() => new Set(messageRuns.map((_, idx) => idx)));
  const lastNodeRef = useRef(null);
  const prevLengthRef = useRef(null);

  const toggleNode = useCallback((idx) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    setExpandedNodes(new Set(messageRuns.map((_, idx) => idx)));
  }, [messageRuns]);

  const collapseAll = useCallback(() => {
    setExpandedNodes(new Set());
  }, []);

  useEffect(() => {
    if (!messageRuns.length) return;
    const isFirst = prevLengthRef.current === null;
    prevLengthRef.current = messageRuns.length;
    lastNodeRef.current?.scrollIntoView({ behavior: isFirst ? 'instant' : 'smooth', block: 'nearest' });
  }, [messageRuns.length]);

  if (!messageRuns.length) {
    return <p className="text-xs text-gray-500 italic">No process message data yet.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 pb-1">
        <button
          onClick={expandAll}
          className="text-[10px] text-indigo-600 hover:underline"
        >
          Expand all
        </button>
        <span className="text-gray-300 text-[10px]">·</span>
        <button
          onClick={collapseAll}
          className="text-[10px] text-gray-400 hover:underline"
        >
          Collapse all
        </button>
      </div>
      {messageRuns.map((mr, idx) => {
        const key = mr.message_id || idx;
        const isExpanded = expandedNodes.has(idx);
        const hasNext = idx < messageRuns.length - 1;
        const tools = mr.tools || [];
        const toolCount = Number(mr.tool_calls || tools.length || 0);
        const toolNames = Array.from(
          new Set(
            tools
              .map((t) => String(t?.tool || '').trim())
              .filter(Boolean)
          )
        );
        return (
          <div
            key={key}
            ref={idx === messageRuns.length - 1 ? lastNodeRef : null}
            className="relative pl-4"
          >
            {hasNext && <div className="absolute left-[7px] top-4 bottom-[-16px] w-px bg-gray-200" />}
            <div className="absolute left-0 top-2 w-3 h-3 rounded-full bg-indigo-500" />
            <button
              onClick={() => toggleNode(idx)}
              className="w-full text-left rounded-lg border border-indigo-100 bg-indigo-50 hover:bg-indigo-100 transition-colors p-3"
            >
              <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <div className="text-xs font-semibold text-indigo-800 shrink-0">#{idx + 1}</div>
                  <div className="font-medium text-sm text-indigo-900 truncate">
                    {shortText(mr.input || 'Message', 100)}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className="text-[10px] text-indigo-600">{mr.timestamp || ''}</span>
                  {isExpanded ? (
                    <ChevronUp className="w-3.5 h-3.5 text-indigo-500" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5 text-indigo-500" />
                  )}
                </div>
              </div>
              <div
                className="text-xs text-indigo-800 mb-2"
                style={{
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {mr.agent_id && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 text-[10px] font-medium mr-1">
                    {mr.agent_id}
                  </span>
                )}
                <span>{mr.output || '(no response yet)'}</span>
              </div>
              <div className="flex items-center gap-1.5 mb-2 min-h-[18px] flex-wrap">
                {(() => {
                  const skillNames = toolNames.filter((n) => n === SKILL_TOOL);
                  const regularNames = toolNames.filter((n) => n !== SKILL_TOOL);
                  return (
                    <>
                      {skillNames.length > 0 && (
                        <>
                          <span className="text-[10px] text-violet-600 font-medium">skill:</span>
                          {skillNames.map((name) => (
                            <span key={name} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-100 text-violet-700 border border-violet-200">
                              <Zap className="w-2.5 h-2.5" />{name}
                            </span>
                          ))}
                          {regularNames.length > 0 && <span className="text-gray-200 text-[10px]">|</span>}
                        </>
                      )}
                      {regularNames.length > 0 ? (
                        <>
                          <span className="text-[10px] text-indigo-700 font-medium">tools:</span>
                          {regularNames.slice(0, 3).map((name) => (
                            <span key={name} className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700">
                              {name}
                            </span>
                          ))}
                          {regularNames.length > 3 && (
                            <span className="text-[10px] text-amber-700">+{regularNames.length - 3}</span>
                          )}
                        </>
                      ) : skillNames.length === 0 ? (
                        <span className="text-[10px] text-gray-400">no tools</span>
                      ) : null}
                    </>
                  );
                })()}
              </div>
              <div className="flex flex-wrap gap-1">
                <TokenPill label="in" value={mr.inbound_tokens} />
                <TokenPill label="out" value={mr.outbound_tokens} />
                <TokenPill label="total" value={mr.total_tokens} />
                <TokenPill label="tools" value={toolCount} />
                <TokenPill label="duration" value={fmtDurationMs(mr.duration_ms)} />
              </div>
            </button>
            {isExpanded && (
              <div className="ml-5 mt-2 space-y-2">
                <div className="rounded-lg border border-gray-200 bg-white p-2.5">
                  <div className="text-[11px] font-semibold text-gray-700 mb-1.5">Input</div>
                  <div className="text-[11px] text-gray-700 whitespace-pre-wrap">
                    {shortText(mr.input || '(empty)', 500)}
                  </div>
                </div>
                {(mr.tools || []).map((t, tIdx) => {
                  if (t.tool === SKILL_TOOL) {
                    return (
                      <div key={tIdx} className="rounded-lg border border-violet-300 bg-violet-50 p-2.5">
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <Zap className="w-3.5 h-3.5 text-violet-500 shrink-0" />
                          <span className="text-[11px] font-bold text-violet-800 uppercase tracking-wide">Skill Retrieved</span>
                        </div>
                        {t.input && (
                          <div className="text-[11px] text-violet-700 whitespace-pre-wrap break-all">
                            <span className="text-violet-400">id:</span> {shortText(t.input, 320)}
                          </div>
                        )}
                        {t.output && (
                          <div className="text-[11px] text-violet-900 mt-1 whitespace-pre-wrap break-all">
                            {shortText(t.output, 320)}
                          </div>
                        )}
                      </div>
                    );
                  }
                  return (
                    <div key={tIdx} className="rounded-lg border border-amber-200 bg-amber-50 p-2.5">
                      <div className="text-[11px] font-semibold text-gray-700 mb-1.5">
                        Tool: {t.tool || 'tool'}
                      </div>
                      {t.input && (
                        <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all">
                          <span className="text-gray-500">in:</span> {shortText(t.input, 320)}
                        </div>
                      )}
                      {t.output && (
                        <div className="text-[11px] text-emerald-700 mt-1 whitespace-pre-wrap break-all">
                          <span className="text-emerald-600">out:</span> {shortText(t.output, 320)}
                        </div>
                      )}
                    </div>
                  );
                })}
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2.5">
                  <div className="text-[11px] font-semibold text-gray-700 mb-1.5">Output</div>
                  <div className="text-[11px] text-gray-700 whitespace-pre-wrap">
                    {shortText(mr.output || '(empty)', 600)}
                  </div>
                </div>
                {mr.run_id && (
                  <a
                    href={`/messages/${mr.run_id}`}
                    className="inline-flex items-center gap-1 px-3 py-1.5 text-xs text-indigo-600 border border-indigo-200 rounded-lg hover:bg-indigo-50"
                  >
                    View Full Details
                  </a>
                )}
              </div>
            )}
          </div>
        );
      })}
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

// Renders plain text segments with inline code and markdown table support
function renderTableAwareText(text, keyPrefix = '') {
  const lines = text.split('\n');
  const elements = [];
  let i = 0;
  let plainBuf = [];
  let elemIdx = 0;

  const renderInlineText = (str, key) => {
    const inlineParts = str.split(/(`[^`]+`)/g);
    return (
      <span key={key}>
        {inlineParts.map((ip, j) => {
          if (ip.startsWith('`') && ip.endsWith('`') && ip.length > 2) {
            return (
              <code key={j} className="bg-gray-100 text-indigo-700 px-1 py-0.5 rounded text-xs">
                {ip.slice(1, -1)}
              </code>
            );
          }
          return ip.split('\n').map((line, k, arr) => (
            <React.Fragment key={`${j}-${k}`}>
              {line}
              {k < arr.length - 1 && <br />}
            </React.Fragment>
          ));
        })}
      </span>
    );
  };

  const flushPlain = () => {
    if (!plainBuf.length) return;
    const content = plainBuf.join('\n');
    plainBuf = [];
    elements.push(renderInlineText(content, `${keyPrefix}p${elemIdx++}`));
  };

  while (i < lines.length) {
    const trimmed = lines[i].trim();
    const nextTrimmed = i + 1 < lines.length ? lines[i + 1].trim() : '';

    if (
      trimmed.startsWith('|') &&
      trimmed.endsWith('|') &&
      nextTrimmed.startsWith('|') &&
      /^\|[\s\-:|]+\|$/.test(nextTrimmed)
    ) {
      flushPlain();
      const headers = trimmed.split('|').slice(1, -1).map((h) => h.trim());
      i += 2; // skip header and separator rows
      const rows = [];
      while (i < lines.length) {
        const rowLine = lines[i].trim();
        if (rowLine.startsWith('|') && rowLine.endsWith('|')) {
          rows.push(rowLine.split('|').slice(1, -1).map((c) => c.trim()));
          i++;
        } else {
          break;
        }
      }
      elements.push(
        <div key={`${keyPrefix}t${elemIdx++}`} className="my-3 overflow-x-auto">
          <table className="min-w-full text-xs border border-gray-200 rounded-lg overflow-hidden">
            <thead className="bg-gray-50">
              <tr>
                {headers.map((h, j) => (
                  <th key={j} className="px-3 py-2 text-left font-semibold text-gray-700 border-b border-gray-200">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, j) => (
                <tr key={j} className={j % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {row.map((cell, k) => (
                    <td key={k} className="px-3 py-2 text-gray-700 border-b border-gray-100">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    } else {
      plainBuf.push(lines[i]);
      i++;
    }
  }
  flushPlain();
  return elements;
}

function renderContent(text) {
  // Split on fenced code blocks first
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
            <div className="bg-gray-800 text-gray-400 text-xs px-4 py-1.5 rounded-t-lg border-b border-gray-700">
              {lang}
            </div>
          )}
          <pre
            className={`bg-gray-900 text-gray-100 text-xs p-4 overflow-x-auto ${lang ? 'rounded-b-lg' : 'rounded-lg'} whitespace-pre`}
          >
            <CopyButton text={code} />
            {code}
          </pre>
        </div>
      );
    }
    // For non-code parts: handle tables and inline code
    return <React.Fragment key={i}>{renderTableAwareText(part, `${i}-`)}</React.Fragment>;
  });
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------
function MessageBubble({ msg, isStreaming = false, agentName }) {
  const isUser = msg.role === 'user';
  const showTypingDots = !isUser && isStreaming && !msg.content;
  const activeRunningTool = !isUser && isStreaming && msg.running_tool;
  return (
    <div className={`flex gap-3 mb-6 mx-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center text-white
            ${isUser ? 'bg-indigo-600' : 'bg-gray-800'}`}
        >
          {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
        </div>
        {!isUser && agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">
            {agentName}
          </span>
        )}
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
        {showTypingDots ? (
          activeRunningTool ? (
            <span className="flex items-center gap-2">
              <Terminal className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
              <span className="text-xs text-indigo-500 font-mono truncate max-w-[260px]">{msg.running_tool}</span>
              <span className="flex gap-1 flex-shrink-0">
                {[0, 150, 300].map((delay) => (
                  <span
                    key={delay}
                    className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </span>
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <span className="text-xs text-gray-400">thinking</span>
              <span className="flex gap-1">
                {[0, 150, 300].map((delay) => (
                  <span
                    key={delay}
                    className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </span>
            </span>
          )
        ) : isUser
          ? <span className="whitespace-pre-wrap">{msg.content}</span>
          : <div>{renderContent(msg.content)}</div>
        }
        {/* Tool indicator shown below streamed content when a tool is running mid-response */}
        {!isUser && activeRunningTool && msg.content && (
          <div className="mt-2 pt-2 border-t border-gray-100 flex items-center gap-2">
            <Terminal className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" />
            <span className="text-xs text-indigo-500 font-mono truncate max-w-[260px]">{msg.running_tool}</span>
            <span className="flex gap-1 flex-shrink-0">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          </div>
        )}
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
          {agents.map((a) => (
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

function ProcessPanelContent({ processInsights }) {
  const graphKey = (processInsights?.message_runs || [])
    .map((mr, idx) => `${mr?.run_id || mr?.message_id || idx}`)
    .join('|');
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex flex-wrap gap-1">
        <TokenPill label="session in" value={processInsights?.token_usage?.inbound_tokens || 0} />
        <TokenPill label="session out" value={processInsights?.token_usage?.outbound_tokens || 0} />
        <TokenPill label="session total" value={processInsights?.token_usage?.total_tokens || 0} />
      </div>
      <ProcessGraph key={graphKey} messageRuns={processInsights.message_runs || []} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Chat page
// ---------------------------------------------------------------------------
export default function Chat() {
  const { convId: urlConvId } = useParams();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  const [agents, setAgents] = useState([]);
  const [workspaceAllowedAgentIds, setWorkspaceAllowedAgentIds] = useState(null);
  const [selectedAgent, setSelectedAgent] = useState('');

  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');

  const [conversations, setConversations] = useState(() => loadConversations());
  const [currentConvId, setCurrentConvId] = useState(urlConvId || null);

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

  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandMenuIndex, setCommandMenuIndex] = useState(0);

  // session_id from the backend — used to subscribe to continuation SSE
  const [sessionId, setSessionId] = useState(null);

  const abortCtrlRef = useRef(null);
  const sessionSseRef = useRef(null);   // EventSource for session continuation stream
  const messagesEndRef = useRef(null);
  const prevConvIdRef = useRef(undefined);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const loadedRunIdsRef = useRef(new Set());   // tracks which run_ids have been fetched
  const processInsightsRef = useRef(processInsights); // used inside loadProcessData to check if silent

  // ---- derived state ----
  const visibleConversations = useMemo(() => {
    if (!selectedWorkspace || selectedWorkspace === 'default') return conversations;
    return conversations.filter((c) => c.workspace === selectedWorkspace);
  }, [conversations, selectedWorkspace]);

  const currentConv = conversations.find((c) => c.id === currentConvId) || null;
  const messages = currentConv?.messages || [];
  const conversationRunIds = useMemo(() => {
    const ids = new Set();
    for (const m of (currentConv?.messages || [])) {
      if (m?.role === 'agent' && m?.run_id) ids.add(String(m.run_id));
    }
    return ids;
  }, [currentConv]);
  const agentName = agents.find((a) => a.id === selectedAgent)?.name || selectedAgent || 'Agent';
  const _agentObj = agents.find((a) => a.id === selectedAgent) || {};
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

  // ---- persist ----
  useEffect(() => { saveConversations(conversations); }, [conversations]);

  // ---- load agents ----
  useEffect(() => {
    getAgents()
      .then((r) => { setAgents(r.data || []); })
      .catch(() => {});
  }, []);


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

  // ---- session SSE subscription for continuation runs ----
  // When we have a session_id, open a persistent SSE connection so continuation
  // runs (spawned as subprocesses after the original HTTP response closed) can
  // stream their output into this chat in real time.
  useEffect(() => {
    if (!sessionId || !currentConvId) return;

    // Close any previous connection for a different session
    if (sessionSseRef.current) {
      sessionSseRef.current.close();
      sessionSseRef.current = null;
    }

    const convId = currentConvId;
    let continuationMsgId = null;

    const es = new EventSource(`http://localhost:8000/api/sessions/${sessionId}/stream`);
    sessionSseRef.current = es;

    es.onmessage = (e) => {
      let event;
      try { event = JSON.parse(e.data); } catch { return; }
      if (!event || !event.type) return;

      // Ignore heartbeats
      if (event.type === 'heartbeat') return;

      // Ignore events from the primary run (already handled by the fetch stream)
      if (event.type === 'meta' && !event.continuation) return;

      if (event.type === 'meta' && event.continuation) {
        // A new continuation run is starting — create a new assistant message bubble
        continuationMsgId = genId();
        setConversations((prev) =>
          prev.map((c) =>
            c.id !== convId ? c : {
              ...c,
              messages: [
                ...c.messages,
                {
                  id: continuationMsgId,
                  role: 'agent',
                  agent_id: event.agent_id || '',
                  content: '',
                  error: false,
                  run_id: event.run_id || null,
                  inbound_tokens: null,
                  outbound_tokens: null,
                  total_tokens: null,
                  tool_calls: null,
                  duration_ms: null,
                },
              ],
            }
          )
        );
        if (event.run_id) setActiveRunId(event.run_id);
      } else if (event.type === 'token' && continuationMsgId) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id !== convId ? c : {
              ...c,
              messages: c.messages.map((m) =>
                m.id === continuationMsgId
                  ? { ...m, content: `${m.content || ''}${event.token || ''}` }
                  : m
              ),
            }
          )
        );
      } else if (event.type === 'done' && continuationMsgId) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id !== convId ? c : {
              ...c,
              messages: c.messages.map((m) =>
                m.id === continuationMsgId
                  ? {
                      ...m,
                      content: (m.content || event.response || '').trim() || event.response || '',
                      error: !event.ok,
                      run_id: event.run_id || m.run_id,
                      inbound_tokens: event.usage?.inbound_tokens ?? null,
                      outbound_tokens: event.usage?.outbound_tokens ?? null,
                      total_tokens: event.usage?.total_tokens ?? null,
                      tool_calls: event.tool_calls ?? null,
                      duration_ms: event.duration_ms ?? null,
                    }
                  : m
              ),
            }
          )
        );
        continuationMsgId = null;
      } else if (event.type === 'session_done') {
        es.close();
        sessionSseRef.current = null;
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects on error — nothing to do here
    };

    return () => {
      es.close();
      sessionSseRef.current = null;
    };
  }, [sessionId, currentConvId]);

  // Keep ref in sync so loadProcessData can check for existing data without a dep cycle
  useEffect(() => { processInsightsRef.current = processInsights; }, [processInsights]);

  const loadProcessData = useCallback(async (runId) => {
    if (!runId) return;
    const silent = processInsightsRef.current.message_runs.length > 0;
    if (!silent) {
      setProcessLoading(true);
      setProcessError('');
    }
    try {
      const insightsRes = await getMessageInsights(runId);
      const raw = insightsRes.data || {
          messages: [],
          tools: [],
          thinking: [],
          message_runs: [],
          token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
        };
      const allowed = conversationRunIds;
      const filteredRuns = (raw.message_runs || []).filter((mr) => {
        const rid = String(mr?.run_id || '');
        return rid ? allowed.has(rid) : false;
      });
      const filteredTools = (raw.tools || []).filter((t) => {
        const rid = String(t?.run_id || '');
        return rid ? allowed.has(rid) : false;
      });

      // Merge fetched run data with existing runs — the API returns data for only
      // one run at a time, so we must preserve previously-loaded runs rather than
      // replacing the whole list. Runs with matching run_id are updated in-place.
      setProcessInsights((prev) => {
        const newRunIds = new Set(filteredRuns.map((mr) => String(mr?.run_id || mr?.message_id || '')));
        const preserved = (prev.message_runs || []).filter((mr) => {
          const id = String(mr?.run_id || mr?.message_id || '');
          return id && !newRunIds.has(id);
        });
        const mergedRuns = [...preserved, ...filteredRuns];
        const newToolRunIds = new Set(filteredTools.map((t) => String(t?.run_id || '')));
        const preservedTools = (prev.tools || []).filter((t) => {
          const id = String(t?.run_id || '');
          return id && !newToolRunIds.has(id);
        });
        const inTok = mergedRuns.reduce((s, mr) => s + (Number(mr?.inbound_tokens) || 0), 0);
        const outTok = mergedRuns.reduce((s, mr) => s + (Number(mr?.outbound_tokens) || 0), 0);
        const totalTok = mergedRuns.reduce(
          (s, mr) => s + (Number(mr?.total_tokens) || ((Number(mr?.inbound_tokens) || 0) + (Number(mr?.outbound_tokens) || 0))),
          0,
        );
        return {
          ...prev,
          session_id: raw.session_id || prev.session_id,
          message_runs: mergedRuns,
          tools: [...preservedTools, ...filteredTools],
          token_usage: { inbound_tokens: inTok, outbound_tokens: outTok, total_tokens: totalTok },
        };
      });
    } catch (err) {
      if (!silent) setProcessError(err.response?.data?.detail || 'Failed to load process details.');
    } finally {
      if (!silent) setProcessLoading(false);
    }
  }, [conversationRunIds]);

  // Load all unloaded conversation runs whenever the panel is open and conversationRunIds changes.
  // Uses a ref to avoid re-fetching runs already loaded in this session.
  useEffect(() => {
    if (!processOpen || loading) return;
    const toLoad = Array.from(conversationRunIds).filter(rid => !loadedRunIdsRef.current.has(rid));
    if (!toLoad.length) return;
    toLoad.forEach(runId => {
      loadedRunIdsRef.current.add(runId);
      loadProcessData(runId);
    });
  }, [processOpen, activeRunId, loading, loadProcessData, conversationRunIds]);

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
        setAttachmentError(`"${file.name}" exceeds 5 MB.`);
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

  // Close session SSE and reset process panel when switching conversations
  useEffect(() => {
    if (sessionSseRef.current) {
      sessionSseRef.current.close();
      sessionSseRef.current = null;
    }
    setSessionId(null);
    loadedRunIdsRef.current = new Set();
    setProcessInsights({
      messages: [],
      tools: [],
      thinking: [],
      message_runs: [],
      token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
    });
  }, [currentConvId]);

  // ---- new conversation ----
  const newConversation = useCallback(() => {
    const id = genId();
    const conv = {
      id,
      title: 'New conversation',
      agent_id: selectedAgent,
      workspace: selectedWorkspace,
      project_id: selectedProject || null,
      messages: [],
      created_at: new Date().toISOString(),
    };
    setConversations((prev) => [conv, ...prev]);
    navigate(`/chat/${id}`);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [selectedAgent, selectedWorkspace, selectedProject, navigate]);

  // ---- delete conversation ----
  const deleteConversation = useCallback((id, e) => {
    e.stopPropagation();
    const conv = conversations.find((c) => c.id === id);
    const label = conv?.title || 'this conversation';
    if (!window.confirm(`Delete "${label}"?`)) return;
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (currentConvId === id) navigate('/chat');
  }, [conversations, currentConvId, navigate]);

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
      const lines = allCommands.map((c) => `**${c.name}** — ${c.description}`).join('\n');
      const helpMsg = { id: genId(), role: 'agent', content: `Available commands:\n\n${lines}`, error: false };
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [...c.messages, helpMsg] } : c));
      }
      setInput('');
      return;
    }
    if (cmd.name === '/config') {
      const agentObj = agents.find((a) => a.id === selectedAgent) || {};
      const provider = agentObj.provider || 'inherit (global)';
      const model = agentObj.model || 'inherit (global)';
      const baseUrl = agentObj.base_url || '—';
      const temperature = agentObj.temperature != null ? agentObj.temperature : 'inherit (global)';
      const maxTokens = agentObj.max_tokens != null ? agentObj.max_tokens : 'inherit (global)';
      const tools = (agentObj.tools || []).length > 0 ? (agentObj.tools || []).join(', ') : '—';
      const streaming = agentObj.streaming ? 'yes' : 'no';
      const verbose = agentObj.verbose ? 'yes' : 'no';
      setInput('');
      let systemPrompt = agentObj.system_prompt || '';
      try {
        const defResp = await getAgentDefinition(selectedAgent);
        systemPrompt = defResp.data?.system_prompt || systemPrompt;
      } catch {}
      const lines = [
        `**Agent:** ${agentObj.name || selectedAgent} (\`${agentObj.id || selectedAgent}\`)`,
        `**Description:** ${agentObj.description || '—'}`,
        `**Domain:** ${agentObj.domain || '—'}`,
        ``,
        `**Provider:** ${provider}`,
        `**Model:** ${model}`,
        `**Base URL:** ${baseUrl}`,
        `**Temperature:** ${temperature}`,
        `**Max tokens:** ${maxTokens}`,
        `**Streaming:** ${streaming}`,
        `**Verbose:** ${verbose}`,
        ``,
        `**Tools:** ${tools}`,
        ``,
        `**System prompt:**\n${systemPrompt || '—'}`,
      ].join('\n');
      const configMsg = { id: genId(), role: 'agent', content: lines, error: false };
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [...c.messages, configMsg] } : c));
      }
      return;
    }
    setInput(cmd.template);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [allCommands, currentConvId]);

  // ---- send message ----
  const sendMessage = useCallback(async () => {
    const text = input.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if ((!text && !hasAttachments) || loading || !selectedAgent) return;

    // Handle special client-side slash commands
    if (text === '/clear') { selectCommand({ name: '/clear' }); return; }
    if (text === '/new') { selectCommand({ name: '/new' }); return; }
    if (text === '/help') { selectCommand({ name: '/help' }); return; }
    if (text === '/config') { selectCommand({ name: '/config' }); return; }

    const attachmentLine = hasAttachments
      ? `Attached files: ${pendingAttachments.map((a) => a.filename).join(', ')}`
      : '';
    const userMsgText = [text, attachmentLine].filter(Boolean).join('\n');
    const userMsg = { id: genId(), role: 'user', content: userMsgText };

    // Ensure there is an active conversation
    let convId = currentConvId;
    if (!convId) {
      convId = genId();
      const basis = text || attachmentLine || 'New chat';
      const title = basis.length > 50 ? basis.slice(0, 50) + '…' : basis;
      const newConv = {
        id: convId,
        title,
        agent_id: selectedAgent,
        workspace: selectedWorkspace,
        project_id: selectedProject || null,
        messages: [],
        created_at: new Date().toISOString(),
      };
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConvId(convId);
      navigate(`/chat/${convId}`);
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

    // Use the conversation's own workspace (set at creation time), not the current global selection.
    // This locks the conversation to the workspace it was started in.
    const convRecord = conversations.find((c) => c.id === convId);
    const effectiveWorkspace = convRecord?.workspace || selectedWorkspace;
    const historyPayload = ((convRecord?.messages || [])
      .filter((m) => (m.role === 'user' || m.role === 'agent') && String(m.content || '').trim())
      .slice(-40)
      .map((m) => ({
        role: m.role,
        content: String(m.content || ''),
      })));
    const autoTitleBasis = (text || attachmentLine || '').trim();
    const autoTitle = autoTitleBasis
      ? (autoTitleBasis.length > 50 ? autoTitleBasis.slice(0, 50) + '…' : autoTitleBasis)
      : 'New conversation';
    const isPlaceholderTitle = !convRecord?.title || convRecord.title.trim().toLowerCase() === 'new conversation';
    const convTitle = (!convRecord || (convRecord.messages || []).length === 0 || isPlaceholderTitle)
      ? autoTitle
      : convRecord.title;

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
                    agent_id: selectedAgent,
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
          workspace: effectiveWorkspace || null,
          project_id: convRecord?.project_id || null,
          conversation_id: convId,
          conversation_title: convTitle,
          history: historyPayload,
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
            if (event.session_id) setSessionId(event.session_id);
            if (processOpen) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: [
                  ...(prev.message_runs || []),
                  {
                    message_id: runId,
                    run_id: runId,
                    timestamp: new Date().toISOString(),
                    input: userMsgText,
                    output: '',
                    tools: [],
                    thinking: [],
                    inbound_tokens: 0,
                    outbound_tokens: 0,
                    total_tokens: 0,
                    tool_calls: 0,
                    duration_ms: 0,
                  },
                ],
              }));
            }
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) => (m.id === assistantId ? { ...m, run_id: runId } : m)),
                },
              ),
            );
          } else if (event.type === 'tool_start') {
            // Update message bubble to show the running tool name
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId ? { ...m, running_tool: event.tool } : m
                  ),
                },
              ),
            );
            if (processOpen) {
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
            }
          } else if (event.type === 'tool_end') {
            // Keep running_tool set so the name stays visible until the next token arrives.
            if (processOpen) {
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
            }
          } else if (event.type === 'token') {
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === assistantId
                      ? { ...m, content: `${m.content || ''}${event.token || ''}`, running_tool: null }
                      : m
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
            const resolvedRunId = runId || event.run_id || null;
            if (resolvedRunId) setActiveRunId(resolvedRunId);
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
                          run_id: resolvedRunId,
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
  }, [input, loading, selectedAgent, currentConvId, messages, selectedWorkspace, selectedProject, conversations, processOpen, loadProcessData, pendingAttachments]);

  const stopGeneration = () => {
    abortCtrlRef.current?.abort();
    if (activeRunId) stopMessage(activeRunId).catch(() => {});
    setLoading(false);
  };

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
          {visibleConversations.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-10 px-3 leading-relaxed">
              No conversations yet.
              <br />
              Click <strong>New chat</strong> to start.
            </p>
          )}
          {visibleConversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => {
                navigate(`/chat/${conv.id}`);
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
              <div className="flex-1 min-w-0">
                <div className="truncate leading-5">{conv.title}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {(!selectedWorkspace || selectedWorkspace === 'default') && (
                    (!conv.workspace || conv.workspace === 'default') ? (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-gray-200 text-gray-500 font-medium">
                        default
                      </span>
                    ) : (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 font-medium truncate max-w-full">
                        {conv.workspace}
                      </span>
                    )
                  )}
                  {conv.agent_id && (() => {
                    const agent = selectableAgents.find(a => a.id === conv.agent_id);
                    return (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium truncate max-w-full">
                        {agent ? agent.name : conv.agent_id}
                      </span>
                    );
                  })()}
                  {conv.project_id && (() => {
                    const proj = projects.find(p => p.id === conv.project_id);
                    return proj ? (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium truncate max-w-full">
                        <FolderGit2 className="w-2.5 h-2.5 flex-shrink-0" />
                        {proj.name}
                      </span>
                    ) : null;
                  })()}
                </div>
              </div>
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

          {(currentConv?.workspace || selectedWorkspace) && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">WS</span>
              <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                {currentConv?.workspace || selectedWorkspace}
              </span>
            </div>
          )}

          {projects.length > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <FolderGit2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="text-xs border border-gray-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-400 max-w-[140px]"
              >
                <option value="">No project</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}

          {selectedAgent && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">MODEL</span>
              {agentProvider === 'inherit' ? (
                <span className="font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded italic">Global</span>
              ) : (
                <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                  {agentProvider}{agentModel ? ` · ${agentModel}` : ''}
                </span>
              )}
            </div>
          )}

          {selectedWorkspace && selectableAgents.length === 0 && (
            <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-1.5 text-xs font-medium">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
              No authorized agents in this workspace.
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
          <div className="max-w-full mx-auto px-6 py-8">
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
                  {selectedWorkspace && <> The agent will work in the <strong className="text-gray-700">{selectedWorkspace}</strong> workspace.</>}
                </p>
              </div>
            )}

            {messages.map((msg, idx) => {
              const msgAgentName = msg.role !== 'user'
                ? (agents.find((a) => a.id === msg.agent_id)?.name || msg.agent_id || agentName)
                : undefined;
              return (
                <MessageBubble
                  key={msg.id}
                  msg={msg}
                  isStreaming={loading && idx === messages.length - 1 && msg.role === 'agent'}
                  agentName={msgAgentName}
                />
              );
            })}

            {loading && (messages.length === 0 || messages[messages.length - 1].role !== 'agent') && (
              <TypingIndicator agentName={agentName} />
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input area */}
        <div className="flex-shrink-0 bg-white border-t border-gray-200 px-4 py-4">
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

            {/* Slash command picker */}
            {commandMenuOpen && commandSuggestions.length > 0 && (
              <div className="mb-2 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
                <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 flex items-center gap-1.5">
                  <Terminal className="w-3 h-3 text-indigo-500" />
                  <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Commands</span>
                </div>
                {commandSuggestions.map((cmd, idx) => (
                  <button
                    key={cmd.name}
                    type="button"
                    onMouseDown={(e) => { e.preventDefault(); selectCommand(cmd); }}
                    className={`w-full flex items-start gap-3 px-3 py-2 text-left transition-colors ${
                      idx === commandMenuIndex ? 'bg-indigo-50' : 'hover:bg-gray-50'
                    }`}
                  >
                    <span className=" text-sm font-semibold text-indigo-600 shrink-0">{cmd.name}</span>
                    <span className="text-xs text-gray-500 mt-0.5">{cmd.description}</span>
                  </button>
                ))}
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
                  : `Message ${agentName}…`}
                rows={1}
                value={input}
                disabled={loading || !selectedAgent}
                onChange={(e) => {
                  const val = e.target.value;
                  setInput(val);
                  resizeTextarea();
                  setCommandMenuOpen(val.startsWith('/'));
                  setCommandMenuIndex(0);
                }}
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
                  disabled={(!input.trim() && pendingAttachments.length === 0) || !selectedAgent}
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
              Enter to send · Shift+Enter for new line · Type <span className="">/</span> for commands
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
              <p className="text-[11px] text-gray-500 mt-0.5">
                {processInsights?.session_id && (
                  <p className="text-[10px] text-gray-500 -mb-0.5">
                    Session:{' '}
                    <a
                      href={`/sessions/${processInsights.session_id}`}
                      className="text-indigo-600 hover:text-indigo-700 hover:underline"
                    >
                      {processInsights.session_id}
                    </a>
                  </p>
                )}
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
            <ProcessPanelContent
              processInsights={processInsights}
            />
          )}
        </div>
      )}
    </div>
  );
}
