import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Clock, Loader, Send, StopCircle, Terminal, X } from 'lucide-react';
import { getAgentDefinition } from '../../api';
import { steerRun } from '../../api/steering';
import MarkdownRenderer from '../MarkdownRenderer';
import { useChatComposerInput } from '../chat/useChatComposerInput';
import ComposerBox from '../chat/ComposerBox';
import { COMPOSER_SEND_CLS, COMPOSER_STOP_CLS, COMPOSER_TEXTAREA_CLS } from '../chat/composerStyles';
import { AttachButton, AttachPickers, PendingAttachments, PendingReferences } from '../chat/ComposerAttachments';
import { attachmentsPayload, referencesPayload } from '../chat/composerPayload';
import { STEER_MODES, loadSteerMode, readLocalSteerMode, saveSteerMode } from '../chat/steering';
import { useI18n } from '../../i18n';

/**
 * The box an instance is written to: the Chat page's composer, for one copy.
 *
 * Everything the main composer does is here, minus `/clear` — an instance's
 * conversation is its journal and cannot be wiped from the box: files from
 * the computer or the workspace and hub records attached to the message
 * (the server renders them under the text, see routes/instances.py), the
 * slash commands (`/help`, `/config`, the agent's own templates, and `/new`
 * where the copy holds several conversations), and a message typed while the
 * copy is answering, which goes one of three ways as on the Chat page:
 * steered into the running turn, interrupting it, or queued. Queueing is the
 * copy's own mailbox, so a queued message survives a reload; steering talks
 * to the live run the page is watching.
 *
 * The page owns the delivery: `onSend` posts the message to the instance and
 * answers true when it was taken. This component owns the box.
 *
 * @param {object} props
 * @param {object[]} props.agents      the workspace's agents (for `/config` and the agent's commands).
 * @param {string}   props.agentId     the agent answering: the copy's, or the runner's pick.
 * @param {boolean}  props.isRunner    a replica bound to no agent picks one per message.
 * @param {string}   props.runnerAgent the runner's current pick.
 * @param {function} props.onRunnerAgent
 * @param {boolean}  props.canNewConversation offers `/new` (resident copies).
 * @param {function} props.onNewConversation
 * @param {string}   props.workspace   for the file and entity pickers.
 * @param {string}   [props.projectId]
 * @param {boolean}  props.streaming   the copy is answering right now.
 * @param {string|null} props.liveRunId the run it is answering with, when known.
 * @param {function} props.onSend      ({message, attachments, references}) => Promise<boolean>
 * @param {function} props.onStop      stop the turn being answered.
 * @param {string}   props.hint        how a message reaches this copy.
 * @param {string}   [props.note]      the page's word on the last message (queued, steered, failed).
 * @param {number}   [props.pendingCount] messages waiting in the mailbox.
 */
