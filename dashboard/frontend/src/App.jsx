import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Loader } from 'lucide-react';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import { PageChatProvider } from './components/pageChat/PageChatContext';
import { FeaturesProvider } from './components/FeaturesContext';
import { useFeatures } from './components/features';
import { AuthProvider } from './components/AuthContext';
import { isAdmin, needsLogin, useAuth } from './components/auth';
import { useI18n } from './i18n';

// ---------------------------------------------------------------------------
// Pages are loaded on demand.
// ---------------------------------------------------------------------------
// Importing all forty-odd pages eagerly meant one bundle that had to parse the
// flow editor, the 3-D scene view and the whole playground before the first
// screen could paint, however little of it the visitor was about to use. Each
// `import()` below becomes its own chunk, named after the page; the modules a
// family of pages shares (the `pages/playground/*` helpers, the view
// renderers, the flow canvas) are factored out into a chunk of their own by
// Rollup, so opening the second page of a family costs nothing extra.
//
// Layout, the providers and the boundary stay eager: they are on screen for
// every route, so deferring them would only add a round trip to every visit.
const TaskManager = lazy(() => import('./pages/TaskManager'));
const AgentManager = lazy(() => import('./pages/AgentManager'));
const ConnectionDetail = lazy(() => import('./pages/ConnectionDetail'));
const Connections = lazy(() => import('./pages/Connections'));
const Connectors = lazy(() => import('./pages/Connectors'));
const Mcp = lazy(() => import('./pages/Mcp'));
const AgentDetails = lazy(() => import('./pages/AgentDetails'));
const TaskDetails = lazy(() => import('./pages/TaskDetails'));
const WorkspaceManager = lazy(() => import('./pages/WorkspaceManager'));
const WorkspaceDetails = lazy(() => import('./pages/WorkspaceDetails'));
const MemoryManager = lazy(() => import('./pages/MemoryManager'));
const AgentFlows = lazy(() => import('./pages/AgentFlows'));
const FlowEditor = lazy(() => import('./pages/FlowEditor'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const AgentManifest = lazy(() => import('./pages/AgentManifest'));
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
const Nodes = lazy(() => import('./pages/Nodes'));
const NodeDetail = lazy(() => import('./pages/NodeDetail'));
const Containers = lazy(() => import('./pages/Containers'));
const Health = lazy(() => import('./pages/Health'));
const Deployment = lazy(() => import('./pages/Deployment'));
const Settings = lazy(() => import('./pages/Settings'));
const Models = lazy(() => import('./pages/Models'));
const Costs = lazy(() => import('./pages/Costs'));
const Evals = lazy(() => import('./pages/Evals'));
const Playground = lazy(() => import('./pages/Playground'));
const PlaygroundScenario = lazy(() => import('./pages/PlaygroundScenario'));
const PlaygroundRuns = lazy(() => import('./pages/PlaygroundRuns'));
const PlaygroundWorlds = lazy(() => import('./pages/PlaygroundWorlds'));
const PlaygroundWorld = lazy(() => import('./pages/PlaygroundWorld'));
const Loops = lazy(() => import('./pages/Loops'));
const Teams = lazy(() => import('./pages/Teams'));
const TeamDetails = lazy(() => import('./pages/TeamDetails'));
const ProjectManager = lazy(() => import('./pages/ProjectManager'));
const ProjectDetails = lazy(() => import('./pages/ProjectDetails'));
const Registry = lazy(() => import('./pages/Registry'));
const Plan = lazy(() => import('./pages/Plan'));
const Marketplace = lazy(() => import('./pages/Marketplace'));
const SkillsCatalog = lazy(() => import('./pages/SkillsCatalog'));
const WebLogs = lazy(() => import('./pages/WebLogs'));
const MarketplaceAgent = lazy(() => import('./pages/MarketplaceAgent'));
const Views = lazy(() => import('./pages/Views'));
const ViewDetail = lazy(() => import('./pages/ViewDetail'));
const Studio = lazy(() => import('./pages/Studio'));
const Docs = lazy(() => import('./pages/Docs'));
// Identity pages. Both are inert outside AUTH_MODE=multi: the login screen is
// never reached and the users route is not registered. See docs/identity.md.
const Login = lazy(() => import('./pages/Login'));
const Users = lazy(() => import('./pages/Users'));

/** Centred spinner shown while a page's chunk is on the wire. */
function RouteFallback() {
  const { t } = useI18n();
  return (
    <div className="h-full w-full flex items-center justify-center text-gray-400">
      <Loader className="w-6 h-6 animate-spin mr-2" />
      <span className="text-sm">{t('errorBoundary.loading')}</span>
    </div>
  );
}

/**
 * One error boundary per route element.
 *
 * Keyed on the pathname, so the boundary is a fresh instance on every
 * navigation: a caught error is state, and without the key a page that threw
 * once would keep showing its fallback after the user had already walked away
 * to a route that works.
 */
function RouteBoundary({ children }) {
  const { pathname } = useLocation();
  return <ErrorBoundary key={pathname}>{children}</ErrorBoundary>;
}

/** `<Route element={guard(<Page />)} />` — every route gets the same wrapper. */
const guard = (element) => <RouteBoundary>{element}</RouteBoundary>;

function AppRoutes() {
  const { playground } = useFeatures();
  const auth = useAuth();
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={guard(<Chat />)} />
        <Route path="/chat" element={guard(<Chat />)} />
        <Route path="/chat/:convId" element={guard(<Chat />)} />
        <Route path="/dashboard" element={guard(<Dashboard />)} />
        <Route path="/orchestrator" element={guard(<Orchestrator />)} />
        <Route path="/tasks" element={guard(<TaskManager />)} />
        <Route path="/tasks/:id" element={guard(<TaskDetails />)} />
        <Route path="/plan" element={guard(<Plan />)} />
        <Route path="/agents" element={guard(<AgentManager />)} />
        <Route path="/agents/:id" element={guard(<AgentDetails />)} />
        <Route path="/marketplace" element={guard(<Marketplace />)} />
        <Route path="/marketplace/:id" element={guard(<MarketplaceAgent />)} />
        <Route path="/skills" element={guard(<SkillsCatalog />)} />
        <Route path="/web-logs" element={guard(<WebLogs />)} />
        <Route path="/manifest" element={guard(<AgentManifest />)} />
        <Route path="/tools" element={guard(<ToolsExplorer />)} />
        <Route path="/workspaces" element={guard(<WorkspaceManager />)} />
        <Route path="/workspaces/:name" element={guard(<WorkspaceDetails />)} />
        <Route path="/memory" element={guard(<MemoryManager />)} />
        <Route path="/flows" element={guard(<AgentFlows />)} />
        <Route path="/flows/:flowId" element={guard(<FlowEditor />)} />
        <Route path="/loops" element={guard(<Loops />)} />
        <Route path="/teams" element={guard(<Teams />)} />
        <Route path="/teams/:teamId" element={guard(<TeamDetails />)} />
        <Route path="/registry" element={guard(<Registry />)} />
        <Route path="/sessions" element={guard(<Sessions />)} />
        <Route path="/sessions/:sessionId" element={guard(<SessionDetails />)} />
        <Route path="/run-groups" element={guard(<RunGroups />)} />
        <Route path="/messages" element={guard(<Messages />)} />
        <Route path="/messages/:runId" element={guard(<MessageDetails />)} />
        <Route path="/connections" element={guard(<Connections />)} />
        <Route path="/connections/:connectionId" element={guard(<ConnectionDetail />)} />
        <Route path="/connectors" element={guard(<Connectors />)} />
        <Route path="/mcp" element={guard(<Mcp />)} />
        <Route path="/instances" element={guard(<Instances />)} />
        <Route path="/instances/:instanceId" element={guard(<InstanceDetail />)} />
        <Route path="/nodes" element={guard(<Nodes />)} />
        <Route path="/nodes/:nodeId" element={guard(<NodeDetail />)} />
        <Route path="/containers" element={guard(<Containers />)} />
        <Route path="/health" element={guard(<Health />)} />
        <Route path="/deployment" element={guard(<Deployment />)} />
        <Route path="/projects" element={guard(<ProjectManager />)} />
        <Route path="/projects/:id" element={guard(<ProjectDetails />)} />
        <Route path="/views" element={guard(<Views />)} />
        <Route path="/views/:viewId" element={guard(<ViewDetail />)} />
        <Route path="/studio" element={guard(<Studio />)} />
        <Route path="/studio/:viewId" element={guard(<Studio />)} />
        <Route path="/models" element={guard(<Models />)} />
        <Route path="/costs" element={guard(<Costs />)} />
        <Route path="/evals" element={guard(<Evals />)} />
        {/* The playground is optional (GET /api/health → features.playground).
            Switched off, its routes are not registered at all and anything
            under /playground lands on the dashboard, so a stale bookmark gets
            a working page instead of a blank one. */}
        {playground ? (
          <>
            <Route path="/playground" element={guard(<Playground />)} />
            {/* Static before dynamic: "runs" and "worlds" are pages, not
                scenario ids. */}
            <Route path="/playground/runs" element={guard(<PlaygroundRuns />)} />
            <Route path="/playground/worlds" element={guard(<PlaygroundWorlds />)} />
            <Route path="/playground/worlds/:worldId" element={guard(<PlaygroundWorld />)} />
            <Route path="/playground/:scenarioId" element={guard(<PlaygroundScenario />)} />
          </>
        ) : (
          <Route path="/playground/*" element={<Navigate to="/dashboard" replace />} />
        )}
        <Route path="/settings" element={guard(<Settings />)} />
        <Route path="/settings/:section" element={guard(<Settings />)} />
        {/* Accounts exist only under AUTH_MODE=multi, and only an administrator
            manages them. Anyone else lands on the dashboard, so the page never
            has to render a refusal of its own. */}
        {isAdmin(auth) ? (
          <Route path="/users" element={guard(<Users />)} />
        ) : (
          <Route path="/users" element={<Navigate to="/dashboard" replace />} />
        )}
        <Route path="/docs" element={guard(<Docs />)} />
        <Route path="/docs/:section" element={guard(<Docs />)} />
        {/* The api client sends a browser whose session died to /login; once
            AuthGate has let it back in there is nothing to show at that path. */}
        <Route path="/login" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Suspense>
  );
}

/**
 * Nothing of the application renders until the viewer is allowed to see it.
 *
 * A pass-through in `single` and `token` mode, where there is one operator and
 * no login to do. In `multi` mode it shows the login screen (or, on a first
 * run, the form that creates the first administrator) in place of the whole
 * app, so there is no route to bookmark past it and no shell behind it.
 */
function AuthGate({ children }) {
  const auth = useAuth();
  if (auth.loading) return <RouteFallback />;
  if (needsLogin(auth)) {
    return (
      <Suspense fallback={<RouteFallback />}>
        <Login />
      </Suspense>
    );
  }
  return children;
}

function App() {
  return (
    <Router>
      {/* Inside the router: the page chat's subject is the route, so it can
          only be resolved under one. */}
      <AuthProvider>
        <FeaturesProvider>
          <PageChatProvider>
            <AuthGate>
              <Layout>
                <AppRoutes />
              </Layout>
            </AuthGate>
          </PageChatProvider>
        </FeaturesProvider>
      </AuthProvider>
    </Router>
  );
}

export default App;
