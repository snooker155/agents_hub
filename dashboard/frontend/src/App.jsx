import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import TaskManager from './pages/TaskManager';
import AgentManager from './pages/AgentManager';
import AgentDetails from './pages/AgentDetails';
import TaskDetails from './pages/TaskDetails';
import WorkspaceManager from './pages/WorkspaceManager';
import WorkspaceDetails from './pages/WorkspaceDetails';
import MemoryManager from './pages/MemoryManager';
import AgentFactory from './pages/AgentFactory';

function App() {
  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<TaskManager />} />
          <Route path="/tasks/:id" element={<TaskDetails />} />
          <Route path="/agents" element={<AgentManager />} />
          <Route path="/agents/:id" element={<AgentDetails />} />
          <Route path="/workspaces" element={<WorkspaceManager />} />
          <Route path="/workspaces/:name" element={<WorkspaceDetails />} />
          <Route path="/memory" element={<MemoryManager />} />
          <Route path="/factory" element={<AgentFactory />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
