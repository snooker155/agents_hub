import ContextEntityPicker from '../ContextEntityPicker';
import ContextMeter from '../ContextMeter';
import WorkspaceFilePicker from '../files/WorkspaceFilePicker';
import { CheckCircle, Clock, FolderOpen, Loader, Paperclip, Send, Send as SendIcon, StopCircle, Terminal, Upload, X, Zap } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useChatPage } from './context';
import { useChatSteering } from './useChatSteering';

/**
 * While a turn runs: how a message typed now reaches it (Steer, Interrupt,
 * Queue; see ./steering.js), the one-line hint for the chosen way, and the
 * messages waiting for the turn to end.
 */
function SteerBar({ steering, t }) {
  const { modes, activeMode, setMode, queued, removeQueued, error, notice } = steering;
  if (!steering.busy && !queued.length && !error && !notice) return null;
  return (
    <div className="mb-2 space-y-1.5" data-testid="steer-bar">
      {steering.busy && modes.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-gray-500">{t('steering.modeLabel')}</span>
          <div className="inline-flex rounded-full border border-gray-200 bg-gray-50 p-0.5" role="radiogroup">
            {modes.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={activeMode === m}
                onClick={() => setMode(m)}
                className={`px-2.5 py-0.5 text-[11px] font-medium rounded-full transition-colors ${
                  activeMode === m ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {t(`steering.modes.${m}`)}
              </button>
            ))}
          </div>
          <span className="text-[11px] text-gray-400">{t(`steering.modeHints.${activeMode}`)}</span>
        </div>
      )}
      {steering.busy && modes.length === 1 && (
        <p className="text-[11px] text-gray-400">{t('steering.queueOnlyHint')}</p>
      )}
      {queued.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {queued.map((q) => (
            <span
              key={q.id}
              title={t('steering.queuedTitle')}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800"
            >
              {q.interrupt ? <Zap className="h-3 w-3" /> : <Clock className="h-3 w-3" />}
              {q.interrupt && <span className="font-semibold">{t('steering.interruptTag')}</span>}
              <span className="truncate max-w-[18rem]">{q.text}</span>
              <button
                type="button"
                onClick={() => removeQueued(q.id)}
                className="text-amber-500 hover:text-red-600"
                title={t('steering.removeQueued')}
                aria-label={t('steering.removeQueued')}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {notice === 'restored' && <p className="text-[11px] text-gray-500">{t('steering.restored')}</p>}
      {error && <p className="text-[11px] text-red-600">{t('steering.failed', { error })}</p>}
    </div>
  );
}

/**
 * Writing the next turn: the box, the slash-command picker, the attachments and
 * the hub records that ride along with it. The box stays open while a turn
 * runs, so a message can steer it, interrupt it or wait for it.
 */
export default function ChatComposer() {
  const page = useChatPage();
  // The "from workspace files" dialog (docs/files.md): files already stored
  // in the workspace, attached to the turn by id.
  const [filePickerOpen, setFilePickerOpen] = useState(false);
  const {
    addReferences, addWorkspaceFiles, attachMenuOpen, attachmentError, commandMenuIndex, commandMenuOpen,
    commandSuggestions, composerPlaceholder, contextKinds, contextUsage,
    currentTelegramBinding, fileInputRef, handleKeyDown, hasTarget, input, loading,
    onPickFiles, pendingAttachments, pendingReferences, pickerKind, removeAttachment,
    removeReference, resizeTextarea, selectCommand, selectedProject, selectedWorkspace,
    sendAsBot, sendMessage, setAttachMenuOpen, setCommandMenuIndex, setCommandMenuOpen,
    setInput, setPickerKind, t, telegramError, telegramReplyAllowed,
    telegramSending, textareaRef, toggleAttachmentStore,
  } = page;
  const steering = useChatSteering(page);
  // Enter while a turn runs goes to the turn (in the chosen mode) instead of
  // starting a new one, which the page's own handler would refuse.
  const onKeyDown = (e) => {
    if (loading && e.key === 'Enter' && !e.shiftKey && !(commandMenuOpen && commandSuggestions.length > 0)) {
      e.preventDefault();
      steering.steer();
      return;
    }
    handleKeyDown(e);
  };
  return (
    <>
        {/* Input area */}
        <div className="flex-shrink-0 border-t border-gray-200 px-4 py-4">
          <div className="max-w-3xl mx-auto">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={onPickFiles}
            />
            {pendingReferences.length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                {pendingReferences.map((ref) => (
                  <span
                    key={`${ref.kind}:${ref.id}`}
                    className="inline-flex items-center gap-1.5 max-w-full rounded-full border border-indigo-200
                      bg-indigo-50 px-2 py-1 text-[11px] text-indigo-700"
                    title={t(`chat.entityKind.${ref.kind}`, { defaultValue: ref.kind })}
                  >
                    <span aria-hidden="true">{ref.icon}</span>
                    {ref.url ? (
                      <Link to={ref.url} className="truncate max-w-[14rem] font-medium hover:underline">
                        {ref.label || ref.id}
                      </Link>
                    ) : (
                      <span className="truncate max-w-[14rem] font-medium">{ref.label || ref.id}</span>
                    )}
                    <button
                      type="button"
                      onClick={() => removeReference(ref.kind, ref.id)}
                      className="text-indigo-400 hover:text-red-600 flex-shrink-0"
                      title={t('chat.removeAttachment')}
                      disabled={loading}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {pendingAttachments.length > 0 && (
              <div className="mb-2 space-y-2">
                {pendingAttachments.map((att) => (
                  <div key={att.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-gray-200 bg-gray-50">
                    <div className="min-w-0">
                      <div className="text-xs text-gray-700 font-medium truncate">{att.filename}</div>
                      <div className="text-[11px] text-gray-500">{t('chat.bytes', { count: att.size })}</div>
                    </div>
                    <div className="flex items-center gap-3">
                      {att.file_id ? (
                        <Link
                          to={`/files?file=${encodeURIComponent(att.file_id)}`}
                          className="flex items-center gap-1 text-xs text-emerald-700 hover:underline"
                          data-testid="attachment-saved"
                        >
                          {att.from_workspace ? <FolderOpen className="w-3.5 h-3.5" /> : <CheckCircle className="w-3.5 h-3.5" />}
                          {att.from_workspace ? t('files.chat.workspaceFile') : t('files.chat.saved')}
                        </Link>
                      ) : att.saving ? (
                        <span className="flex items-center gap-1 text-xs text-gray-500">
                          <Loader className="w-3.5 h-3.5 animate-spin" /> {t('files.chat.saving')}
                        </span>
                      ) : (
                        <label className={`flex items-center gap-1.5 text-xs ${selectedWorkspace ? 'text-gray-600' : 'text-gray-400'}`}>
                          <input
                            type="checkbox"
                            checked={Boolean(att.store_to_workspace)}
                            onChange={(e) => toggleAttachmentStore(att.id, e.target.checked)}
                            disabled={!selectedWorkspace || loading}
                          />
                          {t('chat.storeInWorkspace')}
                        </label>
                      )}
                      <button
                        type="button"
                        onClick={() => removeAttachment(att.id)}
                        className="text-gray-400 hover:text-red-600"
                        title={t('chat.removeAttachment')}
                        disabled={loading}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
                {!selectedWorkspace && (
                  <p className="text-[11px] text-amber-600">{t('chat.selectAWorkspaceIfYou')}</p>
                )}
              </div>
            )}

            {/* Slash command picker */}
            {commandMenuOpen && commandSuggestions.length > 0 && (
              <div className="mb-2 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
                <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 flex items-center gap-1.5">
                  <Terminal className="w-3 h-3 text-indigo-500" />
                  <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{t('chat.commandsLabel')}</span>
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
                    <span className="text-xs text-gray-500 mt-0.5">{cmd.descriptionKey ? t(cmd.descriptionKey) : cmd.description}</span>
                  </button>
                ))}
              </div>
            )}

            {/* What is left of the model's window, read where the next message
                is typed. Clearing is offered once the context is tight; /clear
                is the same action from the keyboard. */}
            <ContextMeter
              usage={contextUsage}
              onClear={loading ? null : () => selectCommand({ name: '/clear' })}
              className="mb-2 px-1"
            />

            <SteerBar steering={steering} t={t} />

            <div
              className="flex items-center gap-3 border border-gray-300 rounded-2xl px-4 py-3
                focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100
                shadow-sm transition-all"
            >
              {/* Attach: a file from the computer, or a record the hub already
                  holds (task / view / project / scenario / loop / …). An entity
                  picked here is folded into the prompt whether or not the agent
                  owns a tool that could have fetched it. */}
              <div className="relative flex-shrink-0">
                <button
                  type="button"
                  onClick={() => setAttachMenuOpen((open) => !open)}
                  className="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors disabled:opacity-40"
                  title={t('chat.attachMenuTitle')}
                  disabled={loading || !hasTarget}
                >
                  <Paperclip className="w-4 h-4" />
                </button>

                {attachMenuOpen && (
                  <>
                    {/* Click-away layer: a menu anchored above the composer has
                        no other way to close without stealing focus. */}
                    <div className="fixed inset-0 z-40" onClick={() => setAttachMenuOpen(false)} />
                    <div className="absolute bottom-10 left-0 z-50 w-60 max-h-80 overflow-y-auto bg-white border border-gray-200 rounded-xl shadow-lg py-1">
                      <button
                        type="button"
                        onClick={() => { setAttachMenuOpen(false); fileInputRef.current?.click(); }}
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <Upload className="w-4 h-4 text-gray-400" />
                        {t('chat.attachFiles')}
                      </button>
                      <button
                        type="button"
                        onClick={() => { setAttachMenuOpen(false); setFilePickerOpen(true); }}
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <FolderOpen className="w-4 h-4 text-gray-400" />
                        {t('files.chat.fromWorkspace')}
                      </button>
                      {contextKinds.length > 0 && (
                        <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-gray-400 border-t border-gray-100 mt-1">
                          {t('chat.attachFromHub')}
                        </div>
                      )}
                      {contextKinds.map((k) => (
                        <button
                          key={k.kind}
                          type="button"
                          onClick={() => { setAttachMenuOpen(false); setPickerKind(k.kind); }}
                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
                        >
                          <span aria-hidden="true">{k.icon}</span>
                          {t(`chat.entityKind.${k.kind}`, { defaultValue: k.noun })}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>

              <textarea
                ref={textareaRef}
                className="flex-1 resize-none text-base text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent leading-relaxed disabled:opacity-50"
                placeholder={loading ? t('steering.placeholderBusy') : composerPlaceholder}
                rows={1}
                value={input}
                disabled={!hasTarget}
                onChange={(e) => {
                  const val = e.target.value;
                  setInput(val);
                  resizeTextarea();
                  setCommandMenuOpen(val.startsWith('/'));
                  setCommandMenuIndex(0);
                }}
                onKeyDown={onKeyDown}
              />

              {loading ? (
                <>
                  {input.trim() && (
                    <button
                      type="button"
                      onClick={() => steering.steer()}
                      title={steering.activeMode ? t(`steering.modeHints.${steering.activeMode}`) : t('steering.send')}
                      data-testid="steer-send"
                      className="flex-shrink-0 h-8 px-3 flex items-center gap-1.5 rounded-full
                        bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-semibold transition-colors"
                    >
                      <Send className="w-3.5 h-3.5" />
                      {steering.activeMode ? t(`steering.modes.${steering.activeMode}`) : t('steering.send')}
                    </button>
                  )}
                  <button
                    onClick={steering.stop}
                    title={t('chat.stop')}
                    className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-100 text-red-600 hover:bg-red-200 transition-colors"
                  >
                    <StopCircle className="w-4 h-4" />
                  </button>
                </>
              ) : currentTelegramBinding ? (
                <button
                  onClick={sendAsBot}
                  disabled={!input.trim() || telegramSending || !telegramReplyAllowed}
                  title={!telegramReplyAllowed
                    ? t('chat.telegramBoundElsewhere', { workspace: currentTelegramBinding.workspace || 'default' })
                    : t('chat.sendAsBotToChat')}
                  className="flex-shrink-0 h-8 px-3 flex items-center gap-1.5 rounded-full
                    bg-sky-600 text-white hover:bg-sky-700 text-xs font-semibold
                    disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <SendIcon className="w-3.5 h-3.5" />
                  {telegramSending ? t('chat.sending') : t('chat.sendAsBot')}
                </button>
              ) : (
                <button
                  onClick={sendMessage}
                  disabled={(!input.trim() && pendingAttachments.length === 0 && pendingReferences.length === 0) || !hasTarget}
                  title={t('chat.sendEnter')}
                  className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full
                    bg-indigo-600 text-white hover:bg-indigo-700
                    disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {telegramError && (
              <p className="text-xs text-red-600 mt-2">{telegramError}</p>
            )}
            {attachmentError && (
              <p className="text-xs text-red-600 mt-2">{attachmentError}</p>
            )}

            {filePickerOpen && (
              <WorkspaceFilePicker
                workspace={selectedWorkspace}
                excludeIds={pendingAttachments.map((a) => a.file_id).filter(Boolean)}
                uploadSource="chat"
                onPick={(records) => { setFilePickerOpen(false); addWorkspaceFiles(records); }}
                onClose={() => setFilePickerOpen(false)}
              />
            )}
            {pickerKind && (
              <ContextEntityPicker
                initialKind={pickerKind}
                workspace={selectedWorkspace}
                projectId={selectedProject}
                selected={pendingReferences}
                onAdd={addReferences}
                onClose={() => setPickerKind(null)}
              />
            )}
            <p className="text-center text-xs text-gray-400 mt-2">
              {t('chat.composerHint')} <span className="">/</span> {t('chat.forCommands')}
            </p>
          </div>
        </div>
    </>
  );
}
