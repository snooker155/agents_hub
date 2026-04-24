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
import AgentFlows from './pages/AgentFlows';
import FlowEditor from './pages/FlowEditor';
import Dashboard from './pages/Dashboard';
import AgentManifest from './pages/AgentManifest';
import ToolsExplorer from './pages/ToolsExplorer';
import Orchestrator from './pages/Orchestrator';
import Sessions from './pages/Sessions';
import SessionDetails from './pages/SessionDetails';
import Messages from './pages/Messages';
import MessageDetails from './pages/MessageDetails';
import Chat from './pages/Chat';
import Nodes from './pages/Nodes';
import NodeDetail from './pages/NodeDetail';
import Containers from './pages/Containers';
import Settings from './pages/Settings';
import ProjectManager from './pages/ProjectManager';
import ProjectDetails from './pages/ProjectDetails';

function App() {
  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/chat/:convId" element={<Chat />} />
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
          <Route path="/flows" element={<AgentFlows />} />
          <Route path="/flows/:flowId" element={<FlowEditor />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:sessionId" element={<SessionDetails />} />
          <Route path="/messages" element={<Messages />} />
          <Route path="/messages/:runId" element={<MessageDetails />} />
          <Route path="/nodes" element={<Nodes />} />
          <Route path="/nodes/:nodeId" element={<NodeDetail />} />
          <Route path="/containers" element={<Containers />} />
          <Route path="/projects" element={<ProjectManager />} />
          <Route path="/projects/:id" element={<ProjectDetails />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
