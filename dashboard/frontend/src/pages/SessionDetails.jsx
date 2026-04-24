import React, { useEffect, useState, useCallback } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  ChevronLeft, ChevronDown, ChevronUp, Loader, RefreshCw, MessageSquare, Square, CheckCircle,
  XCircle, Clock, AlertCircle, Workflow, Trash2, Activity, Copy, Check, Send, Plus, X,
} from 'lucide-react';
import { getSession, getSessionMessages, stopSession, deleteSession, getAgents, createSessionMessage, getMessageInsights } from '../api';

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

function shortText(v, max = 300) {
  const s = String(v || '');
  return s.length > max ? `${s.slice(0, max)}...` : s;
}

function CopyButton({ text }) {
  const [copied, setCopied] = React.useState(false);
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
      trimmed.startsWith('|') && trimmed.endsWith('|') &&
      nextTrimmed.startsWith('|') && /^\|[\s\-:|]+\|$/.test(nextTrimmed)
    ) {
      flushPlain();
      const headers = trimmed.split('|').slice(1, -1).map(h => h.trim());
      i += 2;
      const rows = [];
      while (i < lines.length) {
        const rowLine = lines[i].trim();
        if (rowLine.startsWith('|') && rowLine.endsWith('|')) {
          rows.push(rowLine.split('|').slice(1, -1).map(c => c.trim()));
          i++;
        } else { break; }
      }
      elements.push(
        <div key={`${keyPrefix}t${elemIdx++}`} className="my-3 overflow-x-auto">
          <table className="min-w-full text-xs border border-gray-200 rounded-lg overflow-hidden">
            <thead className="bg-gray-50">
              <tr>{headers.map((h, j) => <th key={j} className="px-3 py-2 text-left font-semibold text-gray-700 border-b border-gray-200">{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, j) => (
                <tr key={j} className={j % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {row.map((cell, k) => <td key={k} className="px-3 py-2 text-gray-700 border-b border-gray-100">{cell}</td>)}
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
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```')) {
      const inner = part.slice(3, -3);
      const newline = inner.indexOf('\n');
      const lang = newline > 0 ? inner.slice(0, newline).trim() : '';
      const code = newline > 0 ? inner.slice(newline + 1) : inner;
      return (
        <div key={i} className="relative my-2">
          {lang && <div className="bg-gray-800 text-gray-400 text-xs px-4 py-1.5 rounded-t-lg border-b border-gray-700">{lang}</div>}
          <pre className={`bg-gray-900 text-gray-100 text-xs p-4 overflow-x-auto ${lang ? 'rounded-b-lg' : 'rounded-lg'} whitespace-pre`}>
            <CopyButton text={code} />
            {code}
          </pre>
        </div>
      );
    }
    return <React.Fragment key={i}>{renderTableAwareText(part, `${i}-`)}</React.Fragment>;
  });
}

function GraphNode({ title, children, tone = 'slate' }) {
  const tones = { slate: 'border-gray-200 bg-white', tool: 'border-amber-200 bg-amber-50', output: 'border-emerald-200 bg-emerald-50' };
  return (
    <div className={`rounded-lg border p-2.5 ${tones[tone] || tones.slate}`}>
      <div className="text-[11px] font-semibold text-gray-700 mb-1.5">{title}</div>
      {children}
    </div>
  );
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

function duration(started, finished) {
  if (!started) return '—';
  const end = finished ? new Date(finished) : new Date();
  const secs = Math.max(0, Math.round((end - new Date(started)) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

const STATUS_STYLES = {
  running:   { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Loader },
  completed: { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  failed:    { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  error:     { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  stopped:   { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Square },
  stop:      { bg: 'bg-orange-100', text: 'text-orange-700', icon: Square },
  pending:   { bg: 'bg-gray-100',   text: 'text-gray-500',   icon: Clock },
};

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'running' ? 'animate-spin' : ''}`} />
      {status}
    </span>
  );
}

function MessageNode({ msg, idx, total, expanded, loading, insightsData, onToggle, onNavigate }) {
  const allRuns = insightsData?.message_runs || [];
  const runs = allRuns.filter(r => String(r?.run_id || '') === String(msg?.run_id || ''));
  const assistantMsgs = (insightsData?.messages || []).filter(m => m.role === 'assistant');
  const getOutput = (r, rIdx) => r.output || assistantMsgs[rIdx]?.content || insightsData?.output || '';
  const previewOutput = shortText(getOutput(runs[0] || {}, 0) || '(no response yet)', 220);
  const inputText = runs[0]?.input || msg.task_title || msg.description || '(empty)';
  const totalIn = runs.reduce((s, r) => s + (r.inbound_tokens || 0), 0);
  const totalOut = runs.reduce((s, r) => s + (r.outbound_tokens || 0), 0);
  const totalMs = runs.reduce((s, r) => s + (r.duration_ms || 0), 0);
  const allTools = runs.flatMap(r => r.tools || []);
  const toolNames = Array.from(
    new Set(
      allTools
        .map((t) => String(t?.tool || '').trim())
        .filter(Boolean)
    )
  );

  return (
    <div className="relative pl-4">
      {idx < total - 1 && (
        <div className="absolute left-[7px] top-4 bottom-[-16px] w-px bg-gray-200" />
      )}
      <div className="absolute left-0 top-2 w-3 h-3 rounded-full bg-indigo-500" />
      <button
        className="w-full text-left rounded-lg border border-indigo-100 bg-indigo-50 hover:bg-indigo-100 transition-colors p-3"
        onClick={onToggle}
      >
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <div className="text-xs font-semibold text-indigo-800 shrink-0">#{idx + 1}</div>
            <span className="font-medium text-sm text-indigo-900 truncate">
              {shortText(msg.task_title || msg.description || 'Message', 120)}
            </span>
            {msg.message_origin === 'session_direct' && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium shrink-0">
                Session message
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-[10px] text-indigo-600">{fmtDate(msg.started_at)}</span>
            {expanded ? <ChevronUp className="w-3.5 h-3.5 text-indigo-500" /> : <ChevronDown className="w-3.5 h-3.5 text-indigo-500" />}
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
          {msg.agent_id && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 text-[10px] font-medium mr-1">
              {msg.agent_id}
            </span>
          )}
          <span>{previewOutput}</span>
        </div>
        <div className="flex items-center gap-1.5 mb-1 min-h-[18px]">
          {allTools.length > 0 ? (
            <>
              <span className="text-[10px] text-indigo-700 font-medium">tools:</span>
              {toolNames.slice(0, 3).map((name) => (
                <span
                  key={name}
                  className="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium"
                >
                  {name}
                </span>
              ))}
              {toolNames.length > 3 && (
                <span className="text-[10px] text-amber-700">+{toolNames.length - 3}</span>
              )}
            </>
          ) : (
            <span className="text-[10px] text-gray-400">no tools</span>
          )}
        </div>
      </button>

      {expanded && (
        <div className="ml-5 mt-2 space-y-2">
          {loading ? (
            <div className="flex items-center gap-2 py-4 text-xs text-gray-400">
              <Loader className="w-4 h-4 animate-spin" /> Loading process data…
            </div>
          ) : (() => {
            return (
              <>
                {(totalIn || totalOut || totalMs) ? (
                  <div className="flex flex-wrap gap-1 pb-1">
                    <TokenPill label="in" value={totalIn} />
                    <TokenPill label="out" value={totalOut} />
                    <TokenPill label="total" value={totalIn + totalOut} />
                    <TokenPill label="tools" value={allTools.length} />
                    <TokenPill label="duration" value={fmtDurationMs(totalMs)} />
                  </div>
                ) : null}
                {runs.length === 0 && (
                  <p className="text-xs text-gray-400 italic px-1">No process data available.</p>
                )}
                {runs.length > 0 && (
                  <GraphNode title="Input" tone="slate">
                    <div className="text-[11px] text-gray-700">
                      {renderContent(inputText)}
                    </div>
                  </GraphNode>
                )}
                {runs.map((r, rIdx) => (
                  <React.Fragment key={rIdx}>
                    {(r.tools || []).map((t, tIdx) => (
                      <GraphNode key={tIdx} title={`Tool: ${t.tool || 'tool'}`} tone="tool">
                        {t.input && <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all"><span className="text-gray-500">in:</span> {shortText(t.input)}</div>}
                        {t.output && <div className="text-[11px] text-emerald-700 mt-1 whitespace-pre-wrap break-all"><span className="text-emerald-600">out:</span> {shortText(t.output)}</div>}
                      </GraphNode>
                    ))}
                    <GraphNode title="Output" tone="output">
                      <div className="text-[11px] text-gray-700">{renderContent(getOutput(r, rIdx) || '(empty)')}</div>
                    </GraphNode>
                  </React.Fragment>
                ))}
                <button
                  onClick={(e) => { e.stopPropagation(); onNavigate(msg.run_id); }}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs text-indigo-600 border border-indigo-200 rounded-lg hover:bg-indigo-50"
                >
                  View Full Details
                </button>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

function fmtPercent(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0%';
  return `${n.toFixed(2)}%`;
}

function ContextWindowsPanel({ insightsMap }) {
  // Build per-model context stats — keep the entry with the highest token usage per model
  const modelStats = {};
  Object.values(insightsMap).forEach(ins => {
    if (!ins?.context_window) return;
    const model = ins.model || 'unknown';
    const cw = ins.context_window;
    if (!modelStats[model] || (cw.input_tokens_used || 0) > (modelStats[model].input_tokens_used || 0)) {
      modelStats[model] = { ...cw, model };
    }
  });
  const entries = Object.values(modelStats);
  if (!entries.length) return null;

  return (
    <div className="pt-2 border-t border-gray-100 space-y-3">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Context Windows</div>
      {entries.map(cw => (
        <div key={cw.model} className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] text-gray-500">
            <span className="font-medium text-gray-700 truncate max-w-[60%]">{cw.model}</span>
            <span>{cw.input_tokens_used || 0} / {cw.context_window_tokens || 0}</span>
          </div>
          <div className="w-full h-2 rounded bg-gray-100 overflow-hidden">
            <div
              className="h-full bg-indigo-500 transition-all"
              style={{ width: `${Math.min(100, Math.max(0, Number(cw.input_fulfillment_pct || 0)))}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-1">
            <TokenPill label="ctx size" value={cw.context_window_tokens || 0} />
            <TokenPill label="ctx used" value={fmtPercent(cw.input_fulfillment_pct)} />
            <TokenPill label="ctx left" value={cw.input_tokens_remaining || 0} />
          </div>
        </div>
      ))}
    </div>
  );
}

function SessionMessagesGraph({
  messages = [],
  onNavigate,
  initialInsightsMap = {},
  scrollToRunId = null,
  onScrollHandled = null,
}) {
  const [expandedIds, setExpandedIds] = useState(new Set());
  const [insightsMap, setInsightsMap] = useState(initialInsightsMap);
  const [loadingIds, setLoadingIds] = useState(new Set());
  const nodeRefs = React.useRef({});

  const loadInsights = useCallback(async (runId) => {
    if (insightsMap[runId] !== undefined) return;
    setLoadingIds(prev => new Set([...prev, runId]));
    try {
      const res = await getMessageInsights(runId);
      setInsightsMap(prev => ({ ...prev, [runId]: res.data || {} }));
    } catch {
      setInsightsMap(prev => ({ ...prev, [runId]: {} }));
    } finally {
      setLoadingIds(prev => { const next = new Set(prev); next.delete(runId); return next; });
    }
  }, [insightsMap]);

  const toggle = (runId) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
        loadInsights(runId);
      }
      return next;
    });
  };

  const expandAll = () => {
    setExpandedIds(new Set(messages.map(m => m.run_id)));
    messages.forEach(m => loadInsights(m.run_id));
  };

  const collapseAll = () => setExpandedIds(new Set());

  useEffect(() => {
    if (!scrollToRunId) return;
    const el = nodeRefs.current[String(scrollToRunId)];
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (typeof onScrollHandled === 'function') onScrollHandled();
  }, [scrollToRunId, messages, onScrollHandled]);

  if (!messages.length) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl text-center py-10">
        <MessageSquare className="w-8 h-8 text-gray-300 mx-auto mb-2" />
        <p className="text-sm text-gray-500">No messages in this session yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 pb-1">
        <button onClick={expandAll} className="text-[10px] text-indigo-600 hover:underline">
          Expand all
        </button>
        <span className="text-gray-300 text-[10px]">·</span>
        <button onClick={collapseAll} className="text-[10px] text-gray-400 hover:underline">
          Collapse all
        </button>
      </div>
      {messages.map((msg, idx) => (
        <div key={msg.run_id} ref={(el) => { nodeRefs.current[String(msg.run_id)] = el; }}>
          <MessageNode
            msg={msg}
            idx={idx}
            total={messages.length}
            expanded={expandedIds.has(msg.run_id)}
            loading={loadingIds.has(msg.run_id)}
            insightsData={insightsMap[msg.run_id]}
            onToggle={() => toggle(msg.run_id)}
            onNavigate={onNavigate}
          />
        </div>
      ))}
    </div>
  );
}

function NewMessageModal({
  open,
  onClose,
  agents,
  sessionWorkspace,
  composerAgentId,
  setComposerAgentId,
  composerPrompt,
  setComposerPrompt,
  composerError,
  composerSending,
  onSend,
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/45 flex items-center justify-center p-4">
      <div className="bg-white w-full max-w-lg rounded-xl border border-gray-200 shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <h3 className="text-sm font-semibold text-gray-900">New message</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            disabled={composerSending}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4 space-y-3">
          <div className="flex items-center gap-3">
            <select
              className="w-56 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={composerAgentId}
              onChange={(e) => setComposerAgentId(e.target.value)}
              disabled={composerSending}
            >
              <option value="">Select agent…</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            {sessionWorkspace && (
              <span className="text-xs text-gray-500">workspace: <strong>{sessionWorkspace}</strong></span>
            )}
          </div>
          <textarea
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm min-h-[110px] max-h-64 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Message to run in this session..."
            value={composerPrompt}
            onChange={(e) => setComposerPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!composerSending) onSend();
              }
            }}
            disabled={composerSending}
          />
          {composerError && <p className="text-xs text-red-600">{composerError}</p>}
          <div className="flex justify-end">
            <button
              onClick={onSend}
              disabled={composerSending}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              title="Send"
            >
              {composerSending ? <Loader className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SessionDetails() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading]         = useState(true);
  const [session, setSession]         = useState(null);
  const [messages, setMessages]       = useState([]);
  const [agents, setAgents]           = useState([]);
  const [error, setError]             = useState('');
  const [stopping, setStopping]       = useState(false);
  const [insightsMap, setInsightsMap] = useState({});
  const [composerAgentId, setComposerAgentId] = useState('');
  const [composerPrompt, setComposerPrompt] = useState('');
  const [composerError, setComposerError] = useState('');
  const [composerSending, setComposerSending] = useState(false);
  const [showNewMessageModal, setShowNewMessageModal] = useState(false);
  const [scrollToRunId, setScrollToRunId] = useState(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError('');
    try {
      const [sessionRes, messagesRes] = await Promise.all([
        getSession(sessionId),
        getSessionMessages(sessionId),
      ]);
      setSession(sessionRes.data || null);
      const msgs = messagesRes.data || [];
      setMessages(msgs);
      // Load all insights in parallel (for context window panel + pre-populate expanded nodes)
      const insightResults = await Promise.allSettled(
        msgs.map(m => getMessageInsights(m.run_id))
      );
      const map = {};
      insightResults.forEach((r, i) => {
        map[msgs[i].run_id] = r.status === 'fulfilled' ? (r.value.data || {}) : {};
      });
      setInsightsMap(map);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load session');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { getAgents().then(r => setAgents(r.data)).catch(() => {}); }, []);
  useEffect(() => {
    if (composerAgentId) return;
    if (!agents.length) return;
    const preferred = (session?.agents || []).find(a => agents.some(x => x.id === a));
    setComposerAgentId(preferred || agents[0].id || '');
  }, [agents, session, composerAgentId]);

  const handleStop = async () => {
    setStopping(true);
    try {
      await stopSession(sessionId);
      await load();
    } catch (err) {
      console.error('Failed to stop session', err);
    } finally {
      setStopping(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm('Delete this session? Message records will be kept.')) return;
    try {
      await deleteSession(sessionId);
      navigate('/sessions');
    } catch (err) {
      window.alert(err?.response?.data?.detail || 'Failed to delete session');
    }
  };

  const handleAddMessage = async () => {
    const prompt = composerPrompt.trim();
    if (!composerAgentId) {
      setComposerError('Select an agent.');
      return;
    }
    if (!prompt) {
      setComposerError('Enter a message.');
      return;
    }
    setComposerError('');
    setComposerSending(true);
    try {
      const title = prompt.split('\n')[0].trim().slice(0, 120) || 'Agent run';
      const res = await createSessionMessage(sessionId, {
        title,
        description: prompt,
        agent_id: composerAgentId,
      });
      const newRunId = res?.data?.run_id || null;
      setComposerPrompt('');
      setShowNewMessageModal(false);
      await load();
      if (newRunId) setScrollToRunId(String(newRunId));
    } catch (err) {
      setComposerError(err?.response?.data?.detail || 'Failed to start run');
    } finally {
      setComposerSending(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-20"><Loader className="w-6 h-6 animate-spin text-indigo-500" /></div>;
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link to="/sessions" className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline">
          <ChevronLeft className="w-4 h-4" /> Back to sessions
        </Link>
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">{error}</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Link to="/sessions" className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline">
            <ChevronLeft className="w-4 h-4" /> Back to sessions
          </Link>
          <div className="flex items-center gap-3 mt-2">
            <h1 className="text-2xl font-bold text-gray-900">Session Details</h1>
            {session?.is_flow && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-violet-100 text-violet-700">
                <Workflow className="w-3.5 h-3.5" />
                Flow
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {session?.status === 'running' && (
            <button
              onClick={handleStop}
              disabled={stopping}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-red-200 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-40"
            >
              {stopping ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
              Stop All
            </button>
          )}
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button
            onClick={handleDelete}
            disabled={session?.status === 'running'}
            title={session?.status === 'running' ? 'Stop before deleting' : 'Delete session'}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
          >
            <Trash2 className="w-4 h-4" />
            Delete
          </button>
        </div>
      </div>

      {/* Session metadata */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-2 text-sm">
        <div><span className="text-gray-500">Session ID:</span> <span className="text-xs">{session?.session_id}</span></div>
        <div><span className="text-gray-500">Title:</span> <span className="font-medium text-gray-800">{session?.title || '—'}</span></div>
        {session?.description && (
          <div><span className="text-gray-500">Description:</span> <span className="text-gray-700">{session.description}</span></div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-gray-500">Status:</span>
          <StatusBadge status={session?.status || 'pending'} />
        </div>
        <div><span className="text-gray-500">Workspace:</span> {session?.workspace || '—'}</div>
        <div>
          <span className="text-gray-500">Agents:</span>{' '}
          <span className="text-xs">{(session?.agents || []).join(', ') || '—'}</span>
        </div>
        <div><span className="text-gray-500">Messages:</span> {session?.message_count ?? 0}</div>
        <div><span className="text-gray-500">Created:</span> {fmtDate(session?.created_at)}</div>
        <div><span className="text-gray-500">Finished:</span> {fmtDate(session?.finished_at)}</div>
        <div><span className="text-gray-500">Duration:</span> {duration(session?.created_at, session?.finished_at)}</div>
        <ContextWindowsPanel insightsMap={insightsMap} />
      </div>

      {/* Messages list */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-indigo-500" />
            Messages List
            <span className="text-xs font-normal text-gray-500">({messages.length})</span>
          </h2>
          <button
            onClick={() => setShowNewMessageModal(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border border-indigo-200 text-indigo-600 rounded-lg hover:bg-indigo-50"
          >
            <Plus className="w-4 h-4" />
            New message
          </button>
        </div>

        <SessionMessagesGraph
          messages={messages}
          onNavigate={(runId) => navigate(`/messages/${runId}`)}
          initialInsightsMap={insightsMap}
          scrollToRunId={scrollToRunId}
          onScrollHandled={() => setScrollToRunId(null)}
        />
      </div>

      {/* Events list (non-agent steps like approvals) */}
      {(session?.events?.length > 0) && (
        <div>
          <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2 mb-3">
            <Activity className="w-4 h-4 text-amber-500" />
            Session Events
            <span className="text-xs font-normal text-gray-500">({session.events.length})</span>
          </h2>
          <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">
            {session.events.map((ev, idx) => (
              <div key={idx} className="flex items-start gap-3 px-4 py-3 text-sm">
                <span className={`mt-0.5 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                  ev.type === 'approved' ? 'bg-green-100 text-green-700' :
                  ev.type === 'rejected' ? 'bg-red-100 text-red-700' :
                  ev.type === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
                  'bg-gray-100 text-gray-600'
                }`}>
                  {ev.type}
                </span>
                <div className="flex-1 min-w-0">
                  <span className="text-gray-700">{ev.description || ev.type}</span>
                  {ev.agent_id && <span className="ml-2 text-xs text-gray-400">agent: {ev.agent_id}</span>}
                </div>
                <span className="text-xs text-gray-400 shrink-0">{ev.timestamp ? new Date(ev.timestamp).toLocaleString() : '—'}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <NewMessageModal
        open={showNewMessageModal}
        onClose={() => {
          if (composerSending) return;
          setShowNewMessageModal(false);
        }}
        agents={agents}
        sessionWorkspace={session?.workspace}
        composerAgentId={composerAgentId}
        setComposerAgentId={setComposerAgentId}
        composerPrompt={composerPrompt}
        setComposerPrompt={setComposerPrompt}
        composerError={composerError}
        composerSending={composerSending}
        onSend={handleAddMessage}
      />

    </div>
  );
}
