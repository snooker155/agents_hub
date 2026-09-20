import { useCallback, useEffect, useRef, useState } from 'react';
import { deleteChat, getChat, importChats, listChats, saveChat } from '../api';
import { useChannel, useStream } from './stream';

/**
 * The Chat page's conversations, stored on the server.
 *
 * They used to live in `localStorage`, which made the main record this service
 * produces the one thing it did not keep: tied to a single browser profile,
 * gone with the site data, and — once the ~5 MB quota was reached — trimmed
 * oldest-first with nothing said. They are rows in the hub's database now
 * (`/api/chats`), beside the runs they spawned.
 *
 * The component's state shape is untouched: the same `conversations` array and
 * the same `setConversations` the whole page already mutates in a hundred
 * places. Only the persistence underneath it changed, which is why this is a
 * hook and not a rewrite of the page.
 *
 * Three things are worth knowing if this is ever changed:
 *
 * * **The list arrives without transcripts.** A chat's messages are fetched
 *   when that chat is opened. Loading every bubble of every conversation to
 *   draw a sidebar would be the old problem in a new place.
 * * **Writes are debounced per conversation.** A streaming turn rewrites its
 *   bubble on every token; saving each frame would be a PUT per token. Changes
 *   settle for {@link SYNC_DEBOUNCE_MS}, and a long stream is still flushed
 *   every {@link SYNC_MAX_WAIT_MS} so leaving mid-answer does not lose it.
 * * **An unhydrated conversation is never written back.** It holds an empty
 *   `messages` array that is absence of knowledge, not an empty transcript;
 *   saving it would erase the stored one.
 * * **Someone else's save is picked up.** A write announces itself on the
 *   conversation's channel, so the same chat open in another tab or on another
 *   device reloads instead of drifting. A reload while this tab has unsaved
 *   changes of its own would throw them away, so it waits.
 */

/** The browser store this replaces. Read once, then imported server-side. */
export const LEGACY_STORAGE_KEY = 'agent_hub_chats_v1';
/** Set after the legacy payload has been handed to the server. */
export const MIGRATED_FLAG_KEY = 'agent_hub_chats_migrated_v1';

/** How long a conversation must sit unchanged before it is written. */
export const SYNC_DEBOUNCE_MS = 1000;
/** …and the longest a continuously changing one may go unwritten. */
export const SYNC_MAX_WAIT_MS = 8000;
/** How often a reload deferred by a busy tab checks whether it can happen. */
const DEFERRED_RELOAD_POLL_MS = 500;
/** Consecutive failed writes before the hook stops retrying and reports it. */
const MAX_SYNC_ATTEMPTS = 3;
/** How long it then waits before giving the backend another chance. */
const RETRY_COOLDOWN_MS = 30000;

// Per-message fields that are large and only meaningful while the turn is live
// (the Build-view timeline, the running-tool indicator, the reasoning ticker).
// They accumulate every streamed token and every tool input/output, and they are
// reconstructable from the run's server-side log, so they are not stored.
export const TRANSIENT_MSG_FIELDS = ['timeline', 'running_tool', 'thinking_live'];

/** The conversation as it is stored: no transient bubble state, no local flags. */
export function stripForStorage(conv) {
  const { _hydrated, message_count: _count, preview: _preview, ...rest } = conv;
  return {
    ...rest,
    messages: (conv.messages || []).map((m) => {
      const copy = { ...m };
      for (const f of TRANSIENT_MSG_FIELDS) delete copy[f];
      return copy;
    }),
  };
}

const serialize = (conv) => JSON.stringify(stripForStorage(conv));

function readLegacy() {
  try {
    return JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || '[]') || [];
  } catch {
    return [];
  }
}

/**
 * Hand the browser's old history to the server, once per browser profile.
 *
 * The raw payload is left in `localStorage` on purpose: the import is additive
 * (ids already stored are skipped), so keeping the old copy costs nothing and
 * is the only safety net if this ever imports the wrong thing.
 */
async function migrateLegacyChats() {
  let alreadyDone = false;
  try { alreadyDone = localStorage.getItem(MIGRATED_FLAG_KEY) === '1'; } catch { return; }
  if (alreadyDone) return;
  const legacy = readLegacy();
  if (!legacy.length) {
    try { localStorage.setItem(MIGRATED_FLAG_KEY, '1'); } catch { /* storage unavailable */ }
    return;
  }
  // A failure here is left unmarked so the next load tries again — the history
  // is the user's, and dropping it because the backend blinked is not an option.
  await importChats(legacy);
  try { localStorage.setItem(MIGRATED_FLAG_KEY, '1'); } catch { /* storage unavailable */ }
}

/**
 * @param {string|null} currentConvId the conversation being read, which is the
 *   one whose transcript is fetched.
 * @returns {{conversations: Array, setConversations: Function, ready: boolean,
 *            removeConversation: Function, syncError: boolean, flush: Function}}
 */