export default function InstanceComposer({
  agents = [],
  agentId = '',
  isRunner = false,
  runnerAgent = '',
  onRunnerAgent = () => {},
  canNewConversation = false,
  onNewConversation = () => {},
  workspace = '',
  projectId = '',
  streaming = false,
  liveRunId = null,
  onSend,
  onStop = () => {},
  hint = '',
  note = '',
  pendingCount = 0,
}) {
  const { t } = useI18n();
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [pendingReferences, setPendingReferences] = useState([]);
  const [attachmentError, setAttachmentError] = useState('');
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [contextKinds, setContextKinds] = useState([]);
  const [pickerKind, setPickerKind] = useState(null);
  const [filePickerOpen, setFilePickerOpen] = useState(false);
  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandMenuIndex, setCommandMenuIndex] = useState(0);
  // What `/help` and `/config` answer: shown in the box, since the journal
  // above only holds what the copy itself said.
  const [localNote, setLocalNote] = useState('');
  const [steerNote, setSteerNote] = useState('');
  const [steerError, setSteerError] = useState('');

  const {
    resizeTextarea, onPickFiles, removeAttachment, toggleAttachmentStore,
    addReferences, removeReference, addWorkspaceFiles,
  } = useChatComposerInput({
    attachMenuOpen, contextKinds, pendingAttachments, selectedWorkspace: workspace,
    setAttachmentError, setContextKinds, setPendingAttachments, setPendingReferences, textareaRef,
  });

  // ---- steering mode: the account's choice, else this browser's (chat/steering.js) ----
  const [steerMode, setSteerModeState] = useState(readLocalSteerMode);
  const chosenHere = useRef(false);
  useEffect(() => {
    let live = true;
    loadSteerMode().then((m) => { if (live && !chosenHere.current) setSteerModeState(m); }).catch(() => {});
    return () => { live = false; };
  }, []);
  const setSteerMode = useCallback((m) => {
    chosenHere.current = true;
    setSteerModeState(m);
    saveSteerMode(m);
  }, []);
  const modes = streaming ? (liveRunId ? STEER_MODES : ['queue']) : [];
  const activeMode = modes.length ? (modes.includes(steerMode) ? steerMode : 'queue') : null;

  // ---- slash commands ----
  const agent = useMemo(() => (agents || []).find((a) => a.id === agentId) || null, [agents, agentId]);
  const allCommands = useMemo(() => [
    { name: '/help', descriptionKey: 'chat.commands.help', template: '/help' },
    ...(canNewConversation ? [{ name: '/new', descriptionKey: 'chat.commands.new', template: '/new' }] : []),
    { name: '/config', descriptionKey: 'chat.commands.config', template: '/config' },
    ...(agent?.commands || []),
  ], [agent, canNewConversation]);
  const commandSuggestions = useMemo(() => {
    if (!commandMenuOpen) return [];
    const query = input.toLowerCase();
    return allCommands.filter((cmd) => cmd.name.toLowerCase().startsWith(query));
  }, [commandMenuOpen, input, allCommands]);

  const focusBox = () => setTimeout(() => textareaRef.current?.focus(), 0);

  const selectCommand = useCallback(async (cmd) => {
    setCommandMenuOpen(false);
    setCommandMenuIndex(0);
    if (cmd.name === '/help') {
      const lines = allCommands
        .map((c) => `**${c.name}** — ${c.descriptionKey ? t(c.descriptionKey) : c.description}`)
        .join('\n');
      setLocalNote(`${t('chat.availableCommands')}\n\n${lines}`);
      setInput('');
      return;
    }
    if (cmd.name === '/new') {
      setInput('');
      onNewConversation();
      focusBox();
      return;
    }
    if (cmd.name === '/config') {
      const agentObj = agent || {};
      const inherit = t('chat.inheritGlobal');
      setInput('');
      let systemPrompt = agentObj.system_prompt || '';
      if (agentId) {
        try {
          const defResp = await getAgentDefinition(agentId);
          systemPrompt = defResp.data?.system_prompt || systemPrompt;
        } catch { /* keep what the list said */ }
      }
      const tools = (agentObj.tools || []).length > 0 ? (agentObj.tools || []).join(', ') : '—';
      setLocalNote([
        `**${t('chat.config.agent')}:** ${agentObj.name || agentId || '—'} (\`${agentObj.id || agentId || '—'}\`)`,
        `**${t('chat.config.description')}:** ${agentObj.description || '—'}`,
        '',
        `**${t('chat.config.provider')}:** ${agentObj.provider || inherit}`,
        `**${t('chat.config.model')}:** ${agentObj.model || inherit}`,
        `**${t('chat.config.baseUrl')}:** ${agentObj.base_url || '—'}`,
        `**${t('chat.config.temperature')}:** ${agentObj.temperature != null ? agentObj.temperature : inherit}`,
        `**${t('chat.config.maxTokens')}:** ${agentObj.max_tokens != null ? agentObj.max_tokens : inherit}`,
        `**${t('chat.config.streaming')}:** ${agentObj.streaming ? t('common.yes') : t('common.no')}`,
        `**${t('chat.config.verbose')}:** ${agentObj.verbose ? t('common.yes') : t('common.no')}`,
        '',
        `**${t('chat.config.tools')}:** ${tools}`,
        '',
        `**${t('chat.config.systemPrompt')}:**\n${systemPrompt || '—'}`,
      ].join('\n'));
      return;
    }
    setInput(cmd.template);
    focusBox();
  }, [agent, agentId, allCommands, onNewConversation, t]);

  // ---- sending ----
  const clearBox = () => {
    setInput('');
    setPendingAttachments([]);
    setPendingReferences([]);
    const ta = textareaRef.current;
    if (ta) ta.style.height = 'auto';
  };

  const deliver = async (text) => onSend({
    message: text,
    attachments: attachmentsPayload(pendingAttachments, workspace),
    references: referencesPayload(pendingReferences),
  });

  const send = async () => {
    const text = input.trim();
    if ((!text && !pendingAttachments.length && !pendingReferences.length) || sending) return;
    setSteerError('');
    setSteerNote('');
    setLocalNote('');
    setSending(true);
    try {
      // A message for the turn being answered, with nothing attached: into
      // the run itself. Anything else goes the ordinary way, which while the
      // copy is busy is its mailbox.
      const steerable = streaming && liveRunId && activeMode && activeMode !== 'queue'
        && text && !pendingAttachments.length && !pendingReferences.length;
      if (steerable) {
        try {
          await steerRun(liveRunId, text, activeMode);
          if (activeMode === 'interrupt') {
            // The stop lands first; the mailbox then hands the message to
            // the copy as its next turn.
            if (!(await deliver(text))) return;
            setSteerNote(t('instanceDetail.composer.interrupted'));
          } else {
            setSteerNote(t('instanceDetail.composer.injected'));
          }
          clearBox();
          return;
        } catch (err) {
          // The turn ended between the key press and the post: not lost,
          // the copy answers it as the next message.
          if (err?.response?.status !== 409) {
            const detail = err?.response?.data?.detail;
            setSteerError(t('steering.failed', { error: (detail && (detail.message || detail)) || err?.message || '' }));
            return;
          }
        }
      }
      if (await deliver(text)) clearBox();
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e) => {
    if (commandMenuOpen && commandSuggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCommandMenuIndex((i) => Math.min(i + 1, commandSuggestions.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCommandMenuIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        selectCommand(commandSuggestions[commandMenuIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setCommandMenuOpen(false);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const canSend = (input.trim() || pendingAttachments.length > 0 || pendingReferences.length > 0)
    && !sending && !(isRunner && !runnerAgent);

  return (
    <div data-testid="instance-composer">
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={onPickFiles} />

      {localNote && (
        <div className="relative mb-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 pr-8 text-xs text-gray-700 max-h-64 overflow-y-auto">
          <button
            type="button"
            onClick={() => setLocalNote('')}
            className="absolute top-1.5 right-1.5 p-1 text-gray-400 hover:text-gray-600"
            aria-label={t('common.close', { defaultValue: 'Close' })}
          >
            <X className="w-3.5 h-3.5" />
          </button>
          <MarkdownRenderer content={localNote} />
        </div>
      )}

      <PendingReferences references={pendingReferences} onRemove={removeReference} disabled={sending} />
      <PendingAttachments
        attachments={pendingAttachments}
        workspace={workspace}
        onToggleStore={toggleAttachmentStore}
        onRemove={removeAttachment}
        disabled={sending}
      />

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
              <span className="text-sm font-semibold text-indigo-600 shrink-0">{cmd.name}</span>
              <span className="text-xs text-gray-500 mt-0.5">{cmd.descriptionKey ? t(cmd.descriptionKey) : cmd.description}</span>
            </button>
          ))}
        </div>
      )}

      {streaming && (
        <div className="mb-2 flex items-center gap-2 min-w-0" data-testid="instance-steer-bar">
          {modes.length > 1 ? (
            <>
              <div className="inline-flex flex-shrink-0 rounded-full border border-gray-200 bg-gray-50 p-0.5" role="radiogroup">
                {modes.map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={activeMode === m}
                    onClick={() => setSteerMode(m)}
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
            </>
          ) : (
            <span className="text-[11px] text-gray-400">{t('steering.queueOnlyHint')}</span>
          )}
        </div>
      )}

      {/* A runner picks the agent per message: the picker stands to the left
          of the box, as tall as the box is. */}
      <div className="flex items-stretch gap-2">
      {isRunner && (
        <select
          value={runnerAgent}
          onChange={(e) => onRunnerAgent(e.target.value)}
          className="flex-shrink-0 max-w-[14rem] px-3 text-sm border border-gray-300 rounded-2xl bg-white
            focus:outline-none"
          aria-label={t('instanceDetail.message.runnerAgent')}
        >
          <option value="">{t('instanceDetail.message.runnerAgent')}</option>
          {(agents || []).map((a) => (
            <option key={a.id} value={a.id}>{a.name || a.id}</option>
          ))}
        </select>
      )}

      <div className="flex-1 min-w-0">
      <ComposerBox
        leading={(
          <AttachButton
            open={attachMenuOpen}
            onOpenChange={setAttachMenuOpen}
            disabled={sending}
            contextKinds={contextKinds}
            onFiles={() => fileInputRef.current?.click()}
            onWorkspaceFiles={() => setFilePickerOpen(true)}
            onKind={setPickerKind}
          />
        )}
        actions={streaming ? (
          <>
            {canSend && (
              <button
                type="button"
                onClick={send}
                title={activeMode ? t(`steering.modeHints.${activeMode}`) : t('steering.send')}
                data-testid="instance-steer-send"
                className="flex-shrink-0 h-8 px-3 flex items-center gap-1.5 rounded-full bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-semibold transition-colors"
              >
                <Send className="w-3.5 h-3.5" />
                {activeMode ? t(`steering.modes.${activeMode}`) : t('steering.send')}
              </button>
            )}
            <button
              type="button"
              onClick={onStop}
              title={t('chat.stop')}
              className={COMPOSER_STOP_CLS}
            >
              <StopCircle className="w-4 h-4" />
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            title={t('chat.sendEnter')}
            className={COMPOSER_SEND_CLS}
          >
            {sending ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
          </button>
        )}
        hint={<>{hint && <>{hint} · </>}{t('chat.composerHint')} <span>/</span> {t('chat.forCommands')}</>}
      >
        <textarea
          ref={textareaRef}
          rows={1}
          value={input}
          onChange={(e) => {
            const val = e.target.value;
            setInput(val);
            resizeTextarea();
            setCommandMenuOpen(val.startsWith('/'));
            setCommandMenuIndex(0);
          }}
          onKeyDown={onKeyDown}
          placeholder={streaming ? t('steering.placeholderBusy') : t('instanceDetail.message.placeholder')}
          className={COMPOSER_TEXTAREA_CLS}
        />
      </ComposerBox>
      </div>
      </div>

      {attachmentError && <p className="text-xs text-red-600 mt-2">{attachmentError}</p>}
      {steerError && <p className="text-xs text-red-600 mt-2">{steerError}</p>}
      {steerNote && <p className="text-xs text-gray-500 mt-2">{steerNote}</p>}
      {note && <p className="text-xs text-amber-700 mt-2">{note}</p>}
      {pendingCount > 0 && (
        <p className="mt-2 text-xs text-gray-500 inline-flex items-center gap-1.5">
          <Clock className="w-3 h-3" />
          {t('instanceDetail.message.pending', { count: pendingCount })}
        </p>
      )}

      <AttachPickers
        workspace={workspace}
        projectId={projectId}
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
  );
}
