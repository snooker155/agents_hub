/**
 * Workspace context and its hook, split out so `WorkspaceContext.jsx` exports
 * only the provider component (see `stream.js` for the same split).
 */
import { createContext, useContext } from 'react';

export const WorkspaceContext = createContext();

export const useWorkspace = () => useContext(WorkspaceContext);
