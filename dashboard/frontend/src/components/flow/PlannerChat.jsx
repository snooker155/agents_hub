import React, { useState, useEffect, useCallback, useRef } from 'react';
import { autoGrowTextarea } from '../../lib/autoGrow';
import { Square, MessageSquare, MessageSquarePlus, PanelRightClose, Send } from 'lucide-react';
import AgentTranscript from '../chat/AgentTranscript';
import ViewModeToggle from '../chat/ViewModeToggle';
import { useChatViewMode } from '../chat/viewMode';
import { agentMessage, applyTurnEvent, settleTurn } from '../chat/turnEvents';
import { messagesFromChatData } from '../chat/traceMessages';
import { genId } from '../chat/turnState';
import ChatSessionList, { ChatSessionsToggle } from '../ChatSessionList';
import { useChatSessions } from '../chatSessions';
import ContextMeter from '../ContextMeter';
import ComposerBox from '../chat/ComposerBox';
import { COMPOSER_SEND_CLS, COMPOSER_SIDE_TEXTAREA_CLS, COMPOSER_STOP_CLS } from '../chat/composerStyles';
import { useContextUsage } from '../contextUsage';
import { useI18n } from '../../i18n';
import { useInlineChatOpen } from '../pageChat/pageChat';
import {
  getProjectTasksChat, clearProjectTasksChat,
  streamProjectTasksGenerate, stopProjectGraphChat,
  getWorkspacePlannerChat, clearWorkspacePlannerChat,
  streamWorkspacePlanner, stopWorkspacePlanner,
} from '../../api';

// The calls behind one planner: a project's (its Tasks tab, which reads the
// project graphs) or a workspace's (the Tasks page, no graphs).
const plannerCalls = (projectId, workspace) => (projectId ? {
  load: () => getProjectTasksChat(projectId),
  clear: () => clearProjectTasksChat(projectId),
  stop: () => stopProjectGraphChat(projectId),
  stream: (opts) => streamProjectTasksGenerate({ projectId, ...opts }),
} : {
  load: () => getWorkspacePlannerChat(workspace),
  clear: () => clearWorkspacePlannerChat(workspace),
  stop: () => stopWorkspacePlanner(workspace),
  stream: (opts) => streamWorkspacePlanner({ workspace, ...opts }),
});

