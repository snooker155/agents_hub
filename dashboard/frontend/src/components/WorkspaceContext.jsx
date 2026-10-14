import { useState, useEffect } from 'react';
import { WORKSPACE_EVENT } from './theme';
import { WorkspaceContext } from './workspace';
import { setActiveWorkspace as apiSetActiveWorkspace } from '../api';
import { checkStoredWorkspace } from '../api/workspaceSummary';

function pushWorkspaceToBackend(workspace) {
  // best-effort — don't block or throw
  apiSetActiveWorkspace(workspace || null).catch(() => {});
}

export const WorkspaceProvider = ({ children }) => {
  const [selectedWorkspace, setSelectedWorkspace] = useState(
    localStorage.getItem('selectedWorkspace') || ''
  );
  // What this browser remembered when the page loaded.
  const [initialStored] = useState(selectedWorkspace);
  const [liveUpdates, setLiveUpdates] = useState(
    () => localStorage.getItem('dashboard_live') !== 'false'
  );
  // The remembered name is checked against this backend's workspaces before
  // it is pushed anywhere: one it does not have (another hub served on this
  // address before) is dropped, and Layout then picks the first workspace.
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    let live = true;
    checkStoredWorkspace().then((kept) => {
      if (!live) return;
      if (!kept) setSelectedWorkspace((prev) => (prev && prev === initialStored ? '' : prev));
      setChecked(true);
    });
    return () => { live = false; };
  }, [initialStored]);

  useEffect(() => {
    if (!checked) return;
    if (selectedWorkspace) {
      localStorage.setItem('selectedWorkspace', selectedWorkspace);
    } else {
      localStorage.removeItem('selectedWorkspace');
    }
    // Keep backend in sync so agent tools can read the active workspace
    pushWorkspaceToBackend(selectedWorkspace || null);
    // ThemeProvider sits above this provider and re-resolves the palette
    // (a workspace may carry a default one) when it hears this.
    try {
      window.dispatchEvent(new CustomEvent(WORKSPACE_EVENT, { detail: selectedWorkspace || '' }));
    } catch {
      // A test DOM without CustomEvent: nothing listens there anyway.
    }
  }, [selectedWorkspace, checked]);

  const toggleLiveUpdates = () => {
    setLiveUpdates(prev => {
      const next = !prev;
      localStorage.setItem('dashboard_live', String(next));
      return next;
    });
  };

  // When "default" workspace is selected, show all data (no filtering)
  const workspaceFilter = selectedWorkspace === 'default' ? undefined : selectedWorkspace || undefined;

  return (
    <WorkspaceContext.Provider value={{ selectedWorkspace, setSelectedWorkspace, workspaceFilter, liveUpdates, toggleLiveUpdates }}>
      {children}
    </WorkspaceContext.Provider>
  );
};
