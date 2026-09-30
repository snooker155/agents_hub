/**
 * What a rendered reply needs from the message around it (ChatMarkdown.jsx).
 */
import { createContext } from 'react';

/**
 * `{ open(code, language) => Promise }` for the code blocks of one message,
 * or null where there is nowhere to open them.
 */
export const ChatCodeActionsContext = createContext(null);

/** `(n) => void`: focus source `n` of the reply's citations. */
export const CitationFocusContext = createContext(null);

/**
 * `{ save(tex, title) => Promise }`: keep a formula from a reply in the
 * user's personal memory, or null where there is no chat to save from.
 */
export const ChatMathActionsContext = createContext(null);
