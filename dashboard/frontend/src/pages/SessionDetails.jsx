import React, { useEffect, useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronLeft, Loader, RefreshCw, MessageSquare, Wrench, Bot, FileText } from 'lucide-react';
import { getSession, getSessionLogs, getSessionInsights } from '../api';

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

function fmtPercent(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0%';
  return `${n.toFixed(2)}%`;
}

function ProcessGraph({ messageRuns = [] }) {
  if (!messageRuns.length) return <p className="text-xs text-gray-500 italic">No graph data available.</p>;
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
              <div className="text-xs font-semibold text-indigo-800">Message {idx + 1}</div>
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
                {t.input && <div className="text-[11px] text-gray-700 font-mono">in: {t.input}</div>}
                {t.output && <div className="text-[11px] text-emerald-700 font-mono mt-1">out: {t.output}</div>}
              </Node>
            ))}

            <Node title="Output" tone="output">
              <div className="text-[11px] text-gray-700 whitespace-pre-wrap">{mr.output || '(empty)'}</div>
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

export default function SessionDetails() {
  const { runId } = useParams();
  const [loading, setLoading] = useState(true);
  const [session, setSession] = useState(null);
  const [insights, setInsights] = useState({ messages: [], tools: [], thinking: [] });
  const [logs, setLogs] = useState('');
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('process');

  const load = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setError('');
    try {
      const [sessionRes, insightsRes, logsRes] = await Promise.all([
        getSession(runId),
        getSessionInsights(runId),
        getSessionLogs(runId),
      ]);
      setSession(sessionRes.data || null);
      setInsights(insightsRes.data || { messages: [], tools: [], thinking: [] });
      setLogs(logsRes.data?.logs || '');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load session details');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Loader className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
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
      <div className="flex items-center justify-between">
        <div>
          <Link to="/sessions" className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline">
            <ChevronLeft className="w-4 h-4" /> Back to sessions
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-2">Session Details</h1>
        </div>
        <button
          onClick={load}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-2 text-sm">
        <div><span className="text-gray-500">Run ID:</span> <span className="font-mono text-xs">{session?.run_id}</span></div>
        <div><span className="text-gray-500">Title:</span> <span className="font-medium text-gray-800">{session?.task_title || '—'}</span></div>
        <div><span className="text-gray-500">Agent:</span> <span className="font-mono text-xs">{session?.agent_id || '—'}</span></div>
        <div><span className="text-gray-500">Model:</span> <span className="font-mono text-xs">{insights?.model || insights?.context_window?.model || '—'}</span></div>
        <div><span className="text-gray-500">Status:</span> <span className="font-medium">{session?.status || '—'}</span></div>
        <div><span className="text-gray-500">Workspace:</span> {session?.workspace || '—'}</div>
        <div><span className="text-gray-500">Started:</span> {fmtDate(session?.started_at)}</div>
        <div><span className="text-gray-500">Finished:</span> {fmtDate(session?.finished_at)}</div>
        <div><span className="text-gray-500">Duration:</span> {duration(session?.started_at, session?.finished_at)}</div>
        <div><span className="text-gray-500">Error:</span> {session?.error || '—'}</div>
        <div className="flex flex-wrap gap-2 pt-1">
          <TokenPill label="session in" value={insights?.token_usage?.inbound_tokens || 0} />
          <TokenPill label="session out" value={insights?.token_usage?.outbound_tokens || 0} />
          <TokenPill label="session total" value={insights?.token_usage?.total_tokens || 0} />
          <TokenPill label="ctx size" value={insights?.context_window?.context_window_tokens || 0} />
          <TokenPill label="ctx used" value={fmtPercent(insights?.context_window?.input_fulfillment_pct || 0)} />
          <TokenPill label="ctx left" value={insights?.context_window?.input_tokens_remaining || 0} />
        </div>
        <div className="pt-1">
          <div className="flex items-center justify-between text-[11px] text-gray-500 mb-1">
            <span>Context window usage</span>
            <span>
              {(insights?.context_window?.input_tokens_used || 0)} / {(insights?.context_window?.context_window_tokens || 0)}
            </span>
          </div>
          <div className="w-full h-2 rounded bg-gray-100 overflow-hidden">
            <div
              className="h-full bg-indigo-500"
              style={{ width: `${Math.min(100, Math.max(0, Number(insights?.context_window?.input_fulfillment_pct || 0))) || 0}%` }}
            />
          </div>
        </div>
      </div>

      <div className="border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
          <button
            type="button"
            onClick={() => setActiveTab('process')}
            className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
              activeTab === 'process'
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <RefreshCw className="w-4 h-4 mr-2" />
            Process
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('insights')}
            className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
              activeTab === 'insights'
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <MessageSquare className="w-4 h-4 mr-2" />
            Insights
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('logs')}
            className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
              activeTab === 'logs'
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <FileText className="w-4 h-4 mr-2" />
            Logs
          </button>
        </nav>
      </div>

      {activeTab === 'process' && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-sm font-semibold text-gray-800 mb-3">Process Graph</div>
          <ProcessGraph messageRuns={insights.message_runs || []} />
        </div>
      )}

      {activeTab === 'insights' && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-3">
              <MessageSquare className="w-4 h-4 text-indigo-500" />
              Message History
            </div>
            {(insights.messages || []).length === 0 ? (
              <p className="text-xs text-gray-500 italic">No chat messages captured.</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-auto">
                {insights.messages.map((m, idx) => (
                  <div key={idx} className="text-xs rounded border border-gray-100 bg-gray-50 p-2">
                    <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">{m.role}</div>
                    <div className="whitespace-pre-wrap text-gray-700">{m.content}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-3">
              <Wrench className="w-4 h-4 text-indigo-500" />
              Tool Activity
            </div>
            {(insights.tools || []).length === 0 ? (
              <p className="text-xs text-gray-500 italic">No tools captured.</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-auto">
                {insights.tools.map((t, idx) => (
                  <div key={idx} className="text-xs rounded border border-gray-100 bg-gray-50 p-2">
                    <div className="font-medium text-gray-800">Step {t.step || idx + 1}: {t.tool || 'tool'}</div>
                    {t.input && <div className="font-mono text-[11px] text-gray-600 mt-1">{t.input}</div>}
                    {t.output && <div className="font-mono text-[11px] text-emerald-700 mt-1">{t.output}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-3">
              <Bot className="w-4 h-4 text-indigo-500" />
              Thinking Process
            </div>
            {(insights.thinking || []).length === 0 ? (
              <p className="text-xs text-gray-500 italic">No process trace captured.</p>
            ) : (
              <div className="space-y-2 max-h-80 overflow-auto">
                {insights.thinking.map((line, idx) => (
                  <div key={idx} className="text-xs text-gray-700 rounded border border-gray-100 bg-gray-50 p-2">{line}</div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'logs' && (
        <div className="bg-black rounded-xl border border-gray-800 overflow-hidden">
          <div className="px-4 py-2 bg-gray-900 text-sm text-gray-300 font-medium flex items-center gap-2">
            <FileText className="w-4 h-4" />
            Logs
          </div>
          <div className="p-4 max-h-[460px] overflow-auto">
            <pre className="text-xs leading-5 font-mono whitespace-pre-wrap break-words text-green-400">
              {logs || '(no logs)'}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
