import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import PageLoader from './components/PageLoader';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import { AuthProvider } from './components/AuthContext';
import FirstRunGate from './components/firstRun/FirstRunGate';

// ---------------------------------------------------------------------------
// Pages are loaded on demand.
// ---------------------------------------------------------------------------
// Importing all forty-odd pages eagerly meant one bundle that had to parse the
// flow editor and the 3-D scene view before the first
// screen could paint, however little of it the visitor was about to use. Each
// `import()` below becomes its own chunk, named after the page; the modules a
// family of pages shares (the view renderers, the flow canvas) are factored
// out into a chunk of their own by Rollup, so opening the second page of a family costs nothing extra.
//
// Layout, the providers and the boundary stay eager: they are on screen for
// every route, so deferring them would only add a round trip to every visit.
const TaskManager = lazy(() => import('./pages/TaskManager'));
const AgentManager = lazy(() => import('./pages/AgentManager'));
const Connectors = lazy(() => import('./pages/Connectors'));
const AgentDetails = lazy(() => import('./pages/AgentDetails'));
const TaskDetails = lazy(() => import('./pages/TaskDetails'));
const WorkspaceManager = lazy(() => import('./pages/WorkspaceManager'));
const WorkspaceDetails = lazy(() => import('./pages/WorkspaceDetails'));
const MemoryManager = lazy(() => import('./pages/MemoryManager'));
const AgentFlows = lazy(() => import('./pages/AgentFlows'));
const FlowEditor = lazy(() => import('./pages/FlowEditor'));
const FlowRun = lazy(() => import('./pages/FlowRun'));
const ToolsExplorer = lazy(() => import('./pages/ToolsExplorer'));
const Orchestrator = lazy(() => import('./pages/Orchestrator'));
const Sessions = lazy(() => import('./pages/Sessions'));
const RunGroups = lazy(() => import('./pages/RunGroups'));
const SessionDetails = lazy(() => import('./pages/SessionDetails'));
const Messages = lazy(() => import('./pages/Messages'));
const Instances = lazy(() => import('./pages/Instances'));
const InstanceDetail = lazy(() => import('./pages/InstanceDetail'));
const MessageDetails = lazy(() => import('./pages/MessageDetails'));
const Chat = lazy(() => import('./pages/Chat'));
const Containers = lazy(() => import('./pages/Containers'));
const Health = lazy(() => import('./pages/Health'));
const Guardrails = lazy(() => import('./pages/Guardrails'));
const Deployments = lazy(() => import('./pages/Deployments'));
const Settings = lazy(() => import('./pages/Settings'));
const Models = lazy(() => import('./pages/Models'));
const Loops = lazy(() => import('./pages/Loops'));
const LoopDetail = lazy(() => import('./pages/LoopDetail'));
const Teams = lazy(() => import('./pages/Teams'));
const TeamDetails = lazy(() => import('./pages/TeamDetails'));
const ProjectManager = lazy(() => import('./pages/ProjectManager'));
const ProjectDetails = lazy(() => import('./pages/ProjectDetails'));
const Plan = lazy(() => import('./pages/Plan'));
const Marketplace = lazy(() => import('./pages/Marketplace'));
const SkillsCatalog = lazy(() => import('./pages/SkillsCatalog'));
const WebLogs = lazy(() => import('./pages/WebLogs'));
const MarketplaceAgent = lazy(() => import('./pages/MarketplaceAgent'));
const Artifacts = lazy(() => import('./pages/Artifacts'));
const ArtifactsRedirect = lazy(() => import('./pages/Artifacts').then((m) => ({ default: m.ArtifactsRedirect })));
const ViewDetail = lazy(() => import('./pages/ViewDetail'));
const Studio = lazy(() => import('./pages/Studio'));
const Docs = lazy(() => import('./pages/Docs'));

/** Centred spinner shown while a page's chunk is on the wire. */
function RouteFallback() {
  return (
    <div className="h-full min-h-[70vh] w-full flex flex-col items-center justify-center">
      <PageLoader size="lg" />
    </div>
  );
}

/**
 * One error boundary per route element.
 *
 * A caught error is state, so the boundary clears it when the pathname
 * changes: without that, a page that threw once would keep showing its
 * fallback after the user had already walked away to a route that works. Not
 * a `key`: that remounts the page on every navigation, including a page that
 * changes its own URL mid-work (Chat's first message moves /chat to
 * /chat/<id> while the reply is streaming).
 */
function RouteBoundary({ children }) {
  const { pathname } = useLocation();
  return <ErrorBoundary resetKey={pathname}>{children}</ErrorBoundary>;
}

/** `<Route element={guard(<Page />)} />` — every route gets the same wrapper. */
const guard = (element) => <RouteBoundary>{element}</RouteBoundary>;

