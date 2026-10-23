import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MessageSquare, MessageSquarePlus, PanelRightClose, Send, StopCircle } from 'lucide-react';
import { streamEntityChat } from '../api';
import ChatSessionList, { ChatSessionsToggle } from './ChatSessionList';
import { useChatSessions } from './chatSessions';
import AgentTranscript from './chat/AgentTranscript';
import ViewModeToggle from './chat/ViewModeToggle';
import { useChatViewMode } from './chat/viewMode';
import { agentMessage, applyTurnEvent, settleTurn } from './chat/turnEvents';
import { messagesFromChatData } from './chat/traceMessages';
import { genId } from './chat/turnState';
import ContextMeter from './ContextMeter';
import { useContextUsage } from './contextUsage';
import { useI18n } from '../i18n';
import { useInlineChatOpen } from './pageChat/pageChat';
import { autoGrowTextarea } from '../lib/autoGrow';

/**
 * A build chat pinned to one service entity.
 *
 * The same surface the project graph has had: you say what you want, the agent
 * edits the thing itself with its tools, and the panel next to the chat updates.
 * It is deliberately not the Chat page in miniature: no agent picker and no
 * attachments, because the conversation is *about* the entity on screen. It
 * does keep its past threads, though. Starting a new chat files the old one
 * rather than ending it, and History, at the end of the title row, puts the
 * list of them in place of the conversation, filling the column; picking one
 * opens it right there (see `ChatSessionList` and
 * `common/entity_chat_store.py`).
 *
 * A turn is drawn the way the Chat page draws one (chat/AgentTranscript.jsx),
 * in the Chat or the Build view the person chose there: the reasoning as it is
 * written, each tool as it is called and returns, the files it touched, and,
 * the point of the whole surface, every change the agent makes to the entity,
 * announced at the same moment it appears in the panel beside the chat. The
 * stream is read by the reducer every chat shares (chat/turnEvents.js).
 *
 * The parent owns the entity. This component owns the conversation: it loads
 * the stored transcript, streams a turn, and hands every event up through
 * `onEvent` so the parent can apply the entity payload the backend sends at the
 * end of a turn without a refetch.
 *
 * @param {object}   props
 * @param {string}   props.path        API path for the streaming turn (e.g. `scenarioChatUrl(id)`).
 * @param {object}   [props.body]      extra fields sent with every turn, read at
 *   send time rather than at render time — the page chat's subject is whatever
 *   is on screen *now*, which may have changed while the user typed.
 * @param {function} props.loadChat    () => Promise<{data: {messages, trace}}>
 * @param {function} props.clearChat   () => Promise<any>
 * @param {function} props.stopChat    () => Promise<any>
 * @param {function} [props.onEvent]   called with every stream event.
 * @param {function} [props.registerComposer] handed a prefill(text) that puts
 *   text in the composer and focuses it, for a host that starts a message for
 *   the user to finish (a code view's Discuss and Edit).
 * @param {function} [props.registerSend] handed this chat's send(text), so a
 *   page can post a turn from outside the composer — a control the agent
 *   authored, a button inside the view it built. Called with null on unmount.
 *   Keep it stable (useCallback): it is an effect dependency.
 * @param {string}   [props.title]     panel heading.
 * @param {React.ReactNode|false} [props.header] shown instead of the heading.
 *   Pass `false` where the card around the chat already carries its title (a
 *   tab header, say): the row then holds only Clear, so the panel is not
 *   titled twice.
 * @param {HTMLElement} [props.clearTarget] where to draw Clear. Hosts that
 *   title the chat themselves (a panel header with a fold-away button) pass a
 *   slot from that header, so Clear sits beside the other chat controls instead
 *   of on a row of its own inside the feed.
 * @param {string[]} [props.suggestions] one-click opening prompts, shown while empty.
 * @param {string}   [props.emptyHint] what to say when there is nothing yet.
 * @param {string}   [props.heightClass] feed height; the default suits a sidebar.
 * @param {string}   [props.className] extra classes on the panel root — pass
 *   `flex-1 min-h-0` where the chat should fill the card it sits in, so the
 *   composer lands on the card's floor instead of under the last message.
 * @param {boolean}  [props.inline]    false only for the copy inside the floating
 *   panel. Every other one is a chat the page is drawing itself, which is what
 *   tells the panel's launcher to stand down (see `useInlineChatOpen`).
 * @param {function} [props.renderReply] text => nodes for the agent's replies,
 *   for a host that renders more than plain text (the Help panel's links).
 * @param {string}   [props.composerClassName] extra classes on the composer
 *   block, replacing its default top margin — `mt-auto` pins it to the bottom
 *   of a panel that fills its card, and negative margins take its rule out to
 *   the card's edges past the card's own padding.
 */
