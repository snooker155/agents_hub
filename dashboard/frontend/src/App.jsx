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
import Dashboard from './pages/Dashboard';
import AgentManifest from './pages/AgentManifest';
import ToolsExplorer from './pages/ToolsExplorer';
import Orchestrator from './pages/Orchestrator';
import Sessions from './pages/Sessions';
import SessionDetails from './pages/SessionDetails';
import Chat from './pages/Chat';
import Nodes from './pages/Nodes';
import Settings from './pages/Settings';

function App() {
  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/orchestrator" element={<Orchestrator />} />
          <Route path="/tasks" element={<TaskManager />} />
          <Route path="/tasks/:id" element={<TaskDetails />} />
          <Route path="/agents" element={<AgentManager />} />
          <Route path="/agents/:id" element={<AgentDetails />} />
          <Route path="/manifest" element={<AgentManifest />} />
          <Route path="/tools" element={<ToolsExplorer />} />
          <Route path="/workspaces" element={<WorkspaceManager />} />
          <Route path="/workspaces/:name" element={<WorkspaceDetails />} />
          <Route path="/memory" element={<MemoryManager />} />
          <Route path="/factory" element={<AgentFactory />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:runId" element={<SessionDetails />} />
          <Route path="/nodes" element={<Nodes />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
