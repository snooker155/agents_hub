import React, { useEffect, useState, useCallback } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { ChevronLeft, Loader, RefreshCw, MessageSquare, ScrollText, Bot, FileText, Workflow, Square, Globe, CheckCircle, XCircle, Clock, AlertCircle, Repeat, FlaskConical } from 'lucide-react';

import { getMessage, getMessageLogs, getMessageInsights, getMessageLive, stopMessage, replayRun, getEvalSets, createEvalSet, addEvalCase } from '../api';
import LiveRunStream from '../components/LiveRunStream';
import { useChannel } from '../components/stream';
import { TokenPill } from '../components/ProcessGraph';
import MessageProcessFlow from '../components/MessageProcessFlow';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
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

// Splits the LangChain-serialized prompt string into role-tagged segments so
// the input-context log can show clear dividers between the init/system prompt,
// any prior conversation history, and the current human message. LangChain
// stringifies chat messages with line-leading role prefixes (e.g. "System:",
// "Human:", "AI:"), which we use as segment boundaries.
function parseInputContext(text) {
  if (!text) return [];
  const ROLE_RE = /^(System|Human|AI|Assistant|Tool|Function):\s?/;
  const lines = String(text).split('\n');
  const segments = [];
  let current = null;
  for (const line of lines) {
    const match = line.match(ROLE_RE);
    if (match) {
      if (current) segments.push(current);
      current = { role: match[1], content: line.slice(match[0].length) };
    } else if (current) {
      current.content += `\n${line}`;
    } else {
      // Leading text before any role marker — treat as a raw preamble.
      current = { role: 'Prompt', content: line };
    }
  }
  if (current) segments.push(current);
  return segments;
}

// One labeled, divider-separated block in the input-context log.
function ContextSegment({ label, color, content }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className={`px-4 py-2 border-b border-gray-100 bg-gray-50 text-[11px] font-semibold uppercase tracking-wide ${color}`}>
        {label}
      </div>
      <pre className="px-4 py-3 text-xs leading-5 whitespace-pre-wrap break-words text-gray-700">
        {content || '(empty)'}
      </pre>
    </div>
  );
}

const ROLE_LABELS = {
  system: { labelKey: 'messageDetails.roles.system', color: 'text-sky-600' },
  user: { labelKey: 'messageDetails.roles.user', color: 'text-sky-600' },
  assistant: { labelKey: 'messageDetails.roles.assistant', color: 'text-sky-600' },
  tool: { labelKey: 'messageDetails.roles.tool', color: 'text-sky-600' },
};

// The chat pipeline folds prior turns into a single prompt string
// (build_chat_context): a "Conversation history:" block of "User:"/"Assistant:"
// lines followed by a "Latest user message:" section. When that string lands in
// `user_message` with no separate `history`, the whole block would otherwise be
// shown as one user message. Split it back into history turns + the real latest
// message so the divider view matches what was actually sent.
const HISTORY_HEADER = 'Conversation history:';
const LATEST_MARKER = 'Latest user message:';

function splitEmbeddedHistory(userMessage) {
  const text = String(userMessage || '');
  const headerIdx = text.indexOf(HISTORY_HEADER);
  const latestIdx = text.indexOf(LATEST_MARKER);
  if (headerIdx === -1 || latestIdx === -1 || latestIdx < headerIdx) {
    return { history: [], userMessage: text };
  }
  const block = text.slice(headerIdx + HISTORY_HEADER.length, latestIdx);
  const latest = text.slice(latestIdx + LATEST_MARKER.length).replace(/^\n/, '');

  const history = [];
  let current = null;
  for (const line of block.split('\n')) {
    const match = line.match(/^(User|Assistant):\s?/);
    if (match) {
      if (current) history.push(current);
      current = {
        role: match[1] === 'User' ? 'user' : 'assistant',
        content: line.slice(match[0].length),
      };
    } else if (current) {
      current.content += `\n${line}`;
    }
  }
  if (current) history.push(current);
  // Trim trailing blank lines accumulated from the block separators.
  history.forEach((h) => { h.content = h.content.replace(/\s+$/, ''); });
  return { history, userMessage: latest.trimEnd() };
}

