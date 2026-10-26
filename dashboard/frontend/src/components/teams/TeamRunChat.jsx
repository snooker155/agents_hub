import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ExternalLink, Loader, Play, RotateCcw, StopCircle, UsersRound } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useI18n } from '../../i18n';
import AgentTranscript from '../chat/AgentTranscript';
import ViewModeToggle from '../chat/ViewModeToggle';
import ComposerBox from '../chat/ComposerBox';
import { COMPOSER_SEND_CLS, COMPOSER_STOP_CLS, COMPOSER_TEXTAREA_CLS } from '../chat/composerStyles';
import { useChatComposerInput } from '../chat/useChatComposerInput';
import { AttachButton, AttachPickers, PendingAttachments, PendingReferences } from '../chat/ComposerAttachments';
import { StatusBadge } from '../loops/loopUi';
import { stopReasonText } from './teamFormat';
import { TEAM_SENDER } from './teamBoard';

/**
 * The Run tab of the team page, drawn as the flow page draws its chat
 * (flow/FlowChat.jsx): what the team was asked for is the person's message,
 * every member's reply is a reply under the member's name (its steps in the
 * Build view, its files under it), a member still working is a reply being
 * written, and the closing answer ends the run. Who is on the team and what
 * each turn did are in the column beside it (TeamRunSide.jsx).
 *
 * The box at the foot asks for the next run, files and hub records attached
 * as in the flow chat: Enter, or the round button, starts it; while the team
 * works the button stops it instead.
 */
export default function TeamRunChat({
  team, run, messages, live, workspace, active = true, viewMode, onViewModeChange,
  goal, onGoalChange, onRun, onStop, stopping, starting, onResume, resuming,
}) {
  const { t } = useI18n();
  const scrollerRef = useRef(null);
  const composerRef = useRef(null);
  const textareaRef = useRef(null);
  const [composerHeight, setComposerHeight] = useState(96);

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

  // A run opens at its end, the tab too when it comes back on screen, and a
  // live one follows what it adds while the reader is at the end already.
  // Switching Chat / Build keeps the place: the reader is reading something.
  const runId = run?.team_run_id;
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (el && active) el.scrollTop = el.scrollHeight;
  }, [active, runId]);
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const canRun = !live && !starting && team.members.length > 0;
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

  const agentNameFor = (msg) => (msg.sender === TEAM_SENDER ? team.name : msg.sender);
  const working = messages.some((m) => m.kind === 'working');
  const ended = run && !live;
  const resumable = ended && run.has_checkpoint && ['stopped', 'failed'].includes(run.status);
  const reason = run ? stopReasonText(run, t) : '';

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-slate-50" data-testid="team-run-chat">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-slate-200 bg-white px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-100">
          <UsersRound className="h-4 w-4 text-indigo-600" />
        </div>
        <div className="min-w-[8rem] flex-1">
          <div className="truncate text-sm font-bold text-slate-900">{t('teamDetails.runChatTitle')}</div>
          <div className="truncate text-[11px] text-slate-400">
            {run?.started_at ? new Date(run.started_at).toLocaleString() : team.name}
          </div>
        </div>
        {run && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600" data-testid="team-run-status">
            <StatusBadge status={run.status} />
            <span>{t('teamDetails.roundsDone', { count: run.rounds_done || 0 })}</span>
            <span>${(run.total_cost || 0).toFixed(4)}</span>
            {reason && <span className="italic text-gray-500">{reason}</span>}
            {run.task_id && (
              <Link to={`/tasks/${run.task_id}`} className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-800">
                <ExternalLink className="h-3 w-3" /> {t('teamDetails.task')}
              </Link>
            )}
            {resumable && (
              <button
                type="button" onClick={onResume} disabled={resuming}
                title={t('teamDetails.resumeHint')}
                className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 hover:bg-amber-100 disabled:opacity-50"
              >
                {resuming ? <Loader className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                {t('teamDetails.resume')}
              </button>
            )}
          </div>
        )}
        <ViewModeToggle viewMode={viewMode} onChange={onViewModeChange} small />
      </div>

      <div className="relative min-h-0 flex-1">
        <div ref={scrollerRef} className="h-full overflow-y-auto px-3 pt-3" style={{ paddingBottom: composerHeight + 12 }}>
          {!run ? (
            <div className="flex min-h-full flex-col items-center justify-center py-4 text-center">
              <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-100 shadow-sm">
                <UsersRound className="h-7 w-7 text-indigo-600" />
              </div>
              <p className="max-w-sm text-sm leading-relaxed text-slate-500">{t('teamDetails.newRunHint')}</p>
            </div>
          ) : (
            <div>
              <AgentTranscript
                messages={messages}
                viewMode={viewMode}
                agentNameFor={agentNameFor}
                agentName={team.name}
                waiting={live && !working}
              />
              {ended && run.error && (
                <div className="mx-2 mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {run.error}
                </div>
              )}
            </div>
          )}
        </div>

        <div ref={composerRef} className="pointer-events-none absolute inset-x-0 bottom-0 z-10 px-3 pb-3 pt-2">
          <div className="pointer-events-auto mx-auto w-full max-w-3xl">
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
              hint={t('teamDetails.enterToRun')}
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
              actions={live ? (
                <button type="button" onClick={onStop} disabled={stopping || run?.status === 'stopping'} title={t('common.stop')} className={COMPOSER_STOP_CLS}>
                  {stopping ? <Loader className="h-4 w-4 animate-spin" /> : <StopCircle className="h-4 w-4" />}
                </button>
              ) : (
                <button
                  type="button" onClick={startRun} disabled={!canRun}
                  title={t('common.run')} className={COMPOSER_SEND_CLS}
                  data-testid="team-run-button"
                >
                  {starting ? <Loader className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                </button>
              )}
            >
              <textarea
                ref={textareaRef}
                className={COMPOSER_TEXTAREA_CLS}
                placeholder={t('teamDetails.whatTheTeamShouldDeliver')}
                aria-label={t('teamDetails.theRequestToHandThe')}
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
