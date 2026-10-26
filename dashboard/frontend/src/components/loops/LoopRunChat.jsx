import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Bot, DollarSign, Play, Repeat, StopCircle, User, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import ComposerBox from '../chat/ComposerBox';
import { COMPOSER_SEND_CLS, COMPOSER_STOP_CLS, COMPOSER_TEXTAREA_CLS } from '../chat/composerStyles';
import { AVATAR_NAME_CLS, BUBBLE_SIZES } from '../chat/bubbleSizes';
import { useChatComposerInput } from '../chat/useChatComposerInput';
import { AttachButton, AttachPickers, PendingAttachments, PendingReferences } from '../chat/ComposerAttachments';
import { buildAttachmentLine } from '../chat/send/buildRequest';
import ChatMarkdown from '../chat/ChatMarkdown';
import PassEvaluation from './PassEvaluation';
import { StatusBadge } from './loopUi';
import { hasScore, stopReasonLabel } from './loopFormat';
import { isEvaluating } from './passLog';
import AgentTranscript from '../chat/AgentTranscript';

const NO_LIVE = [];

const SIZE = BUBBLE_SIZES.full;

// The cost estimate stays in the code, out of the box for now.
const SHOW_ESTIMATE = false;

// A reply's row, as the Chat view draws one (chat/MessageBubble.jsx): the
// avatar with the flow's name under it, then the bubble.
function ReplyRow({ name, children }) {
  return (
    <div className={`${SIZE.row} flex-row`}>
      <div className="flex flex-col items-center gap-1 flex-shrink-0 w-14">
        <div className="w-8 h-8 rounded-full flex items-center justify-center text-white bg-gray-800">
          <Bot className="w-4 h-4" />
        </div>
        {name && <span title={name} className={AVATAR_NAME_CLS}>{name}</span>}
      </div>
      <div className="min-w-0 flex-1 max-w-[92%]">{children}</div>
    </div>
  );
}

const BUBBLE = 'rounded-2xl rounded-tl-sm border border-gray-200 bg-white text-gray-800 shadow-sm';

// What a run had attached, named under its goal as the Chat page's bubble
// names a message's attachments.
function attachedLine(t, attached) {
  const list = attached || [];
  return buildAttachmentLine(t, {
    pendingAttachments: list.filter((a) => a.kind === 'file').map((a) => ({ filename: a.label })),
    pendingReferences: list.filter((a) => a.kind !== 'file').map((a) => ({ label: a.label })),
  });
}

/**
 * The Run tab, drawn as the flow page draws its chat (flow/FlowChat.jsx):
 * what the run was asked for is the person's message, each pass of the flow
 * is a reply with the judge's verdict on it (score, output, comments), and
 * the outcome closes the run. The scores across the passes and what the
 * flow did on each pass are in the column beside the chat (LoopPassLogs.jsx).
 * The box at the foot is where the next run is asked for, files and hub
 * records attached as in the flow chat: Enter, or the round button, starts
 * it; while a run works the button stops it instead.
 */