// Renders the structured input context object stored on new runs:
// {system_prompt, history:[{role,content}], user_message, response,
//  llm_invocations:[{kind, ...}]}. Each field gets a labeled divider.
function StructuredContextView({ struct, output }) {
  const { t } = useI18n();
  let history = Array.isArray(struct?.history) ? struct.history : [];
  let userMessage = struct?.user_message;
  // Recover prior turns folded into the prompt string by the chat pipeline.
  if (history.length === 0 && typeof userMessage === 'string') {
    const split = splitEmbeddedHistory(userMessage);
    history = split.history;
    userMessage = split.userMessage;
  }
  const invocations = Array.isArray(struct?.llm_invocations) ? struct.llm_invocations : [];
  const response = struct?.response || output || '';
  return (
    <div className="space-y-3">
      <ContextSegment label={t('messageDetails.initPromptSystem')} color="text-amber-600" content={struct?.system_prompt} />
      {history.map((h, idx) => {
        const known = ROLE_LABELS[String(h?.role || '').toLowerCase()];
        const meta = known
          ? { label: t(known.labelKey), color: known.color }
          : { label: t('messageDetails.roles.other', { role: h?.role || '?' }), color: 'text-sky-600' };
        return <ContextSegment key={idx} label={meta.label} color={meta.color} content={h?.content} />;
      })}
      <ContextSegment label={t('messageDetails.currentUserMessage')} color="text-emerald-600" content={userMessage} />
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <div className="px-4 py-2 border-b border-gray-100 bg-gray-50 text-[11px] font-semibold uppercase tracking-wide text-indigo-600">
          {t('messageDetails.generatedResponse')}
        </div>
        <pre className="px-4 py-3 text-xs leading-5 whitespace-pre-wrap break-words text-gray-800">
          {response || t('messageDetails.empty')}
        </pre>
      </div>
      {invocations.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-100 bg-gray-50 text-[11px] font-semibold uppercase tracking-wide text-fuchsia-600">
            {t('messageDetails.llmInvocations')} ({invocations.length})
          </div>
          <div className="px-4 py-3 space-y-1">
            {invocations.map((inv, idx) => {
              const tu = inv?.token_usage || {};
              // A tool-driven call is only interesting for *which* tool drove
              // it — "tool-driven LLM call" repeated eight times says nothing
              // a reader can follow. The names are the tools whose output this
              // call was handed, so the list reads as the loop it was.
              const tools = (Array.isArray(inv?.tools) ? inv.tools : []).filter(Boolean);
              const driven = inv?.kind === 'tool';
              return (
                <div key={idx} className="text-[11px] text-gray-400 flex items-center gap-2 flex-wrap">
                  <span className="text-gray-500">#{idx + 1}</span>
                  {tools.length > 0 ? (
                    <span className="font-mono font-semibold text-orange-600 break-all">
                      {tools.join(' · ')}
                    </span>
                  ) : (
                    <span className={`font-semibold ${driven ? 'text-orange-600' : 'text-cyan-600'}`}>
                      {driven ? t('messageDetails.toolDrivenLlmCall') : t('messageDetails.llmCall')}
                    </span>
                  )}
                  <span className="text-gray-500">
                    {tu.inbound_tokens || 0} in / {tu.outbound_tokens || 0} out
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// Renders the message input context with dividers between the init/system
// prompt, conversation history, the human message, and the generated response.
// Prefers the structured object; falls back to parsing the legacy string.
function InputContextView({ struct, text, output }) {
  const { t } = useI18n();
  if (struct && typeof struct === 'object') {
    return <StructuredContextView struct={struct} output={output} />;
  }
  const segments = parseInputContext(text);

  // The first System segment is the init prompt; any further System/AI/Tool
  // segments before the final Human message are prior conversation history.
  const lastHumanIdx = segments.map((s) => s.role).lastIndexOf('Human');
  const labelFor = (seg, idx) => {
    if (seg.role === 'System') {
      return idx === 0
        ? { label: t('messageDetails.initPromptSystem'), color: 'text-amber-600' }
        : { label: t('messageDetails.roles.system'), color: 'text-sky-600' };
    }
    if (seg.role === 'Human') {
      return idx === lastHumanIdx
        ? { label: t('messageDetails.humanMessage'), color: 'text-emerald-600' }
        : { label: t('messageDetails.roles.human'), color: 'text-sky-600' };
    }
    if (seg.role === 'AI' || seg.role === 'Assistant') {
      return { label: t('messageDetails.roles.assistant'), color: 'text-sky-600' };
    }
    if (seg.role === 'Tool' || seg.role === 'Function') {
      return { label: t('messageDetails.roles.tool'), color: 'text-sky-600' };
    }
    return { label: seg.role, color: 'text-gray-600' };
  };

  return (
    <div className="space-y-3">
      {segments.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
          <pre className="px-4 py-3 text-xs leading-5 whitespace-pre-wrap break-words text-gray-700">
            {text || t('messageDetails.noInputContext')}
          </pre>
        </div>
      ) : (
        segments.map((seg, idx) => {
          const { label, color } = labelFor(seg, idx);
          return <ContextSegment key={idx} label={label} color={color} content={seg.content} />;
        })
      )}
      {output ? (
        <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-100 bg-gray-50 text-[11px] font-semibold uppercase tracking-wide text-indigo-600">
            {t('messageDetails.generatedResponse')}
          </div>
          <pre className="px-4 py-3 text-xs leading-5 whitespace-pre-wrap break-words text-gray-800">
            {output}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

export default function MessageDetails() {
  const { t } = useI18n();
  const { runId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState(null);
  const [insights, setInsights] = useState({ tools: [], thinking: [] });
  const [logs, setLogs] = useState('');
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('insights');
  // What this run has produced so far, when it is still producing it.
  const [liveTurn, setLiveTurn] = useState(null);
  const [stopping, setStopping] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [replayModel, setReplayModel] = useState('');
  const [replayResult, setReplayResult] = useState(null);
  const [replayError, setReplayError] = useState('');

  // "Save as eval case" — seeding an eval dataset from real traffic is the
  // cheapest way to build one, so the button lives next to Replay rather than
  // requiring a trip to the Evals page to type the input back in by hand.
  const [caseOpen, setCaseOpen] = useState(false);
  const [evalSets, setEvalSets] = useState([]);
  const [caseTarget, setCaseTarget] = useState('');
  const [newSetName, setNewSetName] = useState('');
  const [caseSaving, setCaseSaving] = useState(false);
  const [caseMessage, setCaseMessage] = useState('');

  const load = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setError('');
    try {
      // The live tail is fetched with everything else, not after it: the panel
      // below must have what already streamed *before* it subscribes, or the
      // text it shows starts in the middle of a word (see components/LiveRunStream).
      const [msgRes, insightsRes, logsRes, liveRes] = await Promise.all([
        getMessage(runId),
        getMessageInsights(runId),
        getMessageLogs(runId),
        getMessageLive(runId).catch(() => ({ data: { turn: null } })),
      ]);
      setMessage(msgRes.data || null);
      setInsights(insightsRes.data || { tools: [], thinking: [] });
      setLogs(logsRes.data?.logs || '');
      setLiveTurn(liveRes.data?.turn || null);
    } catch (err) {
      setError(err.response?.data?.detail || t('messageDetails.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [runId, t]);

  useEffect(() => { load(); }, [load]);

  // While the run is going, the panel above is the page's live half. When it
  // ends, the record is what should be read, so the page reloads and the panel
  // gives way to the tabs rather than sitting there as a second copy.
  const isLive = message?.status === 'running' || liveTurn?.status === 'running';
  useChannel(isLive && message?.session_id ? message.session_id : null, (ev) => {
    if (!ev || String(ev.run_id || '') !== String(runId)) return;
    if (ev.type === 'done' || ev.type === 'session_done') load();
  });

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

  const handleReplay = async () => {
    setReplaying(true);
    setReplayError('');
    setReplayResult(null);
    try {
      const body = replayModel.trim() ? { model: replayModel.trim() } : {};
      const { data } = await replayRun(runId, body);
      setReplayResult(data);
    } catch (err) {
      setReplayError(err.response?.data?.detail || t('messageDetails.replayFailed'));
    } finally {
      setReplaying(false);
    }
  };

  const openCasePanel = async () => {
    const next = !caseOpen;
    setCaseOpen(next);
    setCaseMessage('');
    if (next) {
      try {
        const { data } = await getEvalSets(message?.workspace);
        setEvalSets(data.eval_sets || []);
        setCaseTarget(data.eval_sets?.[0]?.eval_set_id || '');
      } catch {
        setEvalSets([]);
      }
    }
  };

  const handleSaveAsCase = async () => {
    setCaseSaving(true);
    setCaseMessage('');
    try {
      let targetId = caseTarget;
      if (!targetId) {
        if (!newSetName.trim()) {
          setCaseMessage(t('messageDetails.pickEvalSet'));
          return;
        }
        const { data } = await createEvalSet({
          name: newSetName.trim(),
          workspace: message?.workspace || null,
          agent_id: message?.agent_id || null,
          graders: [{ kind: 'substring', params: {}, weight: 1 }],
        });
        targetId = data.eval_set_id;
      }
      // The run's own output becomes `expected`, which makes the first eval a
      // pure regression check: does this still do what it did.
      await addEvalCase(targetId, { from_run_id: runId });
      setCaseMessage(t('messageDetails.savedAsEvalCase'));
      setNewSetName('');
    } catch (err) {
      setCaseMessage(err.response?.data?.detail || t('messageDetails.saveCaseFailed'));
    } finally {
      setCaseSaving(false);
    }
  };

  if (loading) {
    return <div className="flex justify-center py-20"><Loader className="w-6 h-6 animate-spin text-indigo-500" /></div>;
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link to="/messages" className="inline-flex items-center gap-1 text-sm text-indigo-600 hover:underline">
          <ChevronLeft className="w-4 h-4" /> {t('messageDetails.backToMessages')}
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
  // Runs belonging to this message, rendered as the same execution flow the
  // Chat process panel shows. Older records may lack run_id — show everything
  // the insights endpoint returned in that case.
  const matchedRuns = runs.filter((r) => String(r?.run_id || '') === String(runId || ''));
  const flowRuns = matchedRuns.length > 0 ? matchedRuns : runs;
  const rawInvoke = (insights?.llm_invoke_responses || []).filter(
    (entry) => String(entry?.run_id || '') === String(runId || '')
  );
  const inputContexts = (insights?.input_contexts || []).filter(
    (entry) => String(entry?.run_id || '') === String(runId || '')
  );
  const inputContextStruct = inputContexts.length > 0 ? inputContexts[0]?.structured : null;
  const inputContextText = inputContexts.length > 0
    ? String(inputContexts[0]?.context || '')
    : (messageInput || t('messageDetails.noInputContext'));
  const runLogs = logs || insights?.aggregated_logs || '(no logs)';
  const responseJson = Array.isArray(rawInvoke) && rawInvoke.length > 0
    ? (rawInvoke.length === 1 ? rawInvoke[0] : rawInvoke)
    : { message: t('messageDetails.noRawResponse') };

  return (
    <PageContainer fill className="gap-4">
      <PageHeader
        className="mb-0"
        icon={ScrollText}
        title={t('messageDetails.messageDetails')}
        backTo="/messages"
        backLabel={t('messageDetails.messages')}
        badges={message?.is_flow && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-violet-100 text-violet-700">
            <Workflow className="w-3.5 h-3.5" /> {t('messageDetails.flow2')}
          </span>
        )}
        actions={<>
          {message?.session_id && (
            <button
              onClick={() => navigate(`/sessions/${message.session_id}`)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-indigo-200 rounded-lg text-indigo-600 hover:bg-indigo-50"
            >
              {t('messageDetails.viewSession')}
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
          {message?.channel !== 'replay' && (message?.status === 'completed' || message?.status === 'failed') && (
            <button
              onClick={() => setReplayOpen((v) => !v)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-indigo-200 rounded-lg text-indigo-600 hover:bg-indigo-50"
            >
              <Repeat className="w-4 h-4" />
              {t('messageDetails.replay')}
            </button>
          )}
          {message?.channel !== 'replay' && message?.channel !== 'eval'
            && (message?.status === 'completed' || message?.status === 'failed') && (
            <button
              onClick={openCasePanel}
              title={t('messageDetails.addThisRunToAn')}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-indigo-200 rounded-lg text-indigo-600 hover:bg-indigo-50"
            >
              <FlaskConical className="w-4 h-4" />
              {t('messageDetails.saveAsEvalCase')}
            </button>
          )}
          <button
            onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('messageDetails.refresh')}
          </button>
        </>}
      />

      {/* Metadata card */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 shrink-0">

      {/* Save-as-eval-case panel */}
      {caseOpen && (
        <div className="mt-4 border border-indigo-100 bg-indigo-50/40 rounded-xl p-4 space-y-3">
          <div className="text-sm font-semibold text-gray-700">{t('messageDetails.saveThisRunAsAn')}</div>
          <p className="text-xs text-gray-500">
            {t('messageDetails.saveCaseHint')}
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={caseTarget}
              onChange={(e) => setCaseTarget(e.target.value)}
              className="border border-gray-300 rounded-lg px-2 py-1 text-sm w-64"
            >
              <option value="">{t('messageDetails.newEvalSet')}</option>
              {evalSets.map((s2) => (
                <option key={s2.eval_set_id} value={s2.eval_set_id}>{s2.name}</option>
              ))}
            </select>
            {!caseTarget && (
              <input
                value={newSetName}
                onChange={(e) => setNewSetName(e.target.value)}
                placeholder={t('messageDetails.newEvalSetName')}
                className="border border-gray-300 rounded-lg px-2 py-1 text-sm w-56"
              />
            )}
            <button
              onClick={handleSaveAsCase}
              disabled={caseSaving}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {caseSaving ? <Loader className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />}
              Save case
            </button>
            {caseMessage && (
              <span className={`text-xs ${caseMessage.startsWith('Saved') ? 'text-green-600' : 'text-red-600'}`}>
                {caseMessage}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Replay / regression panel */}
      {replayOpen && (
        <div className="mt-4 border border-indigo-100 bg-indigo-50/40 rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-gray-700">{t('messageDetails.replayThisRun')}</span>
            <input
              value={replayModel}
              onChange={(e) => setReplayModel(e.target.value)}
              placeholder={`Model override (default: ${message?.model || 'same'})`}
              className="border border-gray-300 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none w-72"
            />
            <button
              onClick={handleReplay}
              disabled={replaying}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {replaying ? <Loader className="w-4 h-4 animate-spin" /> : <Repeat className="w-4 h-4" />}
              {replaying ? t('common.running') : t('messageDetails.runReplay')}
            </button>
            <span className="text-xs text-gray-500">{t('messageDetails.reInvokesTheAgentOn')}</span>
          </div>

          {replayError && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-2 text-sm">{replayError}</div>}

          {replayResult && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 text-sm">
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${replayResult.identical ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                  {replayResult.identical ? <CheckCircle className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                  {replayResult.identical ? t('messageDetails.outputIdentical') : t('messageDetails.outputDiffers')}
                </span>
                {!replayResult.replay?.ok && <span className="text-xs text-red-600">{t('messageDetails.replayFailed')}: {replayResult.replay?.error}</span>}
                <Link to={`/messages/${replayResult.replay_run_id}`} className="text-xs text-indigo-600 hover:underline ml-auto">{t('messageDetails.openReplayRun')}</Link>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="bg-white border border-gray-200 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">{t('messageDetails.original')} · {replayResult.original?.model || '—'} · ${Number(replayResult.original?.cost || 0).toFixed(4)}</div>
                  <pre className="text-xs whitespace-pre-wrap text-gray-800 max-h-64 overflow-auto">{replayResult.original?.text || t('messageDetails.empty')}</pre>
                </div>
                <div className="bg-white border border-gray-200 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">{t('messageDetails.replay')} · {replayResult.replay?.model || '—'} · ${Number(replayResult.replay?.cost || 0).toFixed(4)} · {replayResult.replay?.duration_ms || 0}ms</div>
                  <pre className="text-xs whitespace-pre-wrap text-gray-800 max-h-64 overflow-auto">{replayResult.replay?.text || t('messageDetails.empty')}</pre>
                </div>
              </div>
              {replayResult.diff && (
                <pre className="text-xs font-mono whitespace-pre-wrap bg-gray-900 text-gray-100 rounded-lg p-3 max-h-64 overflow-auto">{replayResult.diff}</pre>
              )}
            </div>
          )}
        </div>
      )}

      {/* Metadata */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-sm">
        <div><span className="text-gray-500">{t('messageDetails.runId')}</span> <span className="text-xs">{message?.run_id}</span></div>
        <div><span className="text-gray-500">{t('messageDetails.title')}</span> <span className="font-medium text-gray-800">{message?.task_title || message?.title || '—'}</span></div>
        <div><span className="text-gray-500">{t('messageDetails.agent')}</span> <span className="text-xs">{message?.agent_id || '—'}</span></div>
        <div><span className="text-gray-500">{t('messageDetails.model')}</span> <span className="text-xs">{message?.model || insights?.model || '—'}</span></div>
        <div className="flex items-center gap-2"><span className="text-gray-500">{t('messageDetails.status')}</span> <StatusBadge status={message?.status || 'pending'} /></div>
        {message?.session_id && (
          <div>
            <span className="text-gray-500">{t('messageDetails.session')}</span>{' '}
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
            <span className="text-gray-500">{t('messageDetails.flowRun')}</span>{' '}
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
            <span className="text-gray-500">{t('messageDetails.flow')}</span>{' '}
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
            <span className="text-gray-500">{t('messageDetails.flowNode')}</span>{' '}
            <span className="text-xs text-gray-700">{message.flow_node_label}</span>
            {message?.flow_node_id && (
              <span className="text-xs text-gray-400 ml-1">({message.flow_node_id})</span>
            )}
          </div>
        )}
        {/* A simulation turn: which scenario, which role, which tick. Without
            these a sim run reads as an unattached agent call. */}
        {message?.sim_run_id && (
          <div>
            <span className="text-gray-500">{t('messageDetails.simulation')}</span>{' '}
            {message?.scenario_id ? (
              <button
                onClick={() => navigate(`/playground/${message.scenario_id}`)}
                className="text-xs text-fuchsia-600 hover:underline"
              >
                {message.sim_role || message.sim_run_id}
              </button>
            ) : (
              <span className="text-xs text-gray-700">{message.sim_role || message.sim_run_id}</span>
            )}
            {message?.tick != null && (
              <span className="text-xs text-gray-400 ml-1">
                {t('messageDetails.simTick', { tick: message.tick })}
              </span>
            )}
          </div>
        )}
        {message?.session_type === 'http' && (
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-100 text-cyan-700">
              <Globe className="w-3 h-3" />
              {t('messageDetails.externalHttpRun')}
            </span>
          </div>
        )}
        {message?.session_type === 'chat' && (
          <div>
            <span className="text-gray-500">{t('messageDetails.conversationId')}</span>{' '}
            <span className="text-xs">{message?.task_id || insights?.session_task_id || '—'}</span>
          </div>
        )}
        <div><span className="text-gray-500">{t('messageDetails.workspace')}</span> {message?.workspace || '—'}</div>
        <div><span className="text-gray-500">{t('messageDetails.started')}</span> {fmtDate(message?.started_at)}</div>
        <div><span className="text-gray-500">{t('messageDetails.finished')}</span> {fmtDate(message?.finished_at)}</div>
        <div><span className="text-gray-500">{t('messageDetails.duration')}</span> {duration(message?.started_at, message?.finished_at)}</div>
        <div><span className="text-gray-500">{t('messageDetails.error')}</span> {message?.error || '—'}</div>
        <div className="flex flex-wrap gap-2 pt-1 md:col-span-2">
          <TokenPill label={t('messageDetails.in')} value={insights?.token_usage?.inbound_tokens || 0} />
          <TokenPill label={t('messageDetails.out')} value={insights?.token_usage?.outbound_tokens || 0} />
          <TokenPill label={t('messageDetails.total')} value={insights?.token_usage?.total_tokens || 0} />
          {/* The three pills above are the run's token bill, summed over every
              step of the agent loop. This one is the largest single prompt it
              sent, the figure the context window has to hold. */}
          {(insights?.context_window?.input_tokens_used || 0) > 0 && (
            <TokenPill
              label={t('messageDetails.ctxPeak')}
              value={
                insights.context_window.context_window_tokens > 0
                  ? `${insights.context_window.input_tokens_used} / ${insights.context_window.context_window_tokens}`
                  : insights.context_window.input_tokens_used
              }
              title={t('messageDetails.ctxPeakTooltip')}
            />
          )}
        </div>
        {/* Service entities this run touched — the same links the chat reply
            shows, kept with the run record (see common/entity_links.py). */}
        {!!(insights?.entities || []).length && (
          <div className="flex flex-wrap items-center gap-1.5 pt-1 md:col-span-2">
            <span className="text-gray-500">{t('messageDetails.entitiesTouched')}</span>
            {insights.entities.map((e) => (
              <Link
                key={`${e.kind}:${e.id}`}
                to={e.url}
                title={t(`chat.entityKind.${e.kind}`, { defaultValue: e.noun || e.kind })}
                className="inline-flex items-center gap-1 max-w-full rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600 transition-colors hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700"
              >
                <span aria-hidden="true">{e.icon}</span>
                <span className="truncate max-w-[14rem] font-medium">{e.title}</span>
                <span className="text-gray-400">{t(`chat.entityAction.${e.action}`, { defaultValue: e.action })}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
      </div>

      {/* Generation, as it happens.

          A finished run answers from its record — the log, the payloads, the
          process graph. A running one has none of that yet, so this page used to
          show a spinning badge and an empty log until the run ended. The panel
          starts from the server's live tail and follows the run's session
          channel from there, and the page reloads itself once the run is over so
          the record takes over from the stream. */}
      {isLive && message?.session_id && (
        <LiveRunStream
          sessionId={message.session_id}
          runId={runId}
          seed={liveTurn}
          title={t('messageDetails.liveGeneration')}
          className="mb-4 shrink-0"
        />
      )}

      {/* Tabs + content */}
      <div className="flex-1 min-h-0 flex flex-col">
      <div className="border-b border-gray-200 shrink-0">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {[
            { id: 'insights', icon: MessageSquare, label: t('messageDetails.tabs.insights') },
            { id: 'input_context', icon: MessageSquare, label: t('messageDetails.tabs.inputContext') },
            { id: 'response', icon: Bot, label: t('messageDetails.tabs.response') },
            { id: 'logs', icon: FileText, label: t('messageDetails.tabs.logs') },
          ].map(tab => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center px-4 py-2 first:pl-0 text-sm font-semibold border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
              aria-current={activeTab === tab.id ? 'page' : undefined}
            >
              <tab.icon className="w-4 h-4 mr-2" />
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === 'insights' && (
        <div className="bg-white border border-gray-200 rounded-xl flex-1 min-h-0 overflow-y-auto">
          {/* Flat process flow: pinned input + expandable step nodes. */}
          <MessageProcessFlow
            key={flowRuns.map((mr, idx) => `${mr?.message_id || idx}`).join('|')}
            runs={flowRuns}
          />
        </div>
      )}

      {activeTab === 'logs' && (
        <div className="bg-black rounded-xl border border-gray-800 overflow-hidden flex-1 min-h-0 flex flex-col">
          <div className="px-4 py-2 bg-gray-900 text-sm text-gray-300 font-medium flex items-center gap-2 shrink-0">
            <FileText className="w-4 h-4" />
            {t('messageDetails.logs')}
          </div>
          <div className="p-4 flex-1 min-h-0 overflow-auto">
            <pre className="text-xs leading-5 whitespace-pre-wrap break-words text-green-400">
              {runLogs}
            </pre>
          </div>
        </div>
      )}

      {activeTab === 'response' && (
        <div className="bg-black rounded-xl border border-gray-800 overflow-hidden flex-1 min-h-0 flex flex-col">
          <div className="px-4 py-2 bg-gray-900 text-sm text-gray-300 font-medium flex items-center gap-2 shrink-0">
            <Bot className="w-4 h-4" />
            {t('messageDetails.rawLlmInvokeResponse')}
          </div>
          <div className="p-4 flex-1 min-h-0 overflow-auto relative">
            <pre className="text-xs leading-5 whitespace-pre-wrap break-words text-green-400">
              {JSON.stringify(responseJson, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {activeTab === 'input_context' && (
        /* pb-6 is load-bearing: padding on a scroll container counts toward its
           scrollHeight, so the last card can be scrolled fully into view. With
           no padding it ended exactly on the clip edge and its bottom border
           was unreachable — which read as the block being cut off. */
        <div className="flex-1 min-h-0 overflow-y-auto pt-4 pb-6">
          <InputContextView struct={inputContextStruct} text={inputContextText} output={messageOutput} />
        </div>
      )}
      </div>
    </PageContainer>
  );
}
