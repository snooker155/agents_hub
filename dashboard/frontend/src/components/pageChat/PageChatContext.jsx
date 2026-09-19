import React, { useCallback, useMemo, useRef, useState } from 'react';
import { PageChatContext } from './pageChat';

const OPEN_KEY = 'agents_hub_page_chat_open';
const WIDE_KEY = 'agents_hub_page_chat_wide';

const readFlag = (key) => {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
};

const writeFlag = (key, value) => {
  try {
    localStorage.setItem(key, value ? '1' : '0');
  } catch { /* storage unavailable — the panel just forgets between reloads */ }
};

/**
 * Holds the panel's state and the two registrations pages make against it (see
 * `pageChat.js`, where the context and the hooks live).
 */
export function PageChatProvider({ children }) {
  const [open, setOpenState] = useState(() => readFlag(OPEN_KEY));
  const [wide, setWideState] = useState(() => readFlag(WIDE_KEY));
  // Only the signatures live in state; the descriptors themselves live in refs,
  // so a page rebuilding its callbacks every render costs nothing.
  const [entitySig, setEntitySig] = useState('');
  const [subjectSig, setSubjectSig] = useState('');
  const entityRef = useRef(null);
  const subjectRef = useRef(null);
  // How many chats the page is drawing itself (see `useInlineChatOpen`).
  const [inlineChats, setInlineChats] = useState(0);

  const setOpen = useCallback((value) => {
    setOpenState((prev) => {
      const next = typeof value === 'function' ? value(prev) : value;
      writeFlag(OPEN_KEY, next);
      return next;
    });
  }, []);

  const toggle = useCallback(() => setOpen((o) => !o), [setOpen]);

  const setWide = useCallback((value) => {
    setWideState((prev) => {
      const next = typeof value === 'function' ? value(prev) : value;
      writeFlag(WIDE_KEY, next);
      return next;
    });
  }, []);

  // Written through a function rather than by reaching into the ref from a
  // page: a registration is something the page tells the provider, not a slot
  // it pokes at, and only the provider should decide what that means.
  const publishEntity = useCallback((descriptor) => {
    entityRef.current = descriptor || null;
  }, []);

  const publishSubject = useCallback((subject) => {
    subjectRef.current = subject || null;
  }, []);

  const countInlineChat = useCallback((delta) => {
    setInlineChats((n) => Math.max(0, n + delta));
  }, []);

  const value = useMemo(() => ({
    open,
    setOpen,
    toggle,
    wide,
    setWide,
    entityRef,
    subjectRef,
    entitySig,
    subjectSig,
    setEntitySig,
    setSubjectSig,
    publishEntity,
    publishSubject,
    countInlineChat,
    // The page's own chat is in the panel: whatever draws it inline should step
    // aside rather than run a second copy of the same conversation.
    inlineSuppressed: open && !!entitySig,
    // The page is showing a chat of its own, so the launcher would land on its
    // composer. It stands down until that chat is closed.
    inlineChatOpen: inlineChats > 0,
  }), [open, setOpen, toggle, wide, setWide, entitySig, subjectSig,
       publishEntity, publishSubject, countInlineChat, inlineChats]);

  return <PageChatContext.Provider value={value}>{children}</PageChatContext.Provider>;
}

