import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Brain, Loader2, MessageSquare, MessageSquarePlus, Send, StopCircle } from 'lucide-react';
import { streamEntityChat } from '../api';
import ChatSessionList, { ChatSessionsToggle } from './ChatSessionList';
import { useChatSessions } from './chatSessions';
import { FeedItem } from './flow/ChatFeed';
import ContextMeter from './ContextMeter';
import { useContextUsage } from './contextUsage';
import { useI18n } from '../i18n';
import { useInlineChatOpen } from './pageChat/pageChat';

//: A long thought would otherwise leave the chat blank for many seconds — the
//: reasoning only becomes a `think` step once it is finished. The ticker shows
//: its tail meanwhile, so the buffer never needs to be longer than a few lines.
const MAX_LIVE_THOUGHT_CHARS = 2000;

/**
 * The model's reasoning as it is being written: a clipped three-line box whose
 * content is bottom-aligned, so the newest lines stay in view without any
 * scrolling of our own. Replaced by the completed thought when it arrives.
 */
function LiveThought({ text }) {
  if (!text || !text.trim()) return null;
  return (
    <div className="flex items-start gap-1.5">
      <Brain className="w-3.5 h-3.5 mt-0.5 text-amber-400 shrink-0 animate-pulse" />
      <div className="flex-1 min-w-0 h-[3.25rem] overflow-hidden flex flex-col justify-end">
        <div className="text-[11px] leading-[1.1rem] text-gray-400 italic whitespace-pre-wrap break-words">
          {text}
        </div>
      </div>
    </div>
  );
}

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
 * A turn is shown as it happens, not summarised when it ends: the reasoning as
 * it is written, each tool as it is called and returns, and — the point of the
 * whole surface — every change the agent makes to the entity, announced here at
 * the same moment it appears in the panel beside the chat. A build that takes a
 * minute is a minute of watching it being built, not of watching a spinner.
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
  title,
  header = null,
  clearTarget = null,
  suggestions = [],
  emptyHint = '',
  heightClass = 'max-h-[28rem] min-h-[14rem]',
  className = '',
  inline = true,
  composerClassName = '',
}) {
  const { t } = useI18n();
  useInlineChatOpen(inline);
  const [feed, setFeed] = useState([]);
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
  // Reasoning still being written, shown as a ticker until the finished thought
  // takes its place in the feed.
  const [liveThought, setLiveThought] = useState('');
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
  bodyRef.current = body;

  // The threads this chat has been. Loaded from the key the server reports, so
  // no page has to know its own chat's storage key.
  const sessionHistory = useChatSessions(chatRef, sessionsKey);

  // The rich trace when there is one (so a reload shows the thinking and tool
  // steps as they happened), else the bare transcript. Used for both ways a
  // transcript arrives: loading this chat, and switching to an older thread.
  const feedFrom = useCallback((data) => {
    const trace = data?.trace;
    if (Array.isArray(trace) && trace.length) return trace;
    return (data?.messages || []).map((m) => ({ k: m.role, text: m.content }));
  }, []);

  // The stored session: the live thread, plus the key the switcher needs to
  // reach the ones behind it.
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await loadChat();
      setFeed(feedFrom(data));
      setChatRef(data?.chat_ref || null);
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('entityChat.loadFailed'));
      setFeed([]);
    } finally {
      setLoading(false);
    }
  }, [loadChat, feedFrom, t]);

  useEffect(() => { load(); }, [load]);

  // Abort the reader on unmount. The run itself is detached server-side, so it
  // finishes and persists regardless — this only drops our listener.
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  // Follow the run: the ticker and the working line grow the feed too, so a
  // turn keeps its newest line in view without the user chasing it.
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [feed, liveThought, busy]);

  const runTurn = useCallback(async (message) => {
    if (!message.trim() || busy) return;
    setBusy(true);
    setError('');
    setLiveThought('');
    setFeed((f) => [...f, { k: 'user', text: message }]);

    const append = (item) => setFeed((f) => [...f, item]);
    // A tool step, the live reply bubble and a completed thought are all "the
    // latest thing": closing the bubble before anything else is appended is
    // what keeps the transcript in execution order instead of collapsing a
    // turn's worth of steps into one block of text at the end.
    const closeLive = (items) => items.map((it) => (it.live ? { ...it, live: false } : it));
    const appendAfterLive = (item) => setFeed((f) => [...closeLive(f), item]);
    // Mark the tool that is still running — the last one, since a turn runs
    // them one at a time — as finished, or as the failure it turned out to be.
    const settleTool = (patch) => setFeed((f) => {
      const i = f.map((it) => it.k === 'tool' && it.status === 'running').lastIndexOf(true);
      if (i === -1) return f;
      const copy = [...f];
      copy[i] = { ...copy[i], ...patch };
      return copy;
    });

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamEntityChat({
        path,
        message,
        body: bodyRef.current,
        signal: ac.signal,
        onEvent: (ev) => {
          // Kept in sync with `trace_item_for` in chat/entity_chat.py, so a
          // reloaded session renders the same as the live run did.
          switch (ev.type) {
            case 'token':
              // The reply as it is written. A tool step in between closes the
              // bubble, so the next token opens a fresh one under it.
              setFeed((f) => {
                const last = f[f.length - 1];
                if (last?.k === 'assistant' && last.live) {
                  return [...f.slice(0, -1),
                          { ...last, text: last.text + (ev.token || '') }];
                }
                return [...f, { k: 'assistant', text: ev.token || '', live: true }];
              });
              break;
            case 'tool_start':
              appendAfterLive({ k: 'tool', tool: ev.tool, input: ev.input, status: 'running' });
              break;
            case 'tool_end':
              settleTool({ status: 'done' });
              break;
            case 'tool_error':
              settleTool({ status: 'error', error: ev.error || '' });
              break;
            case 'think_delta':
              // A slice of reasoning still being written: a ticker under the
              // feed until the completed thought replaces it.
              setLiveThought((prev) => {
                const text = `${prev}${ev.delta || ''}`;
                return text.length > MAX_LIVE_THOUGHT_CHARS
                  ? text.slice(-MAX_LIVE_THOUGHT_CHARS) : text;
              });
              break;
            case 'think':
              setLiveThought('');
              if (ev.content) appendAfterLive({ k: 'thinking', text: ev.content });
              break;
            case 'thinking':
            case 'native_reasoning': {
              const text = ev.message || ev.content || '';
              // "[llm_start] …" and friends are log markers, not the model's words.
              if (text && !text.startsWith('[')) appendAfterLive({ k: 'thinking', text });
              break;
            }
            // The entity itself changed — the same edit the panel beside this
            // chat is showing as this line lands.
            case 'entity_changed':
              appendAfterLive({
                k: 'entity', action: ev.action || 'updated',
                kind: ev.kind || '', label: ev.label || '',
              });
              break;
            case 'stopped':
              appendAfterLive({ k: 'tool', tool: t('entityChat.stoppedByYou'), status: 'done' });
              break;
            case 'message':
              // The turn's authoritative reply. Where it was streamed, it
              // replaces the live bubble rather than repeating under it: the
              // server may have cleaned it up, or substituted a summary of what
              // changed for an agent that went silent.
              setLiveThought('');
              if (ev.content) {
                setFeed((f) => {
                  const last = f[f.length - 1];
                  if (last?.k === 'assistant' && last.live) {
                    return [...f.slice(0, -1), { k: 'assistant', text: ev.content }];
                  }
                  return [...closeLive(f), { k: 'assistant', text: ev.content }];
                });
              } else {
                setFeed(closeLive);
              }
              break;
            case 'error':
              appendAfterLive({ k: 'error', text: ev.error || 'error' });
              break;
            default:
              break;
          }
          observeContext(ev);
          if (onEvent) onEvent(ev);
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') {
        append({ k: 'error', text: e.message || t('entityChat.turnFailed') });
      }
    } finally {
      setBusy(false);
      setStopping(false);
      setLiveThought('');
      setFeed(closeLive);
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

  const onSend = useCallback(() => {
    const message = input.trim();
    if (!message) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    runTurn(message);
  }, [input, runTurn]);

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
      setFeed([]);
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
    setFeed(feedFrom(data));
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
  const clearButton = feed.length > 0 && !historyOpen ? (
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
  const showRow = header !== false || !clearTarget;
  const rowFilled = header !== false || Boolean(clearButton) || sessionHistory.hasHistory;

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      {clearTarget && createPortal(
        <>{clearButton}{historyToggle}</>, clearTarget,
      )}
      {showRow && (
        <div className={`shrink-0 flex items-center justify-between gap-2 ${rowFilled ? 'mb-2' : ''}`}>
          <div className="flex items-center gap-1 min-w-0">
            {header === false ? <span /> : (header || (
              <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide flex items-center gap-1.5">
                <MessageSquare className={`w-4 h-4 ${busy ? 'text-violet-500 animate-pulse' : 'text-gray-400'}`} />
                {title || t('entityChat.title')}
              </h3>
            ))}
            {/* New chat sits with the name, because it acts on the
                conversation the name is showing. History closes the row: it is
                about the chats beside this one. */}
            {!clearTarget && clearButton}
          </div>
          {!clearTarget && historyToggle}
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
        className={`flex-1 overflow-y-auto space-y-2 pr-1 ${heightClass}`}
      >
        {loading ? (
          <div className="text-xs text-gray-400">{t('common.loading')}</div>
        ) : feed.length === 0 ? (
          <div className="space-y-3">
            <p className="text-xs text-gray-500 leading-relaxed">
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
                    className="text-left text-[11px] text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-md px-2.5 py-1.5 hover:bg-indigo-100"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          feed.map((e, i) => <FeedItem key={i} e={e} />)
        )}

        {/* The run's floor: something is always showing while a turn is open,
            even in the gap between the last tool returning and the next thought
            starting — that silence is what made this chat look dead. */}
        {busy && (
          <>
            <div className="flex items-center gap-1.5 text-[11px] text-violet-500">
              <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin" />
              {stopping ? t('entityChat.stopping') : t('entityChat.working')}
            </div>
            <LiveThought text={liveThought} />
          </>
        )}
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
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
            }}
            placeholder={t('entityChat.placeholder')}
            disabled={busy}
            className="flex-1 resize-none text-xs border border-gray-300 rounded-lg px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-400 disabled:bg-gray-50"
          />
          {busy ? (
            <button
              onClick={onStop} disabled={stopping}
              title={t('entityChat.stop')}
              // The transparent border is load-bearing: it matches the
              // textarea's own border so both boxes end up the same height.
              className="p-2 rounded-lg border border-transparent bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
            >
              <StopCircle className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={onSend} disabled={!input.trim()}
              title={t('entityChat.send')}
              className="p-2 rounded-lg border border-transparent bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
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
