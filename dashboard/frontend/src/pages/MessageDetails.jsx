import React, { useEffect, useState, useCallback } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { ChevronLeft, Loader, RefreshCw, MessageSquare, Wrench, Bot, FileText, Copy, Check, Workflow, Square, Globe, Zap } from 'lucide-react';

const SKILL_TOOL = 'get_skill';
import { getMessage, getMessageLogs, getMessageInsights, stopMessage } from '../api';

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

function TokenPill({ label, value }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-medium">
      {label}: {value ?? 0}
    </span>
  );
}

function shortText(v, max = 180) {
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
        } else {
          break;
        }
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

export default function MessageDetails() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState(null);
  const [insights, setInsights] = useState({ tools: [], thinking: [] });
  const [logs, setLogs] = useState('');
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('insights');
  const [stopping, setStopping] = useState(false);

  const load = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setError('');
    try {
      const [msgRes, insightsRes, logsRes] = await Promise.all([
        getMessage(runId),
        getMessageInsights(runId),
        getMessageLogs(runId),
      ]);
      setMessage(msgRes.data || null);
      setInsights(insightsRes.data || { tools: [], thinking: [] });
      setLogs(logsRes.data?.logs || '');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load message details');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => { load(); }, [load]);

  const handleStop = async () => {
    setStopping(true);
    try {
      await stopMessage(runId);
      await load();
    } catch (err) {
      console.error('Failed to stop message', err);
    } finally {
      setStopping(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-20"><Loader className="w-6 h-6 animate-spin text-indigo-500" /></div>;
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link to="/messages" className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline">
          <ChevronLeft className="w-4 h-4" /> Back to messages
        </Link>
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 text-sm">{error}</div>
      </div>
    );
  }

  // Extract the selected message's input/output by exact run_id match.
  const runs = insights?.message_runs || [];
  const selectedRun = runs.find((r) => String(r?.run_id || '') === String(runId || '')) || null;
  const messageInput = selectedRun?.input || '';
  const messageOutput = selectedRun?.output || '';
  const toolsForRun = (insights?.tools || []).filter(
    (t) => String(t?.run_id || '') === String(runId || '')
  );
  const thinkingForRun = (selectedRun?.thinking || []).filter(Boolean);
  const rawInvoke = (insights?.llm_invoke_responses || []).filter(
    (entry) => String(entry?.run_id || '') === String(runId || '')
  );
  const inputContexts = (insights?.input_contexts || []).filter(
    (entry) => String(entry?.run_id || '') === String(runId || '')
  );
  const inputContextText = inputContexts.length > 0
    ? String(inputContexts[0]?.context || '')
    : (messageInput || '(No input context captured for this run.)');
  const runLogs = logs || insights?.aggregated_logs || '(no logs)';
  const responseJson = Array.isArray(rawInvoke) && rawInvoke.length > 0
    ? (rawInvoke.length === 1 ? rawInvoke[0] : rawInvoke)
    : { message: 'No raw LLM invoke response captured for this run.' };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/messages" className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline">
            <ChevronLeft className="w-4 h-4" /> Back to messages
          </Link>
          <div className="flex items-center gap-3 mt-2">
            <h1 className="text-2xl font-bold text-gray-900">Message Details</h1>
            {message?.is_flow && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-violet-100 text-violet-700">
                <Workflow className="w-3.5 h-3.5" />
                Flow
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {message?.session_id && (
            <button
              onClick={() => navigate(`/sessions/${message.session_id}`)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-indigo-200 rounded-lg text-indigo-600 hover:bg-indigo-50"
            >
              View Session
            </button>
          )}
          {message?.status === 'running' && (
            <button
              onClick={handleStop}
              disabled={stopping}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-red-200 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-40"
            >
              {stopping ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
              Stop
            </button>
          )}
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Metadata card */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-2 text-sm">
        <div><span className="text-gray-500">Run ID:</span> <span className="text-xs">{message?.run_id}</span></div>
        <div><span className="text-gray-500">Title:</span> <span className="font-medium text-gray-800">{message?.task_title || message?.title || '—'}</span></div>
        <div><span className="text-gray-500">Agent:</span> <span className="text-xs">{message?.agent_id || '—'}</span></div>
        <div><span className="text-gray-500">Model:</span> <span className="text-xs">{message?.model || insights?.model || '—'}</span></div>
        <div><span className="text-gray-500">Status:</span> <span className="font-medium">{message?.status || '—'}</span></div>
        {message?.session_id && (
          <div>
            <span className="text-gray-500">Session:</span>{' '}
            <button
              onClick={() => navigate(`/sessions/${message.session_id}`)}
              className="text-xs text-indigo-600 hover:underline"
            >
              {message.session_id}
            </button>
          </div>
        )}
        {message?.flow_run_id && (
          <div>
            <span className="text-gray-500">Flow Run:</span>{' '}
            <button
              onClick={() => navigate(`/messages/${message.flow_run_id}`)}
              className="text-xs text-indigo-600 hover:underline"
            >
              {message.flow_run_id}
            </button>
          </div>
        )}
        {message?.flow_id && (
          <div>
            <span className="text-gray-500">Flow:</span>{' '}
            <button
              onClick={() => navigate(`/flows/${message.flow_id}`)}
              className="text-xs text-violet-600 hover:underline"
            >
              {message.flow_id}
            </button>
          </div>
        )}
        {message?.flow_node_label && (
          <div>
            <span className="text-gray-500">Flow Node:</span>{' '}
            <span className="text-xs text-gray-700">{message.flow_node_label}</span>
            {message?.flow_node_id && (
              <span className="text-xs text-gray-400 ml-1">({message.flow_node_id})</span>
            )}
          </div>
        )}
        {message?.session_type === 'http' && (
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-100 text-cyan-700">
              <Globe className="w-3 h-3" />
              External HTTP Run
            </span>
          </div>
        )}
        {message?.session_type === 'chat' && (
          <div>
            <span className="text-gray-500">Conversation ID:</span>{' '}
            <span className="text-xs">{message?.task_id || insights?.session_task_id || '—'}</span>
          </div>
        )}
        <div><span className="text-gray-500">Workspace:</span> {message?.workspace || '—'}</div>
        <div><span className="text-gray-500">Started:</span> {fmtDate(message?.started_at)}</div>
        <div><span className="text-gray-500">Finished:</span> {fmtDate(message?.finished_at)}</div>
        <div><span className="text-gray-500">Duration:</span> {duration(message?.started_at, message?.finished_at)}</div>
        <div><span className="text-gray-500">Error:</span> {message?.error || '—'}</div>
        <div className="flex flex-wrap gap-2 pt-1">
          <TokenPill label="in" value={insights?.token_usage?.inbound_tokens || 0} />
          <TokenPill label="out" value={insights?.token_usage?.outbound_tokens || 0} />
          <TokenPill label="total" value={insights?.token_usage?.total_tokens || 0} />
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'insights', icon: MessageSquare, label: 'Insights' },
            { id: 'input_context', icon: MessageSquare, label: 'Input Context' },
            { id: 'response', icon: Bot, label: 'Response' },
            { id: 'logs', icon: FileText, label: 'Logs' },
          ].map(tab => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              <tab.icon className="w-4 h-4 mr-2" />
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === 'insights' && (
        <div className="space-y-4">
          {/* Input / Output for this message */}
          {(messageInput || messageOutput) && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {messageInput && (
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <div className="text-sm font-semibold text-gray-800 mb-3">Input</div>
                  <div className="text-xs text-gray-700 max-h-64 overflow-auto">{renderContent(messageInput)}</div>
                </div>
              )}
              {messageOutput && (
                <div className="bg-white border border-emerald-100 rounded-xl p-4">
                  <div className="text-sm font-semibold text-gray-800 mb-3">Output</div>
                  <div className="text-xs text-gray-700 max-h-64 overflow-auto">{renderContent(messageOutput)}</div>
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {/* Tool Activity */}
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-3">
                <Wrench className="w-4 h-4 text-indigo-500" />
                Tool Activity
              </div>
              {toolsForRun.length === 0 ? (
                <p className="text-xs text-gray-500 italic">No tools captured.</p>
              ) : (
                <div className="space-y-2 max-h-80 overflow-auto">
                  {toolsForRun.map((t, idx) => {
                    if (t.tool === SKILL_TOOL) {
                      return (
                        <div key={idx} className="text-xs rounded-lg border border-violet-300 bg-violet-50 p-2">
                          <div className="flex items-center gap-1 font-semibold text-violet-800 mb-1">
                            <Zap className="w-3 h-3 text-violet-500 shrink-0" />
                            Skill Retrieved
                          </div>
                          {t.input && <div className="text-[11px] text-violet-600">{shortText(t.input, 300)}</div>}
                          {t.output && <div className="text-[11px] text-violet-900 mt-1">{shortText(t.output, 300)}</div>}
                        </div>
                      );
                    }
                    return (
                      <div key={idx} className="text-xs rounded border border-gray-100 bg-gray-50 p-2">
                        <div className="font-medium text-gray-800">
                          Step {t.step || idx + 1}: {t.tool || 'tool'}
                        </div>
                        {t.input && <div className="text-[11px] text-gray-600 mt-1">{shortText(t.input, 300)}</div>}
                        {t.output && <div className="text-[11px] text-emerald-700 mt-1">{shortText(t.output, 300)}</div>}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Thinking Process */}
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 mb-3">
                <Bot className="w-4 h-4 text-indigo-500" />
                Thinking Process
              </div>
              {thinkingForRun.length === 0 ? (
                <p className="text-xs text-gray-500 italic">No process trace captured.</p>
              ) : (
                <div className="space-y-2 max-h-80 overflow-auto">
                  {thinkingForRun.map((line, idx) => (
                    <div key={idx} className="text-xs text-gray-700 rounded border border-gray-100 bg-gray-50 p-2">{line}</div>
                  ))}
                </div>
              )}
            </div>
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
            <pre className="text-xs leading-5 whitespace-pre-wrap break-words text-green-400">
              {runLogs}
            </pre>
          </div>
        </div>
      )}

      {activeTab === 'response' && (
        <div className="bg-black rounded-xl border border-gray-800 overflow-hidden">
          <div className="px-4 py-2 bg-gray-900 text-sm text-gray-300 font-medium flex items-center gap-2">
            <Bot className="w-4 h-4" />
            Raw LLM Invoke Response
          </div>
          <div className="p-4 max-h-[460px] overflow-auto relative">
            <pre className="text-xs leading-5 whitespace-pre-wrap break-words text-green-400">
              {JSON.stringify(responseJson, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {activeTab === 'input_context' && (
        <div className="bg-black rounded-xl border border-gray-800 overflow-hidden">
          <div className="px-4 py-2 bg-gray-900 text-sm text-gray-300 font-medium flex items-center gap-2">
            <MessageSquare className="w-4 h-4" />
            Message Input Context
          </div>
          <div className="p-4 max-h-[460px] overflow-auto">
            <pre className="text-xs leading-5 whitespace-pre-wrap break-words text-green-400">
              {inputContextText}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
