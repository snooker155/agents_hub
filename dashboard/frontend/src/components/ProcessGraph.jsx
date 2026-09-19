import { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, ChevronUp, Zap, Bot } from 'lucide-react';
import { SKILL_TOOL, shortText, fmtDurationMs, preview } from './processUtils';
import { toolInline } from './toolFormatters';
import ProcessNode from './ProcessNode';
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
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-medium"
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

const RUN_STATUS_CLS = {
  running: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  error: 'bg-red-100 text-red-700',
  stopped: 'bg-gray-100 text-gray-500',
};

export default function ProcessGraph({ messageRuns = [], showDetailsLink = true, titleByAgent = false }) {
  const { t } = useI18n();
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
            className="relative pl-4"
          >
            {hasNext && <div className="absolute left-[7px] top-4 bottom-[-16px] w-px bg-gray-200" />}
            <div className="absolute left-0 top-2 w-3 h-3 rounded-full bg-indigo-500" />
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
                    ? shortText(mr.output || t('processGraph.noResponseYet'), 120)
                    : (mr.output || t('processGraph.noResponseYet'))}
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
              <div className="flex items-end justify-between gap-2">
                <div className="flex flex-wrap gap-1">
                  <TokenPill label={t('processGraph.in')} value={mr.inbound_tokens} />
                  <TokenPill label={t('processGraph.out')} value={mr.outbound_tokens} />
                  <TokenPill label={t('processGraph.total')} value={mr.total_tokens} />
                  <TokenPill label={t('processGraph.tools2')} value={toolCount} />
                  <TokenPill label={t('processGraph.duration')} value={fmtDurationMs(mr.duration_ms)} />
                </div>
                {showDetailsLink && mr.run_id && (
                  <a
                    href={`/messages/${mr.run_id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="shrink-0 whitespace-nowrap text-sm font-semibold text-gray-400 transition hover:text-indigo-600"
                  >
                    {t('processGraph.viewFullDetails')}
                  </a>
                )}
              </div>
            </div>
            {isExpanded && (
              <div className="ml-5 mt-2 space-y-2">
                <div className="rounded-lg border border-blue-200 bg-blue-50 p-2.5">
                  <div className="text-[11px] font-semibold text-blue-700 mb-1.5">{t('processGraph.input')}</div>
                  <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">
                    {mr.input || '(empty)'}
                  </div>
                </div>
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
                    if (tc.tool === SKILL_TOOL) {
                      return (
                        <ProcessNode
                          key={entry.key}
                          label={t('processGraph.skillRetrieved')}
                          labelColor="text-violet-800"
                          borderColor="border-violet-300"
                          bgColor="bg-violet-50"
                          hint={preview(tc.input || tc.output)}
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
                <div className="rounded-lg border border-green-200 bg-green-50 p-2.5">
                  <div className="text-[11px] font-semibold text-green-700 mb-1.5">{t('processGraph.output')}</div>
                  <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">
                    {mr.output || '(empty)'}
                  </div>
                </div>
                {mr.error && (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-2.5">
                    <div className="text-[11px] font-semibold text-red-700 mb-1.5">{t('processGraph.error')}</div>
                    <div className="text-[11px] text-red-700 whitespace-pre-wrap break-words">
                      {mr.error}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