export default function LoopRunChat({
  loop, run, iterations, logs, nodes, live, workspace, active = true,
  goal, onGoalChange, onRun, onStop, starting, estimate, onEstimate, onEstimateClose,
  liveMessages = NO_LIVE,
}) {
  const { t } = useI18n();
  const scrollerRef = useRef(null);
  const composerRef = useRef(null);
  const textareaRef = useRef(null);
  const [composerHeight, setComposerHeight] = useState(96);

  // What rides with the next run, as in the flow chat: files from the
  // computer or the workspace, and hub records (chat/ComposerAttachments.jsx).
  const fileInputRef = useRef(null);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [pendingReferences, setPendingReferences] = useState([]);
  const [attachmentError, setAttachmentError] = useState('');
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [contextKinds, setContextKinds] = useState([]);
  const [pickerKind, setPickerKind] = useState(null);
  const [filePickerOpen, setFilePickerOpen] = useState(false);
  const {
    resizeTextarea, onPickFiles, removeAttachment, toggleAttachmentStore,
    addReferences, removeReference, addWorkspaceFiles,
  } = useChatComposerInput({
    attachMenuOpen, contextKinds, pendingAttachments, selectedWorkspace: workspace,
    setAttachmentError, setContextKinds, setPendingAttachments, setPendingReferences, textareaRef,
  });
  useEffect(() => { resizeTextarea(); }, [goal, resizeTextarea]);

  // The messages scroll behind the box, which keeps its own height free
  // under the last one.
  useLayoutEffect(() => {
    const el = composerRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => setComposerHeight(el.offsetHeight));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // A run opens at its end, the tab too when it comes back on screen (hidden,
  // the chat has no height to scroll), and a live one follows what it adds
  // while the reader is at the end already.
  const runId = run?.loop_run_id;
  const finished = Boolean(run && !live && (run.result || run.stop_reason || run.error));
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (el && active) el.scrollTop = el.scrollHeight;
  }, [active, runId, iterations.length, finished]);
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !live) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight;
  }, [iterations, live]);

  const canRun = !live && !starting && loop.flow_exists;
  const startRun = async () => {
    if (!canRun) return;
    const started = await onRun({ attachments: pendingAttachments, references: pendingReferences });
    if (started) {
      setPendingAttachments([]);
      setPendingReferences([]);
      setAttachmentError('');
    }
  };
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      startRun();
    }
  };

  const working = live && !iterations.some((i) => i.status === 'running');
  const attached = run ? attachedLine(t, run.attached) : '';

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-slate-50" data-testid="loop-run-chat">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-slate-200 bg-white px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-100">
          <Repeat className="h-4 w-4 text-emerald-600" />
        </div>
        <div className="min-w-[8rem] flex-1">
          <div className="truncate text-sm font-bold text-slate-900">{t('loops.runChatTitle')}</div>
          <div className="truncate text-[11px] text-slate-400">
            {run?.started_at ? new Date(run.started_at).toLocaleString() : loop.flow_name}
          </div>
        </div>
        {run && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600" data-testid="loop-run-summary">
            <StatusBadge status={run.status} />
            <span>{t('loops.iterationCount', { count: run.iterations_done || 0 })}</span>
            {hasScore(run.best_score) && <span>{t('loops.bestScore', { score: Math.round(run.best_score) })}</span>}
          </div>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        <div ref={scrollerRef} className="h-full overflow-y-auto px-3 pt-3" style={{ paddingBottom: composerHeight + 12 }}>
          {!run ? (
            <div className="flex min-h-full flex-col items-center justify-center py-4 text-center">
              <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-100 shadow-sm">
                <Repeat className="h-7 w-7 text-emerald-600" />
              </div>
              <p className="max-w-sm text-sm leading-relaxed text-slate-500">{t('loops.newRunHint')}</p>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-5xl">
              {/* What the run was asked for: the person's message. */}
              <div className={`${SIZE.row} flex-row-reverse`}>
                <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-white bg-indigo-600">
                  <User className="w-4 h-4" />
                </div>
                <div className={`${SIZE.bubble} leading-relaxed bg-indigo-600 text-white rounded-tr-sm`}>
                  <span className="whitespace-pre-wrap">{[run.goal || t('loops.noGoal'), attached].filter(Boolean).join('\n')}</span>
                </div>
              </div>

              {/* Each pass and what the judge made of it. */}
              {iterations.map((it) => (
                <ReplyRow key={`${run.loop_run_id}:${it.iteration}`} name={loop.flow_name}>
                  <PassEvaluation
                    iteration={it}
                    flowId={loop.flow_id}
                    evaluating={isEvaluating(nodes, logs[it.iteration], it)}
                    className={BUBBLE}
                  />
                </ReplyRow>
              ))}

              {/* The pass under way: its nodes at work, as replies being
                  written (components/runs/liveTurns.js). */}
              {liveMessages.length > 0 && (
                <AgentTranscript messages={liveMessages} viewMode="chat" agentNameFor={(m) => m.agent_label || ''} />
              )}

              {working && (
                <ReplyRow name={loop.flow_name}>
                  <div className={`${BUBBLE} inline-flex items-center gap-2 px-4 py-3`}>
                    <span className="text-xs text-gray-400">{t('loops.loopIsWorking')}</span>
                    <span className="flex gap-1">
                      {[0, 150, 300].map((d) => (
                        <span key={d} className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
                      ))}
                    </span>
                  </div>
                </ReplyRow>
              )}

              {/* How the run ended and what it settled on, whole. */}
              {finished && (
                <ReplyRow name={loop.flow_name}>
                  <div className={`${BUBBLE} px-4 py-3 space-y-3`} data-testid="loop-run-outcome">
                    <div className="flex items-center gap-2 flex-wrap text-sm">
                      <span className="text-xs font-bold uppercase tracking-wide text-gray-500">{t('loops.result')}</span>
                      {run.stop_reason && <span className="text-gray-600 italic">{stopReasonLabel(run.stop_reason, t)}</span>}
                    </div>
                    {run.error && <p className="text-sm text-red-700">{run.error}</p>}
                    {run.result && (
                      <div className="break-words text-sm text-gray-800"><ChatMarkdown content={run.result} /></div>
                    )}
                  </div>
                </ReplyRow>
              )}
            </div>
          )}
        </div>

        <div ref={composerRef} className="pointer-events-none absolute inset-x-0 bottom-0 z-10 px-3 pb-3 pt-2">
          <div className="pointer-events-auto mx-auto w-full max-w-3xl">
            {SHOW_ESTIMATE && estimate && (
              <div className="mb-2 flex items-start gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs text-indigo-900" data-testid="loop-estimate">
                <span className="min-w-0 flex-1">
                  <span className="font-bold">{t('loops.upToLlmCalls', { count: estimate.llm_calls_upper_bound })}</span>
                  {': '}{t('loops.estimateBreakdown', { nodes: estimate.agent_nodes, iterations: estimate.max_iterations })}{' '}
                  <span className="text-indigo-700">{t(`loops.${estimate.note_key}`, { defaultValue: estimate.note })}</span>
                </span>
                <button
                  type="button" onClick={onEstimateClose}
                  title={t('loops.closeEstimate')} aria-label={t('loops.closeEstimate')}
                  className="shrink-0 rounded-md p-0.5 text-indigo-600 transition hover:bg-indigo-100 hover:text-indigo-800"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={onPickFiles} />
            <PendingReferences references={pendingReferences} onRemove={removeReference} disabled={live} />
            <PendingAttachments
              attachments={pendingAttachments}
              workspace={workspace}
              onToggleStore={toggleAttachmentStore}
              onRemove={removeAttachment}
              disabled={live}
            />
            <ComposerBox
              hint={t('loops.enterToRun')}
              leading={(
                <AttachButton
                  open={attachMenuOpen}
                  onOpenChange={setAttachMenuOpen}
                  disabled={live}
                  contextKinds={contextKinds}
                  onFiles={() => fileInputRef.current?.click()}
                  onWorkspaceFiles={() => setFilePickerOpen(true)}
                  onKind={setPickerKind}
                />
              )}
              footerLeft={SHOW_ESTIMATE ? (
                <button
                  type="button" onClick={onEstimate}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-indigo-600 hover:bg-indigo-50"
                >
                  <DollarSign className="h-3 w-3" /> {t('loops.estimate')}
                </button>
              ) : null}
              actions={live ? (
                <button type="button" onClick={onStop} disabled={run?.status === 'stopping'} title={t('loops.stop')} className={COMPOSER_STOP_CLS}>
                  <StopCircle className="h-4 w-4" />
                </button>
              ) : (
                <button
                  type="button" onClick={startRun} disabled={!canRun}
                  title={t('loops.run')} className={COMPOSER_SEND_CLS}
                  data-testid="loop-run-button"
                >
                  <Play className="h-3.5 w-3.5" />
                </button>
              )}
            >
              <textarea
                ref={textareaRef}
                className={COMPOSER_TEXTAREA_CLS}
                placeholder={t('loops.theRequestTheFlowWorks')}
                rows={1}
                value={goal}
                disabled={live}
                onChange={(e) => { onGoalChange(e.target.value); resizeTextarea(); }}
                onKeyDown={onKeyDown}
              />
            </ComposerBox>
            {attachmentError && <p className="mt-2 text-xs text-red-600">{attachmentError}</p>}
            <AttachPickers
              workspace={workspace}
              filePickerOpen={filePickerOpen}
              onCloseFilePicker={() => setFilePickerOpen(false)}
              attachments={pendingAttachments}
              onAddWorkspaceFiles={addWorkspaceFiles}
              pickerKind={pickerKind}
              onClosePicker={() => setPickerKind(null)}
              references={pendingReferences}
              onAddReferences={addReferences}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
