/**
 * The Chat page's own context.
 *
 * The page is one conversation shown through five panes: the session list, the
 * target bar, the transcript, the composer and the run panel. They share
 * nearly all of the page's state, so the page publishes it once here instead
 * of threading forty props through five components. Nothing else reads this:
 * it is scoped to one page, not to the app.
 */
import { createContext, useContext } from 'react';

export const ChatPageContext = createContext(null);

export function useChatPage() {
  const ctx = useContext(ChatPageContext);
  if (!ctx) throw new Error('useChatPage must be used inside the Chat page');
  return ctx;
}
