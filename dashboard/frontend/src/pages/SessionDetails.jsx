import React, { useEffect, useState, useCallback } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  ChevronLeft, ChevronDown, ChevronUp, Loader, RefreshCw, MessageSquare, Square, CheckCircle,
  XCircle, Clock, AlertCircle, Workflow, Trash2, Activity, Copy, Check, Send, Plus, X,
  PlayCircle,
} from 'lucide-react';
import { getSession, getSessionMessages, stopSession, deleteSession, getAgents, getMessageInsights, streamChat } from '../api';
import ProcessNode from '../components/ProcessNode';
import { toolInline } from '../components/toolFormatters';
import { preview } from '../components/processUtils';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
function fmtDurationMs(ms) {
  const n = Number(ms || 0);
  if (!n) return '0ms';
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(2)}s`;
}

function TokenPill({ label, value, title = '' }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-medium"
      title={title || undefined}
    >
      {label}: {value ?? 0}
    </span>
  );
}

function shortText(v, max = 300) {
  const s = String(v || '');
  return s.length > max ? `${s.slice(0, max)}...` : s;
}

function CopyButton({ text }) {
  const { t } = useI18n();
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
      title={t('sessionDetails.copy')}
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
  const { t } = useI18n();
  const allRuns = insightsData?.message_runs || [];
  const runs = allRuns.filter(r => String(r?.run_id || '') === String(msg?.run_id || ''));
  const assistantMsgs = (insightsData?.messages || []).filter(m => m.role === 'assistant');
  const getOutput = (r, rIdx) => r.output || assistantMsgs[rIdx]?.content || insightsData?.output || '';
  const previewOutput = shortText(getOutput(runs[0] || {}, 0) || t('sessionDetails.noResponseYet'), 220);
  const inputText = runs[0]?.input || msg.task_title || msg.description || t('sessionDetails.empty');
  const totalIn = runs.reduce((s, r) => s + (r.inbound_tokens || 0), 0);
  const totalOut = runs.reduce((s, r) => s + (r.outbound_tokens || 0), 0);
  const totalMs = runs.reduce((s, r) => s + (r.duration_ms || 0), 0);
  // The token bill above sums every step of the agent loop; this is the single
  // largest prompt of the run, which is what actually had to fit the window.
  const ctxPeak = insightsData?.context_window?.input_tokens_used || 0;
  const allTools = runs.flatMap(r => r.tools || []);
  const toolNames = Array.from(
    new Set(
      allTools
        .map((t) => String(t?.tool || '').trim())
        .filter(Boolean)
    )
  );

  // Merge each run's thoughts + tool calls into execution order (shared step
  // counter) and number thoughts by thought order only (tools don't advance it).
  let thoughtCounter = 0;
  const runSteps = runs.map((r, rIdx) => {
    const merged = [
      ...(r.reasoning || []).map((th, i) => ({ kind: 'thought', step: th.step, item: th, key: `${rIdx}-th-${i}` })),
      ...(r.tools || []).map((t, i) => ({ kind: 'tool', step: t.step, item: t, key: `${rIdx}-t-${i}` })),
    ].sort((a, b) => (a.step ?? Infinity) - (b.step ?? Infinity));
    merged.forEach((s) => { if (s.kind === 'thought') { thoughtCounter += 1; s.thoughtNo = thoughtCounter; } });
    return merged;
  });

  return (
    <div className="relative pl-4">
      {idx < total - 1 && (
        <div className="absolute left-[7px] top-4 bottom-[-16px] w-px bg-gray-200" />
      )}
      <div className="absolute left-0 top-2 w-3 h-3 rounded-full bg-indigo-500" />
      <div
        role="button"
        tabIndex={0}
        className="w-full text-left rounded-lg border border-indigo-100 bg-indigo-50 hover:bg-indigo-100 transition-colors p-3 cursor-pointer"
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
      >
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <div className="text-xs font-semibold text-indigo-800 shrink-0">#{idx + 1}</div>
            <span className="font-medium text-sm text-indigo-900 truncate">
              {shortText(msg.task_title || msg.description || 'Message', 120)}
            </span>
            {msg.message_origin === 'session_direct' && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium shrink-0">
                {t('sessionDetails.sessionMessage')}
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
        <div className="flex items-center gap-1.5 mb-2 min-h-[18px] flex-wrap">
          {allTools.length > 0 ? (
            <>
              <span className="text-[10px] text-indigo-700 font-medium">{t('sessionDetails.tools')}</span>
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
            <span className="text-[10px] text-gray-400">{t('sessionDetails.noTools')}</span>
          )}
        </div>
        <div className="flex items-end justify-between gap-2">
          <div className="flex flex-wrap gap-1">
            <TokenPill label={t('sessionDetails.in')} value={totalIn} />
            <TokenPill label={t('sessionDetails.out')} value={totalOut} />
            <TokenPill label={t('sessionDetails.total')} value={totalIn + totalOut} />
            {ctxPeak > 0 && (
              <TokenPill
                label={t('sessionDetails.ctxPeak')}
                value={ctxPeak}
                title={t('sessionDetails.ctxPeakTooltip')}
              />
            )}
            <TokenPill label={t('sessionDetails.tools2')} value={allTools.length} />
            <TokenPill label={t('sessionDetails.duration')} value={fmtDurationMs(totalMs)} />
          </div>
          {msg.run_id && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onNavigate(msg.run_id); }}
              className="shrink-0 whitespace-nowrap text-sm font-semibold text-gray-400 transition hover:text-indigo-600"
            >
              {t('sessionDetails.viewFullDetails')}
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="ml-5 mt-2 space-y-2">
          {loading ? (
            <div className="flex items-center gap-2 py-4 text-xs text-gray-400">
              <Loader className="w-4 h-4 animate-spin" /> {t('sessionDetails.loadingProcessData')}
            </div>
          ) : (() => {
            return (
              <>
                {runs.length === 0 && (
                  <p className="text-xs text-gray-400 italic px-1">{t('sessionDetails.noProcessDataAvailable')}</p>
                )}
                {runs.length > 0 && (
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-2.5">
                    <div className="text-[11px] font-semibold text-blue-700 mb-1.5">{t('sessionDetails.input')}</div>
                    <div className="text-[11px] text-gray-700">
                      {renderContent(inputText)}
                    </div>
                  </div>
                )}
                {runs.map((r, rIdx) => (
                  <React.Fragment key={rIdx}>
                    {runSteps[rIdx].map((entry) => {
                      if (entry.kind === 'thought') {
                        const th = entry.item;
                        return (
                          <ProcessNode
                            key={entry.key}
                            label={`Thought · step ${entry.thoughtNo}`}
                            labelColor="text-violet-700"
                            borderColor="border-violet-200"
                            bgColor="bg-violet-50"
                            hint={preview(th.content)}
                          >
                            <div className="text-[11px] text-violet-900 whitespace-pre-wrap break-words">
                              {th.content || '(empty)'}
                            </div>
                          </ProcessNode>
                        );
                      }
                      const tc = entry.item;
                      return (
                        <ProcessNode
                          key={entry.key}
                          label={`Tool: ${tc.tool || 'tool'}`}
                          labelColor="text-amber-700"
                          borderColor="border-amber-200"
                          bgColor="bg-amber-50"
                          hint={toolInline(tc.tool, tc.input)}
                        >
                          {tc.input && <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all"><span className="text-gray-500">{t('sessionDetails.in2')}</span> {tc.input}</div>}
                          {tc.output && <div className="text-[11px] text-emerald-700 whitespace-pre-wrap break-all"><span className="text-emerald-600">{t('sessionDetails.out2')}</span> {tc.output}</div>}
                        </ProcessNode>
                      );
                    })}
                    <div className="rounded-lg border border-green-200 bg-green-50 p-2.5">
                      <div className="text-[11px] font-semibold text-green-700 mb-1.5">{t('sessionDetails.output')}</div>
                      <div className="text-[11px] text-gray-700">{renderContent(getOutput(r, rIdx) || '(empty)')}</div>
                    </div>
                  </React.Fragment>
                ))}
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
  const { t } = useI18n();
  // Per-model context fill: the fullest the window ever got in this session.
  // `input_tokens_used` is the largest single prompt a run sent (not its token
  // bill, which sums every step of the agent loop), so the largest across runs
  // is the high-water mark for the model.
  const modelStats = {};
  Object.values(insightsMap).forEach(ins => {
    if (!ins?.context_window) return;
    const model = ins.model || 'unknown';
    const cw = ins.context_window;
    // A window of 0 means nothing knows this model's limit; an invented
    // ceiling reads worse than no meter at all.
    if (!(cw.context_window_tokens > 0)) return;
    if (!modelStats[model] || (cw.input_tokens_used || 0) > (modelStats[model].input_tokens_used || 0)) {
      modelStats[model] = { ...cw, model };
    }
  });
  const entries = Object.values(modelStats);
  if (!entries.length) return null;

  return (
    <div className="pt-2 border-t border-gray-100 space-y-3">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{t('sessionDetails.contextWindows')}</div>
      {entries.map(cw => (
        <div key={cw.model} className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] text-gray-500">
            <span className="font-medium text-gray-700 truncate max-w-[60%]">{cw.model}</span>
            <span title={t('sessionDetails.ctxTooltip')}>{cw.input_tokens_used || 0} / {cw.context_window_tokens || 0}</span>
          </div>
          <div className="w-full h-2 rounded bg-gray-100 overflow-hidden">
            <div
              className="h-full bg-indigo-500 transition-all"
              style={{ width: `${Math.min(100, Math.max(0, Number(cw.input_fulfillment_pct || 0)))}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-1">
            <TokenPill label={t('sessionDetails.ctxSize')} value={cw.context_window_tokens || 0} />
            <TokenPill label={t('sessionDetails.ctxUsed')} value={fmtPercent(cw.input_fulfillment_pct)} />
            <TokenPill label={t('sessionDetails.ctxLeft')} value={cw.input_tokens_remaining || 0} />
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
  const { t } = useI18n();
  const [expandedIds, setExpandedIds] = useState(new Set());
  const [insightsMap, setInsightsMap] = useState(initialInsightsMap);
  const [loadingIds, setLoadingIds] = useState(new Set());
  const nodeRefs = React.useRef({});

  useEffect(() => {
    setInsightsMap(prev => ({ ...prev, ...initialInsightsMap }));
  }, [initialInsightsMap]);

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
        <p className="text-sm text-gray-500">{t('sessionDetails.noMessagesInThisSession')}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 pb-1">
        <button onClick={expandAll} className="text-[10px] text-indigo-600 hover:underline">
          {t('sessionDetails.expandAll')}
        </button>
        <span className="text-gray-300 text-[10px]">·</span>
        <button onClick={collapseAll} className="text-[10px] text-gray-400 hover:underline">
          {t('sessionDetails.collapseAll')}
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
  const { t } = useI18n();
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/45 flex items-center justify-center p-4">
      <div className="bg-white w-full max-w-lg rounded-xl border border-gray-200 shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <h3 className="text-sm font-semibold text-gray-900">{t('sessionDetails.newMessage')}</h3>
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
              <option value="">{t('sessionDetails.selectAgent')}</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            {sessionWorkspace && (
              <span className="text-xs text-gray-500">{t('sessionDetails.workspace')} <strong>{sessionWorkspace}</strong></span>
            )}
          </div>
          <textarea
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm min-h-[110px] max-h-64 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder={t('sessionDetails.messageToRunInThis')}
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
              title={t('sessionDetails.send')}
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
  const { t } = useI18n();
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
      setError(err.response?.data?.detail || t('sessionDetails.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [sessionId, t]);

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
    if (!window.confirm(t('sessionDetails.confirmDelete'))) return;
    try {
      await deleteSession(sessionId);
      navigate('/sessions');
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('sessionDetails.deleteFailed'));
    }
  };

  const handleAddMessage = async () => {
    const prompt = composerPrompt.trim();
    if (!composerAgentId) {
      setComposerError(t('sessionDetails.selectAnAgentError'));
      return;
    }
    if (!prompt) {
      setComposerError(t('sessionDetails.enterMessage'));
      return;
    }
    setComposerError('');
    setComposerSending(true);
    const title = prompt.split('\n')[0].trim().slice(0, 120) || 'Agent run';
    const startedAt = new Date().toISOString();
    const conversationId = session?.conversation_id || session?.session_id || sessionId;
    const workspace = session?.workspace || null;
    const history = [];
    for (const msg of messages) {
      const insight = insightsMap[msg.run_id] || {};
      const insightMessages = Array.isArray(insight.messages) ? insight.messages : [];
      if (insightMessages.length) {
        for (const item of insightMessages) {
          if ((item.role === 'user' || item.role === 'agent' || item.role === 'assistant') && String(item.content || '').trim()) {
            history.push({
              role: item.role === 'assistant' ? 'agent' : item.role,
              content: String(item.content || ''),
            });
          }
        }
        continue;
      }
      for (const run of insight.message_runs || []) {
        if (String(run.input || '').trim()) history.push({ role: 'user', content: String(run.input || '') });
        if (String(run.output || '').trim()) history.push({ role: 'agent', content: String(run.output || '') });
      }
    }

    let newRunId = null;
    const ensureStreamingRun = (runId) => {
      if (!runId) return;
      setMessages(prev => (
        prev.some(m => String(m.run_id) === String(runId))
          ? prev
          : [
              ...prev,
              {
                run_id: runId,
                task_id: conversationId,
                task_title: title,
                title,
                description: prompt,
                agent_id: composerAgentId,
                status: 'running',
                started_at: startedAt,
                finished_at: null,
                session_id: sessionId,
                session_type: 'chat',
                message_origin: 'chat',
                workspace,
              },
            ]
      ));
      setInsightsMap(prev => ({
        ...prev,
        [runId]: prev[runId] || {
          messages: [
            { role: 'user', content: prompt, agent_id: composerAgentId },
            { role: 'assistant', content: '', agent_id: composerAgentId },
          ],
          message_runs: [{
            run_id: runId,
            message_id: runId,
            timestamp: startedAt,
            input: prompt,
            output: '',
            tools: [],
            thinking: [],
            inbound_tokens: 0,
            outbound_tokens: 0,
            total_tokens: 0,
            tool_calls: 0,
            duration_ms: 0,
            agent_id: composerAgentId,
          }],
        },
      }));
    };

    const updateStreamingRun = (runId, updater) => {
      if (!runId) return;
      setInsightsMap(prev => {
        const current = prev[runId] || {
          messages: [
            { role: 'user', content: prompt, agent_id: composerAgentId },
            { role: 'assistant', content: '', agent_id: composerAgentId },
          ],
          message_runs: [{
            run_id: runId,
            message_id: runId,
            timestamp: startedAt,
            input: prompt,
            output: '',
            tools: [],
            thinking: [],
            inbound_tokens: 0,
            outbound_tokens: 0,
            total_tokens: 0,
            tool_calls: 0,
            duration_ms: 0,
            agent_id: composerAgentId,
          }],
        };
        return { ...prev, [runId]: updater(current) };
      });
    };

    setComposerPrompt('');
    setShowNewMessageModal(false);
    try {
      await streamChat({
        body: {
          agent_id: composerAgentId,
          message: prompt,
          workspace,
          conversation_id: conversationId,
          conversation_title: session?.title || title,
          history: history.slice(-40),
          attachments: [],
        },
        onEvent: (event) => {
          if (event.type === 'meta' && event.run_id) {
            newRunId = event.run_id;
            ensureStreamingRun(newRunId);
            setScrollToRunId(String(newRunId));
          } else if (event.type === 'token' && newRunId) {
            const token = event.token || '';
            updateStreamingRun(newRunId, (current) => {
              const messagesNext = [...(current.messages || [])];
              const assistantIdx = messagesNext.findIndex(m => m.role === 'assistant' || m.role === 'agent');
              if (assistantIdx >= 0) {
                messagesNext[assistantIdx] = {
                  ...messagesNext[assistantIdx],
                  role: 'assistant',
                  content: `${messagesNext[assistantIdx].content || ''}${token}`,
                };
              }
              const runs = [...(current.message_runs || [])];
              if (runs[0]) runs[0] = { ...runs[0], output: `${runs[0].output || ''}${token}` };
              return { ...current, messages: messagesNext, message_runs: runs };
            });
          } else if (event.type === 'think' && event.native && newRunId) {
            // Native model reasoning — surface in the session message node live.
            updateStreamingRun(newRunId, (current) => {
              const runs = [...(current.message_runs || [])];
              const run = runs[0] || {};
              runs[0] = {
                ...run,
                reasoning: [
                  ...(run.reasoning || []),
                  { step: event.step, content: event.content, native: true },
                ],
              };
              return { ...current, message_runs: runs };
            });
          } else if (event.type === 'tool_start' && newRunId) {
            updateStreamingRun(newRunId, (current) => {
              const runs = [...(current.message_runs || [])];
              const run = runs[0] || {};
              runs[0] = {
                ...run,
                tools: [
                  ...(run.tools || []),
                  {
                    step: event.step,
                    tool: event.tool,
                    input: event.input,
                    output: null,
                    running: true,
                  },
                ],
                tool_calls: (run.tool_calls || 0) + 1,
              };
              return { ...current, message_runs: runs };
            });
          } else if (event.type === 'tool_end' && newRunId) {
            updateStreamingRun(newRunId, (current) => {
              const runs = [...(current.message_runs || [])];
              const run = runs[0] || {};
              const tools = [...(run.tools || [])];
              for (let i = tools.length - 1; i >= 0; i -= 1) {
                if (tools[i].running) {
                  tools[i] = { ...tools[i], output: event.output, running: false };
                  break;
                }
              }
              runs[0] = { ...run, tools };
              return { ...current, message_runs: runs };
            });
          } else if (event.type === 'done') {
            if (event.run_id) {
              newRunId = event.run_id;
              ensureStreamingRun(newRunId);
            }
            if (newRunId) {
              setMessages(prev => prev.map(m => (
                String(m.run_id) === String(newRunId)
                  ? {
                      ...m,
                      status: event.ok ? 'completed' : 'failed',
                      finished_at: new Date().toISOString(),
                      error: event.error || null,
                    }
                  : m
              )));
              updateStreamingRun(newRunId, (current) => {
                const inTok = event.usage?.inbound_tokens || 0;
                const outTok = event.usage?.outbound_tokens || 0;
                const totalTok = event.usage?.total_tokens || (inTok + outTok);
                const runs = [...(current.message_runs || [])];
                if (runs[0]) {
                  runs[0] = {
                    ...runs[0],
                    output: event.response || runs[0].output || '',
                    inbound_tokens: inTok,
                    outbound_tokens: outTok,
                    total_tokens: totalTok,
                    tool_calls: event.tool_calls ?? runs[0].tool_calls ?? 0,
                    duration_ms: event.duration_ms ?? runs[0].duration_ms ?? 0,
                  };
                }
                const messagesNext = [...(current.messages || [])];
                const assistantIdx = messagesNext.findIndex(m => m.role === 'assistant' || m.role === 'agent');
                if (assistantIdx >= 0) {
                  messagesNext[assistantIdx] = {
                    ...messagesNext[assistantIdx],
                    role: 'assistant',
                    content: event.response || messagesNext[assistantIdx].content || '',
                  };
                }
                return {
                  ...current,
                  output: event.response || current.output || '',
                  token_usage: {
                    inbound_tokens: inTok,
                    outbound_tokens: outTok,
                    total_tokens: totalTok,
                  },
                  message_runs: runs,
                  messages: messagesNext,
                };
              });
            }
          }
        },
      });
      await load();
      if (newRunId) setScrollToRunId(String(newRunId));
    } catch (err) {
      setComposerError(err?.response?.data?.detail || err.message || t('sessionDetails.startRunFailed'));
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
          <ChevronLeft className="w-4 h-4" /> {t('sessionDetails.backToSessions')}
        </Link>
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">{error}</div>
      </div>
    );
  }

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={PlayCircle}
        title={t('sessionDetails.sessionDetails')}
        backTo="/sessions"
        backLabel={t('sessionDetails.sessions')}
        badges={session?.is_flow && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-violet-100 text-violet-700">
            <Workflow className="w-3.5 h-3.5" /> {t('sessionDetails.flow')}
          </span>
        )}
        actions={<>
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
            {t('sessionDetails.refresh')}
          </button>
          <button
            onClick={handleDelete}
            disabled={session?.status === 'running'}
            title={session?.status === 'running' ? t('sessionDetails.stopBeforeDeleting') : t('sessionDetails.deleteSession')}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
          >
            <Trash2 className="w-4 h-4" />
            {t('sessionDetails.delete')}
          </button>
        </>}
      />

      {/* Metadata card */}
      <div className="bg-white border border-gray-200 rounded-xl p-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-sm">
        <div><span className="text-gray-500">{t('sessionDetails.sessionId')}</span> <span className="text-xs">{session?.session_id}</span></div>
        <div><span className="text-gray-500">{t('sessionDetails.title')}</span> <span className="font-medium text-gray-800">{session?.title || '—'}</span></div>
        {session?.description && (
          <div><span className="text-gray-500">{t('sessionDetails.description')}</span> <span className="text-gray-700">{session.description}</span></div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-gray-500">{t('sessionDetails.status')}</span>
          <StatusBadge status={session?.status || 'pending'} />
        </div>
        <div><span className="text-gray-500">{t('sessionDetails.workspace2')}</span> {session?.workspace || '—'}</div>
        <div>
          <span className="text-gray-500">{t('sessionDetails.agents')}</span>{' '}
          <span className="text-xs">{(session?.agents || []).join(', ') || '—'}</span>
        </div>
        <div><span className="text-gray-500">{t('sessionDetails.messages')}</span> {session?.message_count ?? 0}</div>
        <div><span className="text-gray-500">{t('sessionDetails.created')}</span> {fmtDate(session?.created_at)}</div>
        <div><span className="text-gray-500">{t('sessionDetails.finished')}</span> {fmtDate(session?.finished_at)}</div>
        <div><span className="text-gray-500">{t('sessionDetails.duration2')}</span> {duration(session?.created_at, session?.finished_at)}</div>
        <div className="md:col-span-2">
          <ContextWindowsPanel insightsMap={insightsMap} />
        </div>
      </div>
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
            {t('sessionDetails.newMessage')}
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
                  {ev.agent_id && <span className="ml-2 text-xs text-gray-400">{t('sessionDetails.agentLabel')}: {ev.agent_id}</span>}
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

    </PageContainer>
  );
}
