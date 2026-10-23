import ContextMeter from '../ContextMeter';
import { Clock, Send, Send as SendIcon, StopCircle, Terminal, X, Zap } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useChatPage } from './context';
import { useI18n } from '../../i18n';
import ComposerMic from './ComposerMic';
import useChatVoice from './useChatVoice';
import ComposerBox from './ComposerBox';
import { COMPOSER_SEND_CLS, COMPOSER_STOP_CLS, COMPOSER_TEXTAREA_CLS } from './composerStyles';
import { AttachButton, AttachPickers, PendingAttachments, PendingReferences } from './ComposerAttachments';

function isTyping(target) {
  const tag = target?.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable;
}

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
        <div className="flex items-center gap-2 min-w-0">
          <div className="inline-flex flex-shrink-0 rounded-full border border-gray-200 bg-gray-50 p-0.5" role="radiogroup">
            {modes.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={activeMode === m}
                onClick={() => setMode(m)}
                className={`px-2.5 py-0.5 text-[11px] font-medium whitespace-nowrap rounded-full transition-colors ${
                  activeMode === m ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {t(`steering.modes.${m}`)}
              </button>
            ))}
          </div>
          <span className="min-w-0 truncate text-[11px] text-gray-400" title={t(`steering.modeHints.${activeMode}`)}>
            {t(`steering.modeHints.${activeMode}`)}
          </span>
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
    currentConv, currentTelegramBinding, fileInputRef, handleKeyDown, hasTarget, input, loading,
    onPickFiles, pendingAttachments, pendingReferences, pickerKind, removeAttachment,
    removeReference, resizeTextarea, selectCommand, selectedProject, selectedWorkspace,
    sendAsBot, sendMessage, setAttachMenuOpen, setCommandMenuIndex, setCommandMenuOpen,
    setInput, setPickerKind, t, telegramError, telegramReplyAllowed,
    telegramSending, textareaRef, toggleAttachmentStore,
  } = page;

  // The box follows the text however it got there: typed, a slash command's
  // template, or Discuss, Edit and a run report from the Code panel.
  useEffect(() => { resizeTextarea(); }, [input, resizeTextarea]);
  // Kept by the page (useChatSteering), so what waits for a turn outlives a
  // switch to a conversation this composer is not shown for.
  const { steering } = page;
  // The composer is laid over the foot of the conversation rather than under
  // it, so the messages scroll behind the box. The frame around both (the
  // column in Chat.jsx) learns the composer's height and the message list
  // keeps that much room under its last message.
  const frameRef = useRef(null);
  useLayoutEffect(() => {
    const el = frameRef.current;
    const frame = el?.parentElement;
    if (!el || !frame || typeof window === 'undefined') return undefined;
    const measure = () => frame.style.setProperty('--chat-composer-height', `${el.getBoundingClientRect().height}px`);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      frame.style.removeProperty('--chat-composer-height');
    };
  }, []);
  // Enter while a turn runs goes to the turn (in the chosen mode) instead of
  // starting a new one, which the page's own handler would refuse. That is
  // also a turn this tab is not sending (`steering.busy`, see useChatSteering).
  // A voice message: in the Send button's place while the box is empty, and
  // Space held in the empty box (or anywhere outside a text field).
  const { language } = useI18n();
  const [voiceNote, setVoiceNote] = useState('');
  const sendSpoken = useCallback((text) => { setVoiceNote(''); sendMessage(text, { voice: true }); }, [sendMessage]);
  const onVoiceError = useCallback((code) => {
    setVoiceNote(t(`chat.voice.errors.${code}`, { defaultValue: code }));
  }, [t]);
  const voice = useChatVoice({
    workspace: currentConv?.workspace || selectedWorkspace || undefined,
    mode: currentConv?.assistant_thread?.mode,
    language, onText: sendSpoken, onError: onVoiceError,
  });
  const boxEmpty = !input.trim() && pendingAttachments.length === 0 && pendingReferences.length === 0;
  const micShown = boxEmpty && voice.available && !steering.busy && !currentTelegramBinding;
  // A mirrored conversation (docs/hub-link.md) is written on another hub; nothing is sent from here.
  const mirrored = currentConv?.origin === 'mirror';
  const micUsable = micShown && hasTarget && !loading && !mirrored;
  useEffect(() => {
    if (!micUsable) return undefined;
    const down = (e) => {
      if (e.code !== 'Space' || e.altKey || e.ctrlKey || e.metaKey) return;
      const inBox = e.target === textareaRef.current;
      if (!inBox && isTyping(e.target)) return;
      e.preventDefault();
      if (e.repeat) return;
      setVoiceNote('');
      voice.start();
    };
    const up = (e) => {
      if (e.code !== 'Space' || !voice.active()) return;
      e.preventDefault();
      voice.end();
    };
    window.addEventListener('keydown', down, true);
    window.addEventListener('keyup', up, true);
    return () => {
      window.removeEventListener('keydown', down, true);
      window.removeEventListener('keyup', up, true);
    };
  }, [micUsable, textareaRef, voice]);

  const { autoMode } = page;
  const onKeyDown = (e) => {
    // Shift+Tab switches auto mode, as in Claude Code.
    if (e.key === 'Tab' && e.shiftKey && autoMode?.allowed && !currentTelegramBinding) {
      e.preventDefault();
      autoMode.toggle();
      return;
    }
    if (steering.busy && e.key === 'Enter' && !e.shiftKey && !(commandMenuOpen && commandSuggestions.length > 0)) {
      e.preventDefault();
      steering.steer();
      return;
    }
    handleKeyDown(e);
  };
  return (
    <>
        {/* Input area: over the foot of the conversation, no rule above it */}
        <div ref={frameRef} className="absolute inset-x-0 bottom-0 z-10 px-3 sm:px-4 pb-3 sm:pb-4 pt-2 pointer-events-none">
          <div className="max-w-3xl mx-auto pointer-events-auto">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={onPickFiles}
            />
            <PendingReferences references={pendingReferences} onRemove={removeReference} disabled={loading} />
            <PendingAttachments
              attachments={pendingAttachments}
              workspace={selectedWorkspace}
              onToggleStore={toggleAttachmentStore}
              onRemove={removeAttachment}
              disabled={loading}
            />

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

            <SteerBar steering={steering} t={t} />

            {/* Under the line, past a rule: auto mode, how to type, and what
                is left of the model's window. Clearing is offered once the
                context is tight; /clear is the same action from the keyboard. */}
            <ComposerBox
              highlight={Boolean(autoMode?.on) && !currentTelegramBinding && !mirrored}
              // Attach: a file from the computer, or a record the hub already
              // holds (task / view / project / loop / …). An entity picked
              // here is folded into the prompt whether or not the agent owns
              // a tool that could have fetched it.
              leading={(
                <AttachButton
                  open={attachMenuOpen}
                  onOpenChange={setAttachMenuOpen}
                  disabled={loading || !hasTarget}
                  contextKinds={contextKinds}
                  onFiles={() => fileInputRef.current?.click()}
                  onWorkspaceFiles={() => setFilePickerOpen(true)}
                  onKind={setPickerKind}
                />
              )}
              actions={steering.busy ? (
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
                      className={COMPOSER_STOP_CLS}
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
                ) : micShown ? (
                  <ComposerMic voice={voice} disabled={!micUsable} t={t} />
                ) : (
                  <button
                    onClick={sendMessage}
                    disabled={(!input.trim() && pendingAttachments.length === 0 && pendingReferences.length === 0) || !hasTarget || mirrored}
                    title={t('chat.sendEnter')}
                    className={COMPOSER_SEND_CLS}
                  >
                    <Send className="w-3.5 h-3.5" />
                  </button>
                )}
              footerLeft={autoMode && !currentTelegramBinding && !mirrored ? (
                    <button
                      type="button"
                      role="switch"
                      aria-checked={autoMode.on}
                      onClick={autoMode.toggle}
                      disabled={!autoMode.allowed}
                      data-testid="auto-mode-toggle"
                      title={autoMode.allowed ? t('chat.autoMode.toggleHint') : t('chat.autoMode.disabledByAdmin')}
                      className={`inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 font-medium transition-colors
                        disabled:opacity-40 disabled:cursor-not-allowed ${autoMode.on
                          ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
                          : 'text-gray-500 hover:bg-gray-100'}`}
                    >
                      <Zap className="w-3.5 h-3.5" />
                      {autoMode.on ? t('chat.autoMode.on') : t('chat.autoMode.off')}
                      <span className="hidden sm:inline text-[10px] font-normal opacity-70">Shift+Tab</span>
                    </button>
              ) : null}
              hint={<>{t('chat.composerHint')} / {t('chat.forCommands')}</>}
              footerRight={(
                  <ContextMeter
                    usage={contextUsage}
                    onClear={loading ? null : () => selectCommand({ name: '/clear' })}
                    compact
                  />
              )}
            >
                <textarea
                  ref={textareaRef}
                  className={COMPOSER_TEXTAREA_CLS}
                  placeholder={steering.busy ? t('steering.placeholderBusy') : composerPlaceholder}
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
            </ComposerBox>
            {telegramError && (
              <p className="text-xs text-red-600 mt-2">{telegramError}</p>
            )}
            {attachmentError && (
              <p className="text-xs text-red-600 mt-2">{attachmentError}</p>
            )}
            {(voice.talking || voice.transcribing || voiceNote) && (
              <p className={`text-xs mt-2 ${voiceNote && !voice.talking && !voice.transcribing ? 'text-red-600' : 'text-gray-500'}`}
                data-testid="composer-voice-note">
                {voice.talking ? t('chat.voice.recording') : voice.transcribing ? t('chat.voice.transcribing') : voiceNote}
              </p>
            )}

            <AttachPickers
              workspace={selectedWorkspace}
              projectId={selectedProject}
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
    </>
  );
}
