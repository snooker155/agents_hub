import { useState, useEffect, useRef, useCallback } from 'react';
import { ArrowUpRight, ChevronDown, ChevronUp, FlaskConical, Repeat, Zap, Bot } from 'lucide-react';
import { SKILL_TOOL, shortText, fmtDurationMs, plainMarkdown, preview } from './processUtils';
import { toolInline } from './toolFormatters';
import ProcessNode from './ProcessNode';
import StepText from './StepText';
import SaveAsEvalCaseDialog from './evals/SaveAsEvalCaseDialog';
import { getMessageInsights } from '../api';
import { useI18n } from '../i18n';

// Shared agent-process flow renderer. Originally built for the Chat page's
// Process panel; now also drives the Task execution tab and the Message
// insights tab so every surface shows the same flow of execution.
//
// Each item in `messageRuns` is one agent invocation:
// { message_id, run_id, agent_id, timestamp, input, output,
//   tools: [{tool, input, output, step}], reasoning: [{content, step}],
//   inbound_tokens, outbound_tokens, total_tokens, tool_calls, duration_ms,
//   status?, channel?, error? }  — the last three are optional run metadata
// (present on task runs) rendered as extra badges.

export function TokenPill({ label, value, title = '' }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-medium whitespace-nowrap"
      title={title || undefined}
    >
      {label}: {value ?? 0}
    </span>
  );
}

// Run timestamps arrive as ISO strings (utc_iso) — render as local time.
// Anything Date can't parse is shown as-is.
function fmtTimestamp(ts) {
  if (!ts) return '';
  const d = new Date(String(ts));
  return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString();
}

// The tools that run another agent and return its result. Their call is shown as
// the delegated run itself, with that run's own steps inside, rather than as one
// more tool with a JSON blob for output.
const DELEGATION_TOOLS = new Set(['run_agent_tool', 'delegate_task_tool']);

// The run log keeps only the head of a long tool result, so the fields are read
// with a pattern when the JSON no longer parses. The input is logged as a
// Python dict, hence either quote.
function field(text, name) {
  const m = new RegExp(`['"]${name}['"]\\s*:\\s*['"]([^'"]+)['"]`).exec(String(text || ''));
  return m ? m[1] : '';
}

