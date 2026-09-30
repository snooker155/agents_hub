/**
 * The Agent Details page's own context.
 *
 * The page has a dozen tabs over one agent, and the tabs share most of what
 * the page loaded: the agent itself, its live resident instances, its tools, a
 * dozen save handlers. Handing that down as props would be a hundred-name
 * argument list repeated across every tab, so the page publishes its state
 * once here and each tab takes the few names it needs. Nothing else reads
 * this: it is scoped to one page, not to the app.
 */
import { createContext, useContext } from 'react';

export const AgentPageContext = createContext(null);

export function useAgentPage() {
  const ctx = useContext(AgentPageContext);
  if (!ctx) throw new Error('useAgentPage must be used inside the Agent Details page');
  return ctx;
}