export function useConversationStore(currentConvId, { paused = false } = {}) {
  const { clientId } = useStream();
  const [conversations, setConversations] = useState([]);
  const [ready, setReady] = useState(false);
  const [syncError, setSyncError] = useState(false);

  // Latest state, readable from the debounce timer without re-subscribing it.
  const convsRef = useRef(conversations);
  // id → the document last written, so an unchanged conversation is not resent.
  const savedRef = useRef(new Map());
  // id → the object last looked at. A streaming turn replaces its conversation
  // object on every token, so the change check is an identity comparison here
  // and a serialization only when a write is actually about to happen.
  const seenRef = useRef(new Map());
  const dirtyRef = useRef(new Set());
  const timerRef = useRef(null);
  const deadlineRef = useRef(0);
  const attemptsRef = useRef(0);
  // When the attempt budget is spent, when it may be tried again.
  const blockedUntilRef = useRef(0);
  const hydratingRef = useRef(new Set());
  const clientIdRef = useRef(clientId);
  // A save that landed while this tab was busy, replayed once it is free.
  const pendingReloadRef = useRef(null);
  const reloadTimerRef = useRef(null);
  const pausedRef = useRef(paused);

  useEffect(() => { convsRef.current = conversations; }, [conversations]);
  useEffect(() => { clientIdRef.current = clientId; }, [clientId]);
  useEffect(() => { pausedRef.current = paused; }, [paused]);

  // ---- initial load ----
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await migrateLegacyChats();
      } catch { /* retried on the next load */ }
      let items = null;
      try {
        const { data } = await listChats({ limit: 200 });
        items = data?.items || [];
      } catch {
        // Backend unreachable: fall back to whatever this browser still holds,
        // so an offline tab shows a history instead of an empty page. Nothing
        // is written back from that state — the records stay unhydrated.
        items = readLegacy();
      }
      if (cancelled) return;
      setConversations(items.map((c) => ({ ...c, messages: c.messages || [], _hydrated: false })));
      setReady(true);
    })();
    return () => { cancelled = true; };
  }, []);

  // ---- fetch the open conversation's transcript ----
  useEffect(() => {
    if (!ready || !currentConvId) return undefined;
    const conv = conversations.find((c) => c.id === currentConvId);
    if (!conv || conv._hydrated !== false) return undefined;
    // A Telegram thread's transcript is not this store's to serve: the page
    // rebuilds it from the runs recorded against the binding, workspace slice
    // and all. Leaving it unhydrated keeps that rebuild from being written back
    // over itself and keeps the two copies from racing on open.
    if (conv.origin === 'telegram') return undefined;
    const hydrating = hydratingRef.current;
    if (hydrating.has(currentConvId)) return undefined;
    hydrating.add(currentConvId);
    let cancelled = false;
    (async () => {
      let loaded = null;
      try {
        const { data } = await getChat(currentConvId);
        loaded = data || {};
      } catch (e) {
        // A chat the server has never heard of (a record this browser still
        // holds from before the store existed) is as hydrated as it will get.
        // Any other failure means the transcript is unknown, not empty — the
        // record stays unhydrated so the empty `messages` can never be written
        // back over the stored one.
        if (e?.response?.status !== 404) { hydrating.delete(currentConvId); return; }
        loaded = {};
      }
      if (cancelled) return;
      setConversations((prev) => prev.map((c) => {
        if (c.id !== currentConvId) return c;
        const merged = { ...c, ...loaded, messages: loaded.messages || c.messages || [], _hydrated: true };
        // Record what was read as already written and already seen, so opening
        // a chat neither sends it straight back nor counts as an edit that has
        // to settle before anything else can happen.
        savedRef.current.set(currentConvId, serialize(merged));
        seenRef.current.set(currentConvId, merged);
        return merged;
      }));
      hydrating.delete(currentConvId);
    })();
    return () => { cancelled = true; hydrating.delete(currentConvId); };
  }, [ready, currentConvId, conversations]);

  // ---- someone else saved this chat ----
  const reloadChat = useCallback(async (chatId) => {
    if (!chatId) return;
    let loaded = null;
    try {
      const { data } = await getChat(chatId);
      loaded = data;
    } catch {
      return;  // keep what this tab has rather than emptying it on a failed read
    }
    if (!loaded) return;
    setConversations((prev) => prev.map((c) => {
      if (c.id !== chatId) return c;
      const merged = { ...c, ...loaded, messages: loaded.messages || [], _hydrated: true };
      // What was just read is, by definition, what is stored: record it as both
      // seen and saved so taking it in is not mistaken for a local change and
      // written straight back.
      savedRef.current.set(chatId, serialize(merged));
      seenRef.current.set(chatId, merged);
      return merged;
    }));
  }, []);

  const busy = useCallback((chatId) => pausedRef.current || dirtyRef.current.has(chatId), []);

  // A reload while this tab has a turn running, or changes not yet written,
  // would throw them away — so it waits and is retried until the tab is free.
  const deferReload = useCallback((chatId) => {
    pendingReloadRef.current = chatId;
    if (reloadTimerRef.current) return;
    reloadTimerRef.current = setInterval(() => {
      const id = pendingReloadRef.current;
      if (id && busy(id)) return;
      clearInterval(reloadTimerRef.current);
      reloadTimerRef.current = null;
      pendingReloadRef.current = null;
      if (id) reloadChat(id);
    }, DEFERRED_RELOAD_POLL_MS);
  }, [busy, reloadChat]);

  useChannel(currentConvId ? `chat:${currentConvId}` : null, (event) => {
    if (event?.type !== 'chat_saved') return;
    if (event.origin_client && event.origin_client === clientIdRef.current) return;
    if (busy(currentConvId)) { deferReload(currentConvId); return; }
    reloadChat(currentConvId);
  });

  useEffect(() => () => {
    if (reloadTimerRef.current) clearInterval(reloadTimerRef.current);
  }, []);

  // ---- write the changed conversations ----
  // `flush` schedules its own retry, so the timer goes through a ref rather
  // than through the callback itself, which cannot reference itself.
  const flushRef = useRef(() => {});
  const schedule = useCallback((wait) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => { timerRef.current = null; flushRef.current(); }, wait);
  }, []);

  const flush = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    deadlineRef.current = 0;
    const ids = [...dirtyRef.current];
    dirtyRef.current.clear();
    for (const id of ids) {
      const conv = convsRef.current.find((c) => c.id === id);
      if (!conv || conv._hydrated === false) continue;  // deleted, or never loaded
      const body = serialize(conv);
      // Changed object, identical document: a re-render, or the transcript that
      // was just read back. Nothing to send.
      if (savedRef.current.get(id) === body) continue;
      savedRef.current.set(id, body);
      saveChat(JSON.parse(body), clientIdRef.current)
        .then(() => { attemptsRef.current = 0; setSyncError(false); })
        .catch(() => {
          // Forget the snapshot so the change is seen again, and retry until
          // the attempt budget runs out — a backend that is down should not
          // turn into a write every second for the rest of the session.
          savedRef.current.delete(id);
          attemptsRef.current += 1;
          if (attemptsRef.current >= MAX_SYNC_ATTEMPTS) {
            blockedUntilRef.current = Date.now() + RETRY_COOLDOWN_MS;
            setSyncError(true);
            return;
          }
          dirtyRef.current.add(id);
          if (!timerRef.current) schedule(SYNC_DEBOUNCE_MS);
        });
    }
  }, [schedule]);

  useEffect(() => { flushRef.current = flush; }, [flush]);

  useEffect(() => {
    if (!ready) return;
    let dirty = false;
    for (const conv of conversations) {
      if (!conv?.id || conv._hydrated === false) continue;
      if (seenRef.current.get(conv.id) === conv) continue;
      seenRef.current.set(conv.id, conv);
      dirtyRef.current.add(conv.id);
      dirty = true;
    }
    if (!dirty) return;
    if (attemptsRef.current >= MAX_SYNC_ATTEMPTS) {
      // The budget is spent. A backend that comes back should be picked up, so
      // the next change after the cooldown starts a fresh round rather than the
      // tab staying read-only until it is reloaded.
      if (Date.now() < blockedUntilRef.current) return;
      attemptsRef.current = 0;
    }
    // A conversation being streamed into changes on every token, so the debounce
    // would keep sliding; the deadline is what guarantees it is written anyway.
    if (!deadlineRef.current) deadlineRef.current = Date.now() + SYNC_MAX_WAIT_MS;
    schedule(Math.max(0, Math.min(SYNC_DEBOUNCE_MS, deadlineRef.current - Date.now())));
  }, [conversations, ready, schedule]);

  // Leaving the page is the one moment a pending write must not wait out its
  // debounce. `visibilitychange` fires where `beforeunload` is unreliable
  // (mobile, tab discard), and unmount covers navigating within the app.
  useEffect(() => {
    const onHide = () => { if (document.visibilityState === 'hidden') flushRef.current(); };
    const onPageHide = () => flushRef.current();
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', onPageHide);
      flushRef.current();
    };
  }, []);

  // ---- delete ----
  const removeConversation = useCallback((id) => {
    dirtyRef.current.delete(id);
    savedRef.current.delete(id);
    seenRef.current.delete(id);
    setConversations((prev) => prev.filter((c) => c.id !== id));
    // The row is the record; a failed delete leaves it, and the next load shows
    // it again rather than pretending it is gone.
    return deleteChat(id).catch(() => {});
  }, []);

  return {
    conversations, setConversations, ready, removeConversation, syncError, flush,
    reloadChat,
  };
}

export default useConversationStore;