// Sidebar chat panel for the Tasks tab. The page's "Generate from
// architecture" button opens it with `generateRequested` set, and the panel
// runs the default plan once its transcript is loaded (then calls
// `onGenerateStarted` so the page clears the request); the chat input sends
// free-text refinements. Shows thinking + tool calls live and replays the
// session on reload. "New chat" files the thread in the history, the same
// way the other build chats do (common/entity_chat_store.py, kind projtasks).
// Calls onGenerated when tasks change. With `workspace` instead of
// `projectId` it plans over the whole workspace (the Tasks page).
function PlannerChat({ projectId, workspace, onGenerated, onClose, generateRequested = false, onGenerateStarted }) {
  const { t } = useI18n();
  // Drawn only while the Tasks tab has the planner open, so being mounted is
  // being on screen: the floating launcher stands down meanwhile.
  useInlineChatOpen();
  const calls = React.useMemo(() => plannerCalls(projectId, workspace), [projectId, workspace]);
  // The conversation, in the Chat page's message shape.
  const [messages, setMessages] = useState([]);
  const [streamingId, setStreamingId] = useState(null);
  const [viewMode, setViewMode] = useChatViewMode('side');
  // Planning sessions run long and re-send their transcript each turn: the
  // meter under the composer says how much of the model's window is left.
  const { usage: contextUsage, observe: observeContext, reset: resetContext } = useContextUsage();
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const abortRef = useRef(null);
  const feedRef = useRef(null);
  const textareaRef = useRef(null);
  // The thread store key the server reports, the history list it opens, and
  // whether the transcript has arrived (a requested run waits for it, or the
  // load would overwrite the run's first lines).
  const [chatRef, setChatRef] = useState(null);
  const [sessionsKey, setSessionsKey] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const sessionHistory = useChatSessions(chatRef, sessionsKey);


  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await calls.load();
        if (cancelled) return;
        setError('');
        setMessages(messagesFromChatData(data));
        setChatRef(data.chat_ref || null);
      } catch (e) {
        if (cancelled) return;
        setError(e?.response?.data?.detail || e.message || t('flowPlannerChat.loadFailed'));
        setMessages([]);
      }
      if (!cancelled) setLoaded(true);
    })();
    return () => { cancelled = true; };
  }, [calls, t]);
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);
  useEffect(() => { if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight; }, [messages]);

  // Run one planning turn. `message` empty → the default "generate from graphs".
  const run = useCallback(async (message) => {
    if (busy) return;
    setBusy(true); setError('');
    const shown = message || t('flowPlannerChat.generatePrompt');
    const replyId = genId();
    setMessages((list) => [...list, { id: genId(), role: 'user', content: shown }, agentMessage({ id: replyId })]);
    setStreamingId(replyId);
    const patch = (fn) => setMessages((list) => list.map((m) => (m.id === replyId ? fn(m) : m)));
    let stopped = false;
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await calls.stream({
        // The Generate button sends the prompt in the interface language too,
        // so the planner answers in it (the server's default is English).
        message: shown, signal: ac.signal,
        onEvent: (ev) => {
          observeContext(ev);
          switch (ev.type) {
            case 'meta':
              if (ev.run_id) patch((m) => ({ ...m, run_id: ev.run_id }));
              break;
            case 'message':
              if (ev.content) patch((m) => settleTurn({ ...m, run_id: ev.run_id || m.run_id }, { content: ev.content }));
              break;
            case 'error': {
              const text = ev.error || t('flowPlannerChat.generationFailed');
              patch((m) => ({ ...m, error: true, content: m.content ? `${m.content}\n\n${text}` : text }));
              break;
            }
            case 'stopped':
              stopped = true;
              break;
            case 'done':
              if (ev.created > 0 && typeof onGenerated === 'function') onGenerated();
              break;
            case 'thinking':
            case 'native_reasoning':
              // Log markers: the reasoning itself arrives as think events.
              break;
            default:
              patch((m) => applyTurnEvent(m, ev));
              break;
          }
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') {
        patch((m) => ({ ...m, error: true, content: m.content || e.message || t('flowPlannerChat.generationFailed') }));
      }
    } finally {
      // A run that ended (stopped, failed, or a tool whose end never came)
      // leaves nothing spinning.
      patch((m) => {
        const settled = settleTurn(m);
        if (stopped && !(settled.content || '').trim()) return { ...settled, content: t('flowPlannerChat.stoppedByYou') };
        return settled;
      });
      setStreamingId(null);
      setBusy(false);
      setStopping(false);
      abortRef.current = null;
    }
  }, [busy, t, calls, onGenerated, observeContext]);

  const onSend = useCallback(() => {
    const m = input.trim();
    if (!m) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    run(m);
  }, [input, run]);

  // Up to ten lines, then it scrolls (lib/autoGrow.js); follows the text
  // so a box emptied after a send shrinks back too.
  const resizeTextarea = useCallback(() => autoGrowTextarea(textareaRef.current), []);
  useEffect(() => { resizeTextarea(); }, [input, resizeTextarea]);

  const stop = useCallback(async () => {
    if (!busy || stopping) return;
    setStopping(true);
    try {
      await calls.stop();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowPlannerChat.stopFailed'));
      setStopping(false);
    }
  }, [busy, stopping, calls, t]);

  // "New chat": the server files the current thread in the history.
  const newChat = useCallback(async () => {
    if (busy) return;
    setError('');
    try {
      await calls.clear();
      setMessages([]);
      resetContext();
      setSessionsKey((k) => k + 1);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowPlannerChat.clearFailed'));
    }
  }, [busy, calls, resetContext, t]);

  // The page asked for a generation (its button opened this panel).
  // On a microtask, not straight from the effect body: starting the run sets
  // state, which an effect should not do synchronously.
  useEffect(() => {
    if (!generateRequested || !loaded || busy) return undefined;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      if (onGenerateStarted) onGenerateStarted();
      setHistoryOpen(false);
      run();
    });
    return () => { cancelled = true; };
  }, [generateRequested, loaded, busy, onGenerateStarted, run]);

  const pickSession = useCallback(async (session) => {
    if (session.active) { setHistoryOpen(false); return; }
    const data = await sessionHistory.activate(session.id);
    if (!data) return;
    setMessages(messagesFromChatData(data));
    setError('');
    resetContext();
    setHistoryOpen(false);
  }, [sessionHistory, resetContext]);

  const toggleHistory = useCallback(() => {
    setHistoryOpen((open) => {
      if (!open) sessionHistory.reload();
      return !open;
    });
  }, [sessionHistory]);

  return (
    <div className="w-[36rem] 2xl:w-[41.5rem] max-w-[50%] shrink-0 flex flex-col bg-white border border-gray-200 rounded-xl overflow-hidden">
      {/* A fixed height: the controls come and go with the conversation and
          the history list, and the row must not move when they do. */}
      <div className="flex items-center justify-between h-11 shrink-0 px-3 border-b border-gray-100 gap-2">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-700 min-w-0">
          <MessageSquare className={`w-4 h-4 shrink-0 ${busy ? 'text-emerald-500 animate-pulse' : 'text-gray-400'}`} />
          <span className="truncate">{t('flowPlannerChat.taskPlanner')}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {messages.length > 0 && !historyOpen && (
            <button onClick={newChat} disabled={busy} title={t('entityChat.clearChat')}
              className="inline-flex items-center gap-1 text-[11px] font-normal text-gray-400 hover:text-indigo-600 disabled:opacity-40">
              <MessageSquarePlus className="w-3.5 h-3.5" /> {t('entityChat.clear')}
            </button>
          )}
          {sessionHistory.hasHistory && (
            <ChatSessionsToggle open={historyOpen} count={sessionHistory.count} disabled={busy} onToggle={toggleHistory} />
          )}
          {messages.length > 0 && !historyOpen && <ViewModeToggle viewMode={viewMode} onChange={setViewMode} small />}
          {onClose && (
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600" title={t('flowPlannerChat.hidePlanner')}>
              <PanelRightClose className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {error ? <div className="px-4 py-2 text-xs text-red-600 bg-red-50 border-b border-red-100">{error}</div> : null}

      {/* History replaces the conversation in the same column; picking a
          thread turns it back into the chat showing that one. */}
      {historyOpen ? (
        <ChatSessionList
          sessions={sessionHistory.sessions}
          working={sessionHistory.working}
          error={sessionHistory.error}
          onPick={pickSession}
          onDelete={(session) => sessionHistory.remove(session.id)}
          onClose={() => setHistoryOpen(false)}
          className="flex-1 min-h-0 overflow-y-auto px-3 py-2"
        />
      ) : (<>
      <div ref={feedRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-2">
        {messages.length === 0 && !busy ? (
          <div className="text-xs text-gray-400 mt-2">
            {projectId
              ? t('flowPlannerChat.emptyIntro', { button: t('flowPlannerChat.generateFromArchitecture') })
              : t('flowPlannerChat.emptyIntroWorkspace')}{' '}
            <span className="italic">{t('flowPlannerChat.breakThePaymentsTaskInto')}</span>
          </div>
        ) : null}
        {messages.length > 0 && (
          <AgentTranscript
            messages={messages}
            viewMode={viewMode}
            streamingId={streamingId}
            compact
            onAction={busy ? null : run}
          />
        )}
        {busy ? <div className="text-[11px] text-emerald-500 animate-pulse">{stopping ? t('flowPlannerChat.stopping') : t('flowPlannerChat.planning')}</div> : null}
      </div>

      <div className="border-t border-gray-100 p-3">
        <ComposerBox
          actions={busy ? (
            <button onClick={stop} disabled={stopping} title={t('flowPlannerChat.stopTheCurrentRun')}
              className={COMPOSER_STOP_CLS}>
              <Square className="w-3 h-3" fill="currentColor" />
            </button>
          ) : (
            <button onClick={onSend} disabled={!input.trim()} title={t('flowPlannerChat.sendEnter')}
              className={COMPOSER_SEND_CLS}>
              <Send className="w-3.5 h-3.5" />
            </button>
          )}
          hint={busy ? (stopping ? t('flowPlannerChat.stopping') : t('flowPlannerChat.pressStop')) : t('flowPlannerChat.enterToSend')}
          footerRight={<ContextMeter usage={contextUsage} onClear={busy ? null : newChat} compact />}
        >
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            disabled={busy}
            onChange={(e) => { setInput(e.target.value); resizeTextarea(); }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); } }}
            placeholder={t('flowPlannerChat.askThePlannerToCreate')}
            className={COMPOSER_SIDE_TEXTAREA_CLS}
          />
        </ComposerBox>
      </div>
      </>)}
    </div>
  );
}

export default PlannerChat;