function AppRoutes() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={guard(<Chat />)} />
        <Route path="/chat" element={guard(<Chat />)} />
        <Route path="/chat/:convId" element={guard(<Chat />)} />
        <Route path="/dashboard" element={<Navigate to="/chat" replace />} />
        <Route path="/orchestrator" element={guard(<Orchestrator />)} />
        <Route path="/tasks" element={guard(<TaskManager />)} />
        <Route path="/tasks/:id" element={guard(<TaskDetails />)} />
        <Route path="/plan" element={guard(<Plan />)} />
        <Route path="/deployments" element={guard(<Deployments />)} />
        {/* Files and Views live under Artifacts; the old addresses redirect with their query. */}
        <Route path="/files" element={<ArtifactsRedirect />} />
        <Route path="/agents" element={guard(<AgentManager />)} />
        <Route path="/agents/:id" element={guard(<AgentDetails />)} />
        <Route path="/marketplace" element={guard(<Marketplace />)} />
        <Route path="/marketplace/:id" element={guard(<MarketplaceAgent />)} />
        <Route path="/skills" element={guard(<SkillsCatalog />)} />
        <Route path="/web-logs" element={guard(<WebLogs />)} />
        <Route path="/tools" element={guard(<ToolsExplorer />)} />
        <Route path="/workspaces" element={guard(<WorkspaceManager />)} />
        <Route path="/workspaces/:name" element={guard(<WorkspaceDetails />)} />
        <Route path="/memory" element={guard(<MemoryManager />)} />
        <Route path="/flows" element={guard(<AgentFlows />)} />
        <Route path="/flows/:flowId" element={guard(<FlowRun />)} />
        {/* One conversation or run of the flow, its own address: the same
            page (kept mounted when the address changes), that record open. */}
        <Route path="/flows/:flowId/runs/:runGroup" element={guard(<FlowRun />)} />
        <Route path="/flows/:flowId/edit" element={guard(<FlowEditor />)} />
        <Route path="/loops" element={guard(<Loops />)} />
        <Route path="/loops/:loopId" element={guard(<LoopDetail />)} />
        {/* One run of the loop: the same page, that run open. */}
        <Route path="/loops/:loopId/runs/:runId" element={guard(<LoopDetail />)} />
        <Route path="/teams" element={guard(<Teams />)} />
        <Route path="/teams/:teamId" element={guard(<TeamDetails />)} />
        <Route path="/sessions" element={guard(<Sessions />)} />
        <Route path="/sessions/:sessionId" element={guard(<SessionDetails />)} />
        <Route path="/run-groups" element={guard(<RunGroups />)} />
        <Route path="/messages" element={guard(<Messages />)} />
        <Route path="/messages/:runId" element={guard(<MessageDetails />)} />
        <Route path="/connectors" element={guard(<Connectors />)} />
        <Route path="/instances" element={guard(<Instances />)} />
        <Route path="/instances/:instanceId" element={guard(<InstanceDetail />)} />
        <Route path="/nodes" element={<Navigate to="/instances" replace />} />
        <Route path="/guardrails" element={guard(<Guardrails />)} />
        <Route path="/containers" element={guard(<Containers />)} />
        <Route path="/health" element={guard(<Health />)} />
        <Route path="/projects" element={guard(<ProjectManager />)} />
        <Route path="/projects/:id" element={guard(<ProjectDetails />)} />
        <Route path="/artifacts" element={guard(<Artifacts />)} />
        <Route path="/artifacts/files" element={<ArtifactsRedirect />} />
        <Route path="/views" element={<ArtifactsRedirect folder="__views__" />} />
        <Route path="/views/:viewId" element={guard(<ViewDetail />)} />
        <Route path="/studio" element={guard(<Studio />)} />
        <Route path="/studio/:viewId" element={guard(<Studio />)} />
        <Route path="/models" element={guard(<Models />)} />
        <Route path="/settings" element={guard(<Settings />)} />
        <Route path="/settings/:section" element={guard(<Settings />)} />
        <Route path="/docs" element={guard(<Docs />)} />
        <Route path="/docs/:section" element={guard(<Docs />)} />
        <Route path="/login" element={<Navigate to="/chat" replace />} />
      </Routes>
    </Suspense>
  );
}

function App() {
  // The router follows Vite's BASE_URL rather than assuming the root.
  return (
    <Router basename={import.meta.env.BASE_URL}>
      <AuthProvider>
        {/* A new install sets itself up first
            (docs/installation.md, "The first run in the browser"), in place of the whole app. */}
        <FirstRunGate>
          <Layout>
            <AppRoutes />
          </Layout>
        </FirstRunGate>
      </AuthProvider>
    </Router>
  );
}

export default App;
