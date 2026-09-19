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
import Instances from './pages/Instances';
import InstanceDetail from './pages/InstanceDetail';
import MessageDetails from './pages/MessageDetails';
import Chat from './pages/Chat';
import Nodes from './pages/Nodes';
import NodeDetail from './pages/NodeDetail';
import Containers from './pages/Containers';
import Health from './pages/Health';
import Settings from './pages/Settings';
import Models from './pages/Models';
import Costs from './pages/Costs';
import Evals from './pages/Evals';
import Playground from './pages/Playground';
import PlaygroundScenario from './pages/PlaygroundScenario';
import PlaygroundRuns from './pages/PlaygroundRuns';
import PlaygroundWorlds from './pages/PlaygroundWorlds';
import PlaygroundWorld from './pages/PlaygroundWorld';
import Loops from './pages/Loops';
import Teams from './pages/Teams';
import TeamDetails from './pages/TeamDetails';
import ProjectManager from './pages/ProjectManager';
import ProjectDetails from './pages/ProjectDetails';
import Registry from './pages/Registry';
import Plan from './pages/Plan';
import Marketplace from './pages/Marketplace';
import SkillsCatalog from './pages/SkillsCatalog';
import WebLogs from './pages/WebLogs';
import MarketplaceAgent from './pages/MarketplaceAgent';
import Views from './pages/Views';
import ViewDetail from './pages/ViewDetail';
import Studio from './pages/Studio';
import Docs from './pages/Docs';
import { PageChatProvider } from './components/pageChat/PageChatContext';

function App() {
  return (
    <Router>
      {/* Inside the router: the page chat's subject is the route, so it can
          only be resolved under one. */}
      <PageChatProvider>
        <Layout>
          <Routes>
            <Route path="/" element={<Chat />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:convId" element={<Chat />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/orchestrator" element={<Orchestrator />} />
            <Route path="/tasks" element={<TaskManager />} />
            <Route path="/tasks/:id" element={<TaskDetails />} />
            <Route path="/plan" element={<Plan />} />
            <Route path="/agents" element={<AgentManager />} />
            <Route path="/agents/:id" element={<AgentDetails />} />
            <Route path="/marketplace" element={<Marketplace />} />
            <Route path="/marketplace/:id" element={<MarketplaceAgent />} />
            <Route path="/skills" element={<SkillsCatalog />} />
            <Route path="/web-logs" element={<WebLogs />} />
            <Route path="/manifest" element={<AgentManifest />} />
            <Route path="/tools" element={<ToolsExplorer />} />
            <Route path="/workspaces" element={<WorkspaceManager />} />
            <Route path="/workspaces/:name" element={<WorkspaceDetails />} />
            <Route path="/memory" element={<MemoryManager />} />
            <Route path="/flows" element={<AgentFlows />} />
            <Route path="/flows/:flowId" element={<FlowEditor />} />
            <Route path="/loops" element={<Loops />} />
            <Route path="/teams" element={<Teams />} />
            <Route path="/teams/:teamId" element={<TeamDetails />} />
            <Route path="/registry" element={<Registry />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:sessionId" element={<SessionDetails />} />
            <Route path="/messages" element={<Messages />} />
            <Route path="/messages/:runId" element={<MessageDetails />} />
            <Route path="/instances" element={<Instances />} />
            <Route path="/instances/:instanceId" element={<InstanceDetail />} />
            <Route path="/nodes" element={<Nodes />} />
            <Route path="/nodes/:nodeId" element={<NodeDetail />} />
            <Route path="/containers" element={<Containers />} />
            <Route path="/health" element={<Health />} />
            <Route path="/projects" element={<ProjectManager />} />
            <Route path="/projects/:id" element={<ProjectDetails />} />
            <Route path="/views" element={<Views />} />
            <Route path="/views/:viewId" element={<ViewDetail />} />
            <Route path="/studio" element={<Studio />} />
            <Route path="/studio/:viewId" element={<Studio />} />
            <Route path="/models" element={<Models />} />
            <Route path="/costs" element={<Costs />} />
            <Route path="/evals" element={<Evals />} />
            <Route path="/playground" element={<Playground />} />
            {/* Static before dynamic: "runs" and "worlds" are pages, not
                scenario ids. */}
            <Route path="/playground/runs" element={<PlaygroundRuns />} />
            <Route path="/playground/worlds" element={<PlaygroundWorlds />} />
            <Route path="/playground/worlds/:worldId" element={<PlaygroundWorld />} />
            <Route path="/playground/:scenarioId" element={<PlaygroundScenario />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/settings/:section" element={<Settings />} />
            <Route path="/docs" element={<Docs />} />
            <Route path="/docs/:section" element={<Docs />} />
          </Routes>
        </Layout>
      </PageChatProvider>
    </Router>
  );
}

export default App;