function parseDelegation(tc) {
  let data = null;
  try { data = JSON.parse(String(tc.output || '')); } catch { /* truncated in the log */ }
  const out = data && typeof data === 'object' ? data : {};
  return {
    runId: out.run_id || field(tc.output, 'run_id'),
    agent: out.agent?.name || out.agent_id || field(tc.input, 'agent_id') || field(tc.output, 'agent_id'),
    output: typeof out.output === 'string' ? out.output : '',
    failed: out.ok === false || /^\s*\{\s*"ok"\s*:\s*false/.test(String(tc.output || '')),
  };
}

// A run still in progress carries the worker's steps as they happen
// (chat/processLive.js): drawn as a run row of its own, without a fetch.
function liveRun(d) {
  return {
    message_id: d.run_id,
    run_id: d.run_id,
    agent_id: d.agent_name || d.agent_id,
    input: d.input,
    output: d.output,
    tools: d.tools || [],
    reasoning: d.reasoning || [],
    tool_calls: (d.tools || []).length,
    duration_ms: d.duration_ms,
    status: d.running ? 'running' : d.ok === false ? 'failed' : 'completed',
    error: d.error || '',
  };
}

// A delegated run inside the run that asked for it: fetched when opened, and
// drawn with the same graph, so a delegation nested in it opens the same way.
// While the turn is live it is open and grows with each step the worker takes.
function DelegatedRunNode({ tc }) {
  const { t } = useI18n();
  const live = tc.delegation || null;
  const [open, setOpen] = useState(Boolean(live?.running));
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState('');
  const parsed = parseDelegation(tc);
  const runId = parsed.runId || live?.run_id || '';
  const agentName = parsed.agent || live?.agent_name || live?.agent_id;
  const output = parsed.output || live?.output || '';
  const failed = parsed.failed || live?.ok === false;
  useEffect(() => {
    if (!open || !runId || runs || live) return undefined;
    let cancelled = false;
    getMessageInsights(runId)
      .then((r) => { if (!cancelled) setRuns(r.data?.message_runs || []); })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || t('processGraph.delegatedRunUnavailable')); });
    return () => { cancelled = true; };
  }, [open, runId, runs, live, t]);
  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-2 text-left"
      >
        {open ? <ChevronUp className="w-3.5 h-3.5 text-gray-400 shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />}
        <Repeat className="w-3.5 h-3.5 text-indigo-500 shrink-0" />
        <span className="text-[11px] font-semibold text-indigo-700 shrink-0">
          {t('processGraph.delegatedTo', { agent: agentName || '…' })}
        </span>
        {live?.running && (
          <span className="flex gap-1 shrink-0">
            {[0, 150, 300].map((d) => (
              <span key={d} className="w-1 h-1 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        )}
        {failed && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-red-100 text-red-700">{t('processGraph.failed')}</span>
        )}
        {!open && (
          <span className="text-[11px] text-gray-500 truncate">{preview(output || tc.input)}</span>
        )}
      </button>
      {open && (
        <div className="px-2.5 pb-2.5 space-y-1.5">
          {live ? (
            <ProcessGraph messageRuns={[liveRun(live)]} titleByAgent nested />
          ) : !runId ? (
            <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all">{tc.output || tc.input}</div>
          ) : error ? (
            <div className="text-[11px] text-red-600">{error}</div>
          ) : runs === null ? (
            <div className="text-[11px] text-gray-400 italic">{t('processGraph.loadingDelegatedRun')}</div>
          ) : (
            <ProcessGraph messageRuns={runs} titleByAgent nested />
          )}
        </div>
      )}
    </div>
  );
}

const RUN_STATUS_CLS = {
  running: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  error: 'bg-red-100 text-red-700',
  stopped: 'bg-gray-100 text-gray-500',
};

// `nested`: the graph of a delegated run, drawn inside its parent's step. Its
// runs hang off that step, not off a timeline of their own, so they have no
// timeline dot or connector.
export default function ProcessGraph({
  messageRuns = [], showDetailsLink = true, titleByAgent = false, evalCaseWorkspace = undefined, nested = false,
}) {
  const { t } = useI18n();
  // "To eval case" is offered where the host passes a workspace for it (the
  // Chat page's process panel); `null` is a valid "no workspace" choice.
  const showEvalCase = evalCaseWorkspace !== undefined;
  const [caseRunId, setCaseRunId] = useState(null);
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
    if (!messageRuns.length || nested) return;
    const isFirst = prevLengthRef.current === null;
    prevLengthRef.current = messageRuns.length;
    lastNodeRef.current?.scrollIntoView({ behavior: isFirst ? 'instant' : 'smooth', block: 'nearest' });
  }, [messageRuns.length, nested]);

  if (!messageRuns.length) {
    return <p className="text-xs text-gray-500 italic">{t('processGraph.noProcessMessageDataYet')}</p>;
  }
  return (
    <div className="space-y-2">
      {messageRuns.length > 1 && (
        <div className="flex items-center gap-2 pb-1">
          <button
            onClick={expandAll}
            className="text-[10px] text-indigo-600 hover:underline"
          >
            {t('processGraph.expandAll')}
          </button>
          <span className="text-gray-300 text-[10px]">·</span>
          <button
            onClick={collapseAll}
            className="text-[10px] text-gray-400 hover:underline"
          >
            {t('processGraph.collapseAll')}
          </button>
        </div>
      )}
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
            className={nested ? 'relative' : 'relative pl-4'}
          >
            {hasNext && !nested && <div className="absolute left-[7px] top-4 bottom-[-16px] w-px bg-gray-200" />}
            {!nested && <div className="absolute left-0 top-2 w-3 h-3 rounded-full bg-indigo-500" />}
            <div
              role="button"
              tabIndex={0}
              onClick={() => toggleNode(idx)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleNode(idx); } }}
              className="w-full text-left rounded-lg border border-indigo-100 bg-indigo-50 hover:bg-indigo-100 transition-colors p-3 cursor-pointer"
            >
              <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <div className="text-xs font-semibold text-indigo-800 shrink-0">#{idx + 1}</div>
                  {titleByAgent && mr.agent_id ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-indigo-600 text-white text-xs font-semibold shrink-0 max-w-full">
                      <Bot className="w-3.5 h-3.5 shrink-0" />
                      <span className="truncate">{mr.agent_id}</span>
                    </span>
                  ) : (
                    <div className="font-medium text-sm text-indigo-900 truncate">
                      {shortText(mr.input || 'Message', 100)}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {mr.status && (
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${RUN_STATUS_CLS[mr.status] || 'bg-gray-100 text-gray-500'}`}>
                      {mr.status}
                    </span>
                  )}
                  {mr.channel && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-600">
                      {mr.channel}
                    </span>
                  )}
                  <span className="text-[10px] text-indigo-600">{fmtTimestamp(mr.timestamp)}</span>
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
                  WebkitLineClamp: titleByAgent ? 1 : 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {mr.agent_id && !titleByAgent && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 text-[10px] font-medium mr-1">
                    {mr.agent_id}
                  </span>
                )}
                <span>
                  {titleByAgent
                    ? shortText(plainMarkdown(mr.output) || t('processGraph.noResponseYet'), 120)
                    : (plainMarkdown(mr.output) || t('processGraph.noResponseYet'))}
                </span>
              </div>
              <div className="flex items-center gap-1.5 mb-2 min-h-[18px] flex-wrap">
                {(() => {
                  const skillNames = toolNames.filter((n) => n === SKILL_TOOL);
                  const regularNames = toolNames.filter((n) => n !== SKILL_TOOL);
                  return (
                    <>
                      {skillNames.length > 0 && (
                        <>
                          <span className="text-[10px] text-violet-600 font-medium">{t('processGraph.skill')}</span>
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
                          <span className="text-[10px] text-indigo-700 font-medium">{t('processGraph.tools')}</span>
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
                        <span className="text-[10px] text-gray-400">{t('processGraph.noTools')}</span>
                      ) : null}
                    </>
                  );
                })()}
              </div>
              {/* Two rows: the tokens, then tool calls and time. The second row's
                  pills never wrap apart; the links go under them when the
                  panel is too narrow for both. */}
              <div className="flex flex-wrap gap-1 mb-1" data-testid="run-stats-tokens">
                <TokenPill label={t('processGraph.in')} value={mr.inbound_tokens} />
                <TokenPill label={t('processGraph.out')} value={mr.outbound_tokens} />
                <TokenPill label={t('processGraph.total')} value={mr.total_tokens} />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                <div className="flex shrink-0 gap-1" data-testid="run-stats-run">
                  <TokenPill label={t('processGraph.tools2')} value={toolCount} />
                  <TokenPill label={t('processGraph.duration')} value={fmtDurationMs(mr.duration_ms)} />
                </div>
                {mr.run_id && (showDetailsLink || showEvalCase) && (
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
                    {showEvalCase && mr.status !== 'running' && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setCaseRunId(mr.run_id); }}
                        title={t('messageDetails.toEvalCaseHint')}
                        className="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-indigo-500 hover:text-indigo-700"
                      >
                        <FlaskConical className="w-3 h-3" /> {t('messageDetails.toEvalCase')}
                      </button>
                    )}
                    {showDetailsLink && (
                      <a
                        href={`/messages/${mr.run_id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-indigo-500 hover:text-indigo-700"
                      >
                        <ArrowUpRight className="w-3 h-3" /> {t('processGraph.viewFullDetails')}
                      </a>
                    )}
                  </div>
                )}
              </div>
            </div>
            {isExpanded && (
              <div className="ml-5 mt-2 space-y-2">
                <ProcessNode
                  label={t('processGraph.input')}
                  labelColor="text-blue-700"
                  borderColor="border-blue-200"
                  bgColor="bg-blue-50"
                >
                  <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">
                    {mr.input || '(empty)'}
                  </div>
                </ProcessNode>
                {/* Thoughts and tool calls share one step counter (ChatStreamCallback._step),
                    so merging by step renders them in true execution order. Entries without
                    a step (older runs) sink to the end, keeping their original order. Thoughts
                    are numbered by thought order only — tools (their result) don't advance it. */}
                {(() => {
                  const merged = [
                    ...(mr.reasoning || []).map((r, rIdx) => ({ kind: 'thought', step: r.step, key: `r-${rIdx}`, item: r })),
                    ...(mr.tools || []).map((t, tIdx) => ({ kind: 'tool', step: t.step, key: `t-${tIdx}`, item: t })),
                  ].sort((a, b) => (a.step ?? Infinity) - (b.step ?? Infinity));
                  let thoughtNo = 0;
                  merged.forEach((s) => { if (s.kind === 'thought') { thoughtNo += 1; s.thoughtNo = thoughtNo; } });
                  return merged.map((entry) => {
                    if (entry.kind === 'thought') {
                      const r = entry.item;
                      return (
                        <ProcessNode
                          key={entry.key}
                          label={`Thought · step ${entry.thoughtNo}`}
                          labelColor="text-violet-700"
                          borderColor="border-violet-200"
                          bgColor="bg-violet-50"
                          hint={preview(r.content)}
                        >
                          <div className="text-[11px] text-violet-900 whitespace-pre-wrap break-words">
                            {r.content || '(empty)'}
                          </div>
                        </ProcessNode>
                      );
                    }
                    const tc = entry.item;
                    if (DELEGATION_TOOLS.has(tc.tool)) {
                      return <DelegatedRunNode key={entry.key} tc={tc} />;
                    }
                    if (tc.tool === SKILL_TOOL) {
                      return (
                        <ProcessNode
                          key={entry.key}
                          label={t('processGraph.skillRetrieved')}
                          labelColor="text-violet-800"
                          borderColor="border-violet-300"
                          bgColor="bg-violet-50"
                          hint={preview(tc.input || tc.output)}
                          tool={tc}
                        >
                          {tc.input && (
                            <div className="text-[11px] text-violet-700 whitespace-pre-wrap break-all">
                              <span className="text-violet-400">{t('processGraph.id')}</span> {tc.input}
                            </div>
                          )}
                          {tc.output && (
                            <div className="text-[11px] text-violet-900 whitespace-pre-wrap break-all">
                              {tc.output}
                            </div>
                          )}
                        </ProcessNode>
                      );
                    }
                    // think/plan tools carry their content as the input and merely
                    // echo it back as output, so the "in:" line is redundant noise.
                    const isReasoningTool = tc.tool === 'think' || tc.tool === 'plan';
                    return (
                      <ProcessNode
                        key={entry.key}
                        label={`Tool: ${tc.tool || 'tool'}`}
                        labelColor="text-amber-700"
                        borderColor="border-amber-200"
                        bgColor="bg-amber-50"
                        hint={toolInline(tc.tool, tc.input)}
                        tool={tc}
                      >
                        {!isReasoningTool && tc.input && (
                          <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all">
                            <span className="text-gray-500">{t('processGraph.in2')}</span> {tc.input}
                          </div>
                        )}
                        {(tc.output || (isReasoningTool && tc.input)) && (
                          <div className="text-[11px] text-emerald-700 whitespace-pre-wrap break-all">
                            <span className="text-emerald-600">{t('processGraph.out2')}</span> {tc.output || tc.input}
                          </div>
                        )}
                      </ProcessNode>
                    );
                  });
                })()}
                <ProcessNode
                  label={t('processGraph.output')}
                  labelColor="text-green-700"
                  borderColor="border-green-200"
                  bgColor="bg-green-50"
                >
                  <StepText text={mr.output} />
                </ProcessNode>
                {mr.error && (
                  <ProcessNode
                    label={t('processGraph.error')}
                    labelColor="text-red-700"
                    borderColor="border-red-200"
                    bgColor="bg-red-50"
                  >
                    <div className="text-[11px] text-red-700 whitespace-pre-wrap break-words">
                      {mr.error}
                    </div>
                  </ProcessNode>
                )}
              </div>
            )}
          </div>
        );
      })}
      {caseRunId && (
        <SaveAsEvalCaseDialog
          runId={caseRunId}
          workspace={evalCaseWorkspace || undefined}
          onClose={() => setCaseRunId(null)}
        />
      )}
    </div>
  );
}