export default function EntityChat({
  path,
  body = null,
  loadChat,
  clearChat,
  stopChat,
  onEvent = null,
  registerSend = null,
  registerComposer = null,
  title,
  header = null,
  clearTarget = null,
  suggestions = [],
  emptyHint = '',
  heightClass = 'max-h-[28rem] min-h-[14rem]',
  className = '',
  inline = true,
  composerClassName = '',
  onHide = null,
  // Accepted for hosts that used to render the replies themselves (the Help
  // panel): the transcript renders markdown itself now, links included.
  renderReply: _renderReply = null,
}) {
  const { t } = useI18n();
  useInlineChatOpen(inline);
  // The conversation, in the Chat page's message shape.
  const [messages, setMessages] = useState([]);
  // The reply being written, while a turn is open.
  const [streamingId, setStreamingId] = useState(null);
  const [viewMode, setViewMode] = useChatViewMode();
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  // This chat's storage key, as the server reports it (`chat_ref`), and what
  // the session history is driven by. A chat whose route does not send one
  // simply has no history.
  const [chatRef, setChatRef] = useState(null);
  // Bumped whenever the thread list may have changed, so it reloads without
  // polling: a turn just landed, or a new thread was started.
  const [sessionsKey, setSessionsKey] = useState(0);
  // Is the column showing the past threads instead of the conversation?
  const [historyOpen, setHistoryOpen] = useState(false);
  // How full the model's context is. The transcript is re-sent whole on every
  // turn, so a long build chat walks into the model's ceiling; the meter shows
  // the approach and Clear is the way back.
  const { usage: contextUsage, observe: observeContext, reset: resetContext } = useContextUsage();
  const abortRef = useRef(null);
  const feedRef = useRef(null);
  const textareaRef = useRef(null);
  // The turn's extra payload, kept in a ref so `runTurn` does not have to be
  // rebuilt (and the composer re-rendered) every time the page beneath the
  // chat changes what it is showing.
  const bodyRef = useRef(body);
  useLayoutEffect(() => { bodyRef.current = body; });

  // The threads this chat has been. Loaded from the key the server reports, so
  // no page has to know its own chat's storage key.
  const sessionHistory = useChatSessions(chatRef, sessionsKey);

  // The rich trace when there is one (so a reload shows the thinking and tool
  // steps as they happened), else the bare transcript. Used for both ways a
  // transcript arrives: loading this chat, and switching to an older thread.
  const feedFrom = messagesFromChatData;

  // The stored session: the live thread, plus the key the switcher needs to
  // reach the ones behind it.
  // `loading` starts true, so the first load needs no synchronous reset here.
  // The ignore flag drops a response that arrives after the chat was swapped.
  useEffect(() => {
    let ignore = false;
    const load = async () => {
      try {
        const { data } = await loadChat();
        if (ignore) return;
        setMessages(feedFrom(data));
        setChatRef(data?.chat_ref || null);
        setError('');
      } catch (e) {
        if (ignore) return;
        setError(e?.response?.data?.detail || e.message || t('entityChat.loadFailed'));
        setMessages([]);
      } finally {
        if (!ignore) setLoading(false);
      }
    };
    load();
    return () => { ignore = true; };
  }, [loadChat, feedFrom, t]);

  // Abort the reader on unmount. The run itself is detached server-side, so it
  // finishes and persists regardless — this only drops our listener.
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  // Follow the run, so a turn keeps its newest line in view without the user
  // chasing it.
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [messages, busy]);

  const runTurn = useCallback(async (message) => {
    if (!message.trim() || busy) return;
    setBusy(true);
    setError('');
    const replyId = genId();
    setMessages((list) => [...list, { id: genId(), role: 'user', content: message }, agentMessage({ id: replyId })]);
    setStreamingId(replyId);
    const patch = (fn) => setMessages((list) => list.map((m) => (m.id === replyId ? fn(m) : m)));
    let stopped = false;

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamEntityChat({
        path,
        message,
        body: bodyRef.current,
        signal: ac.signal,
        onEvent: (ev) => {
          switch (ev.type) {
            case 'meta':
              if (ev.run_id) patch((m) => ({ ...m, run_id: ev.run_id }));
              break;
            case 'message':
              // The turn's authoritative reply: the server may have cleaned up
              // what was streamed, or written a summary of what changed for an
              // agent that went silent.
              patch((m) => settleTurn({ ...m, run_id: ev.run_id || m.run_id }, { content: ev.content }));
              break;
            case 'stopped':
              stopped = true;
              break;
            case 'error':
              patch((m) => ({
                ...m, error: true,
                content: m.content ? `${m.content}\n\n${ev.error || 'error'}` : (ev.error || 'error'),
              }));
              break;
            default:
              // Thoughts, tools, text, files and entity changes: the reducer
              // every chat shares.
              patch((m) => applyTurnEvent(m, ev));
              break;
          }
          observeContext(ev);
          if (onEvent) onEvent(ev);
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') {
        patch((m) => ({ ...m, error: true, content: m.content || e.message || t('entityChat.turnFailed') }));
      }
    } finally {
      patch((m) => {
        const settled = settleTurn(m);
        // A turn stopped before it said anything still says why it ended.
        if (stopped && !(settled.content || '').trim()) return { ...settled, content: t('entityChat.stoppedByYou') };
        return settled;
      });
      setStreamingId(null);
      setBusy(false);
      setStopping(false);
      abortRef.current = null;
      // The live thread just grew, so its label and size in the switcher are
      // stale; a chat's first turn is also what creates the thread at all.
      setSessionsKey((k) => k + 1);
    }
  }, [busy, path, onEvent, observeContext, t]);

  // A page that drives this chat from its own buttons needs the same send the
  // composer uses — the turn must land in this transcript, not in a second one.
  useEffect(() => {
    if (!registerSend) return undefined;
    registerSend(runTurn);
    return () => registerSend(null);
  }, [registerSend, runTurn]);

  const prefill = useCallback((text) => {
    setInput(text);
    textareaRef.current?.focus();
  }, []);
  useEffect(() => {
    if (!registerComposer) return undefined;
    registerComposer(prefill);
    return () => registerComposer(null);
  }, [registerComposer, prefill]);

  const onSend = useCallback(() => {
    const message = input.trim();
    if (!message) return;
    setInput('');
    runTurn(message);
  }, [input, runTurn]);

  // The box follows its text: typed, put there by a host (Discuss, a run
  // report) or emptied after a send, up to ten lines, then it scrolls.
  useEffect(() => { autoGrowTextarea(textareaRef.current); }, [input]);

  // Stopping has to happen server-side: the agent runs detached from this
  // connection, so aborting the fetch alone would leave it running.
  const onStop = useCallback(async () => {
    if (!busy || stopping) return;
    setStopping(true);
    try {
      await stopChat();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('entityChat.stopFailed'));
      setStopping(false);
    }
  }, [busy, stopping, stopChat, t]);

  // "Clear" starts a new thread rather than destroying the old one: the store
  // files it, and the switcher beside the title is the way back to it.
  const onClear = useCallback(async () => {
    if (busy) return;
    try {
      await clearChat();
      setMessages([]);
      setError('');
      resetContext();
      setSessionsKey((k) => k + 1);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('entityChat.clearFailed'));
    }
  }, [busy, clearChat, resetContext, t]);

  // A thread was picked from the list. The live one is just the way back; any
  // other is swapped in server-side and comes back with its transcript, so the
  // column returns to being a chat already showing it. The meter is reset
  // because the fill it held belonged to the thread we just left.
  const onPickSession = useCallback(async (session) => {
    if (session.active) { setHistoryOpen(false); return; }
    const data = await sessionHistory.activate(session.id);
    if (!data) return;
    setMessages(feedFrom(data));
    setError('');
    resetContext();
    setHistoryOpen(false);
  }, [sessionHistory, feedFrom, resetContext]);

  const onDeleteSession = useCallback((session) => {
    sessionHistory.remove(session.id);
  }, [sessionHistory]);

  // History and the conversation are the same column, so opening one closes
  // the other. Reopening the list refetches it: a turn may have landed, or
  // another tab may have started a thread, while it was closed.
  const toggleHistory = useCallback(() => {
    setHistoryOpen((open) => {
      if (!open) sessionHistory.reload();
      return !open;
    });
  }, [sessionHistory]);

  // Two controls, two homes: the host's header when it offered a slot for
  // them, this panel's own row otherwise. New chat leads, History follows.
  const clearButton = messages.length > 0 && !historyOpen ? (
    <button
      onClick={onClear} disabled={busy}
      title={t('entityChat.clearChat')}
      className={clearTarget
        ? 'inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-500 hover:text-red-600 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40'
        : 'inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-red-600 disabled:opacity-40'}
    >
      <MessageSquarePlus className="w-3.5 h-3.5" /> {t('entityChat.clear')}
    </button>
  ) : null;

  // Shown from the first "New chat" onwards, and it stays shown while an older
  // thread is being read: the way back to the list must not depend on how many
  // threads happen to be left beside the one on screen.
  // Chat or Build, the same choice as on the Chat page (chat/viewMode.js).
  const viewToggle = !historyOpen && messages.length > 0
    ? <ViewModeToggle viewMode={viewMode} onChange={setViewMode} small />
    : null;

  const historyToggle = sessionHistory.hasHistory ? (
    <ChatSessionsToggle
      open={historyOpen}
      count={sessionHistory.count}
      // Mid-turn the server refuses the swap (the running answer would be filed
      // in whichever thread was live when it ended), so it is not offered.
      disabled={busy}
      onToggle={toggleHistory}
    />
  ) : null;

  // The row carries the title, History and New chat. Kept without its margin
  // when it has nothing to show, so an untitled chat gains no empty rule.
  const showRow = header !== false || !clearTarget || Boolean(onHide);
  const rowFilled = header !== false || Boolean(clearButton) || Boolean(viewToggle) || sessionHistory.hasHistory;

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      {clearTarget && createPortal(
        <>{clearButton}{viewToggle}{historyToggle}</>, clearTarget,
      )}
      {showRow && (
        // A fixed height, so the row does not move when the controls in it
        // come and go (History open, a new empty chat).
        <div className={`shrink-0 flex items-center justify-between gap-2 ${rowFilled ? 'mb-2 h-8' : ''}`}>
          <div className="flex items-center gap-1 min-w-0">
            {header === false ? <span /> : (header || (
              <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide flex items-center gap-1.5">
                <MessageSquare className={`w-4 h-4 ${busy ? 'text-violet-500 animate-pulse' : 'text-gray-400'}`} />
                {title || t('entityChat.title')}
              </h3>
            ))}
          </div>
          {/* The chat's controls on the right, in the same order as every
              other chat: New chat, Chat/Build, History. */}
          <div className="flex items-center gap-1 shrink-0">
            {!clearTarget && clearButton}
            {!clearTarget && viewToggle}
            {!clearTarget && historyToggle}
            {/* Folds the column away; the page's header button brings it back. */}
            {onHide && (
              <button type="button" onClick={onHide} title={t('entityChat.hide')} aria-label={t('entityChat.hide')}
                className="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100">
                <PanelRightClose className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      )}

      {error && <div className="mb-2 text-[11px] text-red-600">{error}</div>}

      {/* History is not a layer over the conversation: it replaces it, in the
          column the chat already fills, and picking a thread turns the column
          back into a chat showing that one. */}
      {historyOpen ? (
        <ChatSessionList
          sessions={sessionHistory.sessions}
          working={sessionHistory.working}
          error={sessionHistory.error}
          onPick={onPickSession}
          onDelete={onDeleteSession}
          onClose={() => setHistoryOpen(false)}
          className={`flex-1 overflow-y-auto pr-1 ${heightClass}`}
        />
      ) : (
      <div
        ref={feedRef}
        className={`flex-1 overflow-y-auto pr-1 ${heightClass}`}
      >
        {loading ? (
          <div className="text-xs text-gray-400">{t('common.loading')}</div>
        ) : messages.length === 0 ? (
          <div className="space-y-3">
            <p className="text-sm text-gray-500 leading-relaxed">
              {emptyHint || t('entityChat.emptyHint')}
            </p>
            {/* Openers, not a menu: the hard part of a build chat is the first
                sentence, and these are the three it is usually made of. */}
            {suggestions.length > 0 && (
              <div className="flex flex-col gap-1.5">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    onClick={() => runTurn(s)}
                    className="text-left text-xs text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-md px-2.5 py-1.5 hover:bg-indigo-100"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <AgentTranscript
            messages={messages}
            viewMode={viewMode}
            streamingId={streamingId}
            compact
            onAction={busy ? null : runTurn}
          />
        )}
        {stopping && <div className="text-xs text-amber-600">{t('entityChat.stopping')}</div>}
      </div>
      )}

      {/* The composer is the panel's floor. Where the panel fills its card the
          caller passes `mt-auto` to pin it to the bottom however short the feed
          is, plus the negative margins that draw its rule across the card's
          whole width instead of stopping at the padding. It steps aside with
          the conversation: there is nothing to write to while browsing. */}
      {!historyOpen && (
      <div className={`shrink-0 pt-3 border-t border-gray-200 ${composerClassName || 'mt-3'}`}>
        <ContextMeter usage={contextUsage} onClear={busy ? null : onClear} className="mb-2" />
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
            }}
            placeholder={t('entityChat.placeholder')}
            disabled={busy}
            // One line is 40px (line 20 + padding 16 + border 2), the buttons'
            // h-10, so the row lines up until the field grows.
            className="flex-1 resize-none text-sm leading-5 border border-gray-300 rounded-lg px-3 py-2 focus:outline-none disabled:bg-gray-50"
          />
          {busy ? (
            <button
              onClick={onStop} disabled={stopping}
              title={t('entityChat.stop')}
              className="h-10 w-10 inline-flex items-center justify-center shrink-0 rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
            >
              <StopCircle className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={onSend} disabled={!input.trim()}
              title={t('entityChat.send')}
              className="h-10 w-10 inline-flex items-center justify-center shrink-0 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
