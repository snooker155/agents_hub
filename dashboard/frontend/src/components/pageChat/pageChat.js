/**
 * The page chat's context and hooks, split out so `PageChatContext.jsx` exports
 * only the provider component (see `workspace.js` for the same split).
 */
import { createContext, useContext, useEffect } from 'react';

/**
 * The page chat's shared state: is the panel open, and what is it about.
 *
 * Two kinds of registration land here, and the difference is the whole design:
 *
 * * **A page's own chat** (`usePageChat`) — ten pages already have one, with
 *   their own builder agent, their own endpoints and their own thread. Those
 *   chats do not change when the panel appears; only where they are drawn does.
 *   A page registers its chat here and the panel shows *that*, so the user gets
 *   one chat per page rather than two about the same thing.
 * * **What a page is showing** (`usePageChatSubject`) — the records and the
 *   state of a page that has no chat of its own. The panel feeds them to the
 *   one page-chat agent, which is the same agent everywhere.
 *
 * Both registrations are tolerant of unmemoised arguments: only a small
 * signature of primitives drives re-renders, while the callbacks themselves are
 * read from a ref at the moment they are used. A page therefore cannot put the
 * panel into a render loop by building its descriptor inline.
 */

export const PageChatContext = createContext(null);

const signature = (descriptor) => {
  if (!descriptor) return '';
  return [
    descriptor.scope || '',
    descriptor.path || '',
    descriptor.title || '',
    descriptor.emptyHint || '',
    (descriptor.suggestions || []).join('\u00a7'),
  ].join('\u00a6');
};

const subjectSignature = (subject) => {
  if (!subject) return '';
  const refs = (subject.refs || []).map((r) => `${r.kind}:${r.id}`).join(',');
  return [subject.title || '', refs, subject.hints || ''].join('\u00a6');
};

/**
 * Panel state for whoever needs to know about it: the launcher, the panel, and
 * the inline chat columns that fold away while it is open.
 *
 * Safe outside the provider — a page rendered on its own in a test gets a
 * closed panel and nothing else.
 */
export function usePageChatPanel() {
  const ctx = useContext(PageChatContext);
  return ctx || {
    open: false, setOpen: () => {}, toggle: () => {}, wide: false, setWide: () => {},
    inlineSuppressed: false, inlineChatOpen: false,
  };
}

/**
 * Declare that a chat of the page's own is on screen right now, composer and
 * all, for as long as *visible* stays true.
 *
 * The launcher is a floating button in the bottom-right corner, which is
 * exactly where a chat column puts its send button: on those pages the two
 * overlapped. So while a page is showing a chat, the button stands down — the
 * user is already typing to an agent, and on every page that registers its
 * chat it is the same conversation the panel would open anyway. Closing the
 * inline chat brings the button back.
 *
 * Counted rather than flagged: a page may have two chats mounted (a tab and a
 * column), and the button must stay away until the last of them is gone.
 */
export function useInlineChatOpen(visible = true) {
  const ctx = useContext(PageChatContext);
  const count = ctx?.countInlineChat;

  useEffect(() => {
    if (!count || !visible) return undefined;
    count(1);
    return () => count(-1);
  }, [count, visible]);
}

/**
 * Declare the chat this page already has, so the panel shows it instead of the
 * general page assistant.
 *
 * @param {object|null} descriptor
 * @param {string} descriptor.scope       conversation key, for the panel's own bookkeeping.
 * @param {string} descriptor.path        streaming turn path (e.g. `loopChatUrl(id)`).
 * @param {function} descriptor.loadChat  () => Promise<{data:{messages, trace}}>
 * @param {function} descriptor.clearChat () => Promise<any>
 * @param {function} descriptor.stopChat  () => Promise<any>
 * @param {function} [descriptor.onEvent] every stream event, so the page can
 *   apply what the agent changed without a refetch — the same callback the
 *   inline chat gets.
 * @param {function} [descriptor.registerSend] handed the chat's send(text), so a
 *   page that posts turns from its own buttons keeps doing so while the panel
 *   hosts the conversation. Must be stable across renders.
 * @param {string} descriptor.title       panel heading.
 * @param {string} [descriptor.emptyHint]
 * @param {string[]} [descriptor.suggestions]
 *
 * Pass `null` when the page has no chat right now (nothing selected yet), and
 * the panel falls back to the page assistant.
 */
export function usePageChat(descriptor) {
  const ctx = useContext(PageChatContext);
  const usable = descriptor && descriptor.path ? descriptor : null;
  const sig = usable ? signature(usable) : '';
  const publish = ctx?.publishEntity;
  const setSig = ctx?.setEntitySig;

  // After every commit, not only when the signature changes: a page may rebuild
  // its callbacks without changing anything the signature can see, and the
  // panel must call this render's, not the ones from three renders ago.
  useEffect(() => { if (publish) publish(usable); });

  useEffect(() => {
    if (!setSig) return undefined;
    setSig(sig);
    return () => {
      // Only clear what we set: on a navigation the next page registers before
      // this cleanup runs, and dropping its registration would leave the panel
      // showing the general assistant on a page that has its own chat.
      setSig((current) => (current === sig ? '' : current));
    };
  }, [sig, setSig]);
}

/**
 * Declare what this page is showing, for pages without a chat of their own.
 *
 * @param {object} subject
 * @param {string} [subject.title]  the page as the user would name it.
 * @param {Array<{kind: string, id: string, label?: string}>} [subject.refs]
 *   hub records the chat should be given. Pointers only: the server renders
 *   them, so nothing on the page can become prompt text by accident.
 * @param {string} [subject.hints]  the page's state in plain words — the active
 *   filter, what is selected, how many rows there are.
 */
export function usePageChatSubject(subject) {
  const ctx = useContext(PageChatContext);
  const sig = subject ? subjectSignature(subject) : '';
  const publish = ctx?.publishSubject;
  const setSig = ctx?.setSubjectSig;

  useEffect(() => { if (publish) publish(subject || null); });

  useEffect(() => {
    if (!setSig) return undefined;
    setSig(sig);
    return () => setSig((current) => (current === sig ? '' : current));
  }, [sig, setSig]);
}
