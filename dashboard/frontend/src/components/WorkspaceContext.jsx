import { useState, useEffect } from 'react';
import { WorkspaceContext } from './workspace';
import { setActiveWorkspace as apiSetActiveWorkspace } from '../api';

function pushWorkspaceToBackend(workspace) {
  // best-effort — don't block or throw
  apiSetActiveWorkspace(workspace || null).catch(() => {});
}

export const WorkspaceProvider = ({ children }) => {
  const [selectedWorkspace, setSelectedWorkspace] = useState(
    localStorage.getItem('selectedWorkspace') || ''
  );
  const [liveUpdates, setLiveUpdates] = useState(
    () => localStorage.getItem('dashboard_live') !== 'false'
  );

  useEffect(() => {
    if (selectedWorkspace) {
      localStorage.setItem('selectedWorkspace', selectedWorkspace);
    } else {
      localStorage.removeItem('selectedWorkspace');
    }
    // Keep backend in sync so agent tools can read the active workspace
    pushWorkspaceToBackend(selectedWorkspace || null);
  }, [selectedWorkspace]);

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
