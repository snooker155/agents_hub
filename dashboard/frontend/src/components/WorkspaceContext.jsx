import React, { createContext, useState, useContext, useEffect } from 'react';

const WorkspaceContext = createContext();

export const WorkspaceProvider = ({ children }) => {
  const [selectedWorkspace, setSelectedWorkspace] = useState(
    localStorage.getItem('selectedWorkspace') || ''
  );

  useEffect(() => {
    if (selectedWorkspace) {
      localStorage.setItem('selectedWorkspace', selectedWorkspace);
    } else {
      localStorage.removeItem('selectedWorkspace');
    }
  }, [selectedWorkspace]);

  return (
    <WorkspaceContext.Provider value={{ selectedWorkspace, setSelectedWorkspace }}>
      {children}
    </WorkspaceContext.Provider>
  );
};

export const useWorkspace = () => useContext(WorkspaceContext);
