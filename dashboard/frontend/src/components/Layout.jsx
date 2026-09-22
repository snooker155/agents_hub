import { useState, useEffect, useCallback, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useWorkspace } from './workspace';
import { useTheme } from './theme';
import { useStream, useLiveRefetch } from './stream';
import { useFeatures } from './features';
import { getWorkspaces, getWorkspaceModel, updateWorkspaceModel, testProvider, getModelsCatalog } from '../api';
import {
  LayoutDashboard,
  CheckSquare,
  Folder,
  Database,
  Factory,
  Wrench,
  Users,
  Activity,
  PlayCircle,
  MessageCircle,
  MessageSquare,
  ScrollText,
  Server,
  Settings,
  Sun,
  Moon,
  Monitor,
  Network,
  Radio,
  Pause,
  Cpu,
  ChevronDown,
  FolderGit2,
  Box,
  Boxes,
  Shapes,
  WifiOff,
  PanelLeftClose,
  PanelLeftOpen,
  Store,
  CalendarClock,
  BookOpen,
  Brain,
  DollarSign,
  Images,
  FlaskConical,
  Gamepad2,
  Repeat,
  UsersRound,
  GraduationCap,
  Globe,
  Share2,
  Link2,
  Plug,
} from 'lucide-react';
import NotificationBell from './NotificationBell';
import LanguageSwitcher from './LanguageSwitcher';
import { useI18n } from '../i18n';
import OnboardingModal from './docs/OnboardingModal';
import { routeTitleKey } from './routeTitles';
import PageChatPanel from './pageChat/PageChatPanel';

const SIDEBAR_COLLAPSED_KEY = 'agents_hub_sidebar_collapsed';

const PROVIDER_CONFIG = {
  openai:    { label: 'OpenAI',    color: 'text-green-700 bg-green-50 border-green-200' },
  anthropic: { label: 'Anthropic', color: 'text-orange-700 bg-orange-50 border-orange-200' },
  google:    { label: 'Google',    color: 'text-blue-700 bg-blue-50 border-blue-200' },
  ollama:    { label: 'Ollama',    color: 'text-purple-700 bg-purple-50 border-purple-200' },
  lmstudio:  { label: 'LM Studio', color: 'text-teal-700 bg-teal-50 border-teal-200' },
};

const THEME_OPTIONS = [
  { value: 'light',  icon: Sun,     labelKey: 'layout.theme.light' },
  { value: 'dark',   icon: Moon,    labelKey: 'layout.theme.dark' },
  { value: 'system', icon: Monitor, labelKey: 'layout.theme.system' },
];

const BUILTIN_PROVIDER_ORDER = ['openai', 'anthropic', 'google', 'ollama', 'lmstudio'];

const Layout = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { selectedWorkspace, setSelectedWorkspace, liveUpdates, toggleLiveUpdates } = useWorkspace();
  const { theme, setTheme } = useTheme();
  // Optional features the backend reports at /api/health; a feature that is
  // switched off has no sidebar row and no route (see App.jsx).
  const { playground: playgroundEnabled } = useFeatures();
  const { t } = useI18n();
  const [workspaces, setWorkspaces] = useState([]);
  // workspaceModel: full model state returned by GET /api/workspaces/{name}/model
  // - global_default: DEFAULT_PROVIDER + its model from .env (lowest-priority fallback)
  // - workspace_default: resolved from workspace settings in .workspace.json
  // - override: explicitly set via UI picker
  const [workspaceModel, setWorkspaceModel] = useState({
    global_default: { provider: 'openai', model: '' },
    workspace_default: { provider: '', model: '' },
    override: { provider: '', model: '' },
  });
  // availableModels: all providers with a model configured in Settings, used for override rows
  const [availableModels, setAvailableModels] = useState([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [providerStatuses, setProviderStatuses] = useState({});
  const [providersTesting, setProvidersTesting] = useState({});
  const [backendOnline, setBackendOnline] = useState(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
    } catch {
      return false;
    }
  });
  const modelPickerRef = useRef(null);

  const toggleSidebar = () => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? '1' : '0');
      } catch { /* storage unavailable */ }
      return next;
    });
  };

  useEffect(() => {
    const fetchWorkspaces = async () => {
      try {
        const resp = await getWorkspaces();
        setWorkspaces(resp.data);
        if (resp.data.length === 0) return;
        const names = resp.data.map(w => w.name);
        const stale = selectedWorkspace && !names.includes(selectedWorkspace);
        if (!selectedWorkspace || stale) {
          setSelectedWorkspace(resp.data[0].name);
        }
      } catch (error) {
        console.error('Error fetching workspaces:', error);
      }
    };
    fetchWorkspaces();
  }, [selectedWorkspace, setSelectedWorkspace]);

  // Keep the picker in sync when workspaces are created/deleted elsewhere.
  useLiveRefetch(() => {
    getWorkspaces()
      .then((resp) => setWorkspaces(resp.data))
      .catch((error) => console.error('Error refreshing workspaces:', error));
  }, { type: 'workspaces.changed' });

  // Fetch every enabled catalog model (multiple per provider) — these are the
  // selectable rows in the picker. Re-fetched when the picker opens so newly
  // enabled/disabled models in the Models page show up without a full reload.
  // Built-in providers first, then any custom backends present in the catalog,
  // so user-defined backends' enabled models also appear in the picker.
  const loadAvailableModels = useCallback(() => {
    return getModelsCatalog().then(({ data }) => {
      const providers = data.providers || {};
      const order = [
        ...BUILTIN_PROVIDER_ORDER.filter((p) => p in providers),
        ...Object.keys(providers).filter((p) => !BUILTIN_PROVIDER_ORDER.includes(p)).sort(),
      ];
      const avail = [];
      for (const p of order) {
        for (const m of (providers[p]?.models || [])) {
          if (m.enabled) avail.push({ provider: p, model: m.id });
        }
      }
      setAvailableModels(avail);
      return avail;
    }).catch(e => { console.error('Error fetching catalog:', e); return []; });
  }, []);
  useEffect(() => { loadAvailableModels(); }, [loadAvailableModels]);

  // Re-fetch workspace model whenever workspace changes
  useEffect(() => {
    if (!selectedWorkspace) return;
    getWorkspaceModel(selectedWorkspace)
      .then(({ data }) => setWorkspaceModel(data))
      .catch(() => setWorkspaceModel(prev => ({ ...prev, override: { provider: '', model: '' }, workspace_default: { provider: '', model: '' } })));
  }, [selectedWorkspace]);

  // Backend health is implied by the shared stream connection: if our single
  // EventSource is open, the backend is up. No separate polling needed.
  const { connected } = useStream();
  useEffect(() => { setBackendOnline(connected); }, [connected]);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false);
      }
    };
    if (showModelPicker) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showModelPicker]);

  const SERVICE_NAME = t('layout.serviceName');

  useEffect(() => {
    const key = routeTitleKey(location.pathname);
    const pageTitle = key ? t(key) : '';
    document.title = pageTitle ? `${SERVICE_NAME} - ${pageTitle}` : SERVICE_NAME;
  }, [location.pathname, t, SERVICE_NAME]);

  // Resolve effective display model using three-tier priority:
  // 1. explicit override (set via picker), 2. workspace default model from settings, 3. global DEFAULT_PROVIDER
  const globalDefault = workspaceModel.global_default || { provider: 'openai', model: '' };
  // A workspace default that resolves to the very same provider+model as the
  // global default is not a distinct choice — treat it as "global" so the picker
  // doesn't show the same provider twice (once as Global, once as Default).
  const wsDefault = workspaceModel.workspace_default || { provider: '', model: '' };
  const workspaceDefaultIsGlobal = Boolean(
    wsDefault.provider &&
    wsDefault.provider === globalDefault.provider &&
    (wsDefault.model || '') === (globalDefault.model || '')
  );
  const displayModel = (() => {
    const op = workspaceModel.override?.provider || '';
    if (op && op !== 'workspace_default') {
      if (op === 'global') return { ...globalDefault, source: 'global' };
      return { provider: op, model: workspaceModel.override.model, source: 'override' };
    }
    const dp = workspaceModel.workspace_default?.provider || '';
    if (dp && dp !== 'global' && !workspaceDefaultIsGlobal) {
      return { provider: dp, model: workspaceModel.workspace_default.model, source: 'workspace_default' };
    }
    return { ...globalDefault, source: 'global' };
  })();

  const handleModelSwitch = async (provider, model) => {
    try {
      await updateWorkspaceModel(selectedWorkspace || 'default', { provider, model });
      // Update override state; workspace_default is read-only from picker perspective
      const newOverride = (!provider || provider === 'workspace_default')
        ? { provider: '', model: '' }
        : { provider, model };
      setWorkspaceModel(prev => ({ ...prev, override: newOverride }));
      setShowModelPicker(false);
    } catch (e) {
      console.error('Error switching workspace model:', e);
    }
  };

  // Test each provider once (deduped) — the dot is per-provider, but multiple
  // models per provider now share it.
  const runProviderTests = (providers) => {
    [...new Set(providers)].filter(Boolean).forEach((provider) => {
      setProvidersTesting(s => ({ ...s, [provider]: true }));
      testProvider({ provider })
        .then(({ data }) => setProviderStatuses(s => ({ ...s, [provider]: data })))
        .catch(e => setProviderStatuses(s => ({ ...s, [provider]: { ok: false, error: e.message } })))
        .finally(() => setProvidersTesting(s => ({ ...s, [provider]: false })));
    });
  };

  const openModelPicker = () => {
    const opening = !showModelPicker;
    setShowModelPicker(opening);
    if (opening) {
      // Pick up enable/disable changes made on the Models page, then test only
      // the providers the picker actually shows a dot for: the global default
      // plus whatever providers still have enabled models.
      loadAvailableModels().then((avail) => {
        runProviderTests([globalDefault.provider, ...avail.map((a) => a.provider)]);
      });
    }
  };

  const statusDot = (provider) => {
    if (providersTesting[provider]) return <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse shrink-0" />;
    const s = providerStatuses[provider];
    if (!s) return <span className="w-2 h-2 rounded-full bg-gray-300 shrink-0" />;
    return <span className={`w-2 h-2 rounded-full shrink-0 ${s.ok ? 'bg-green-500' : 'bg-red-500'}`} />;
  };

  const currentStatusDot = () => {
    const p = displayModel.provider;
    if (providersTesting[p]) return <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />;
    const s = providerStatuses[p];
    if (!s) return null;
    return <span className={`w-1.5 h-1.5 rounded-full ${s.ok ? 'bg-green-500' : 'bg-red-500'}`} />;
  };

  const menuGroups = [
    {
      label: t('nav.groups.main'),
      items: [
        { name: t('nav.chat'), path: '/chat', icon: MessageCircle },
        { name: t('nav.dashboard'), path: '/dashboard', icon: LayoutDashboard },
      ],
    },
    {
      label: t('nav.groups.workspace'),
      items: [
        { name: t('nav.workspaces'), path: '/workspaces', icon: Folder },
        { name: t('nav.projects'), path: '/projects', icon: FolderGit2 },
        { name: t('nav.tasks'), path: '/tasks', icon: CheckSquare },
        { name: t('nav.plan'), path: '/plan', icon: CalendarClock },
        { name: t('nav.sessions'), path: '/sessions', icon: PlayCircle },
        { name: t('nav.messages'), path: '/messages', icon: ScrollText },
        { name: t('nav.views'), path: '/views', icon: Images },
        { name: t('nav.studio'), path: '/studio', icon: Shapes },
      ],
    },
    {
      // Attaching something that is not defined in here. Its own group rather
      // than a row inside Infrastructure: someone looking for "how do I connect
      // what we already have" is not looking under agents and containers, and
      // this is the answer to that question.
      label: t('nav.groups.connect'),
      items: [
        // Two directions, one question. The names carry the difference and
        // each page states it in a line: something of yours runs elsewhere and
        // reports in, or this service reaches out to a system you use.
        { name: t('nav.connections'), path: '/connections', icon: Share2 },
        { name: t('nav.connectors'), path: '/connectors', icon: Link2 },
        // A third way in, and the one that is not an integration this product
        // wrote: an MCP server hands over tools nobody here has seen, which is
        // why attaching one asks for a capability declaration. See docs/mcp.md.
        { name: t('nav.mcp'), path: '/mcp', icon: Plug },
      ],
    },
    {
      label: t('nav.groups.infrastructure'),
      items: [
        { name: t('nav.agents'), path: '/agents', icon: Users },
        // Live copies of agents, across every carrier. Nodes and Containers
        // below show the carriers themselves.
        { name: t('nav.instances'), path: '/instances', icon: Activity },
        { name: t('nav.marketplace'), path: '/marketplace', icon: Store },
        { name: t('nav.orchestrator'), path: '/orchestrator', icon: Network },
        { name: t('nav.teams'), path: '/teams', icon: UsersRound },
        { name: t('nav.nodes'), path: '/nodes', icon: Server },
        { name: t('nav.containers'), path: '/containers', icon: Box },
      ],
    },
    {
      label: t('nav.groups.tools'),
      items: [
        { name: t('nav.flows'), path: '/flows', icon: Factory },
        { name: t('nav.loops'), path: '/loops', icon: Repeat },
        { name: t('nav.registry'), path: '/registry', icon: Boxes },
        { name: t('nav.toolbox'), path: '/tools', icon: Wrench },
        { name: t('nav.skills'), path: '/skills', icon: GraduationCap },
        { name: t('nav.memory'), path: '/memory', icon: Database },
        { name: t('nav.webLogs'), path: '/web-logs', icon: Globe },
        { name: t('nav.evals'), path: '/evals', icon: FlaskConical },
        playgroundEnabled && { name: t('nav.playground'), path: '/playground', icon: Gamepad2 },
      ].filter(Boolean),
    },
    {
      label: t('nav.groups.system'),
      items: [
        // The service looking at itself: the snapshot, and the agent that can
        // follow a symptom down from it.
        { name: t('nav.health'), path: '/health', icon: Activity },
        { name: t('nav.models'), path: '/models', icon: Brain },
        { name: t('nav.costs'), path: '/costs', icon: DollarSign },
        { name: t('nav.docs'), path: '/docs', icon: BookOpen },
        { name: t('nav.settings'), path: '/settings', icon: Settings },
      ],
    },
  ];

  const handleWorkspaceChange = (e) => {
    const newWs = e.target.value;
    setSelectedWorkspace(newWs);
    if (location.pathname.startsWith('/workspaces/')) {
      navigate('/workspaces');
    }
  };

  const cycleTheme = () => {
    const idx = THEME_OPTIONS.findIndex(o => o.value === theme);
    const next = THEME_OPTIONS[(idx + 1) % THEME_OPTIONS.length];
    setTheme(next.value);
  };

  const currentThemeOption = THEME_OPTIONS.find(o => o.value === theme) || THEME_OPTIONS[2];
  const ThemeIcon = currentThemeOption.icon;

  return (
    <div className="app-shell flex h-screen overflow-hidden">
      {/* First-run onboarding (auto-opens once; re-openable from Docs) */}
      <OnboardingModal />
      {/* Sidebar */}
      <div
        className={`${
          sidebarCollapsed ? 'w-16' : 'w-64'
        } bg-white shadow-md border-r border-gray-200 h-screen overflow-y-auto overflow-x-hidden flex flex-col transition-[width] duration-200`}
      >
        <div className={`flex items-center ${sidebarCollapsed ? 'justify-center px-2' : 'justify-between px-6'} py-6`}>
          {!sidebarCollapsed && <h1 className="text-2xl font-bold text-indigo-600 truncate">{t('layout.serviceName')}</h1>}
          <button
            onClick={toggleSidebar}
            title={sidebarCollapsed ? t('layout.expandSidebar') : t('layout.collapseSidebar')}
            aria-label={sidebarCollapsed ? t('layout.expandSidebar') : t('layout.collapseSidebar')}
            className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-indigo-600 transition-colors shrink-0"
          >
            {sidebarCollapsed ? <PanelLeftOpen className="w-5 h-5" /> : <PanelLeftClose className="w-5 h-5" />}
          </button>
        </div>
        <nav className="mt-2 flex-1 pb-6">
          {menuGroups.map((group, gi) => (
            <div key={group.label}>
              {gi > 0 && <div className="mx-4 my-1 border-t border-gray-100" />}
              {!sidebarCollapsed && (
                <p className="px-6 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400 flex items-center gap-1.5">
                  {group.label}
                  {group.disabled && (
                    <span className="text-[9px] font-semibold bg-gray-100 text-gray-400 border border-gray-200 rounded px-1 py-0.5 leading-none normal-case tracking-normal">
                      {t('nav.comingSoon')}
                    </span>
                  )}
                </p>
              )}
              {sidebarCollapsed && gi === 0 && <div className="pt-3" />}
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = !group.disabled && (
                  item.path === '/dashboard'
                    ? location.pathname === item.path
                    : location.pathname === item.path || location.pathname.startsWith(item.path + '/')
                );
                if (group.disabled) {
                  return (
                    <div
                      key={item.name}
                      className={`flex items-center py-2.5 text-gray-300 cursor-not-allowed select-none ${
                        sidebarCollapsed ? 'justify-center px-2' : 'px-6'
                      }`}
                      title={sidebarCollapsed ? t('nav.comingSoonItem', { name: item.name }) : t('nav.comingSoonTitle')}
                    >
                      <Icon className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-3'}`} />
                      {!sidebarCollapsed && <span className="font-medium text-sm">{item.name}</span>}
                    </div>
                  );
                }
                return (
                  <Link
                    key={item.name}
                    to={item.path}
                    title={sidebarCollapsed ? item.name : undefined}
                    className={`flex items-center py-2.5 text-gray-700 hover:bg-indigo-50 hover:text-indigo-600 transition-colors ${
                      sidebarCollapsed ? 'justify-center px-2' : 'px-6'
                    } ${isActive ? 'bg-indigo-50 text-indigo-600 ' + (sidebarCollapsed ? 'border-l-4 border-indigo-600' : 'border-r-4 border-indigo-600') : ''}`}
                  >
                    <Icon className={`w-5 h-5 ${sidebarCollapsed ? '' : 'mr-3'}`} />
                    {!sidebarCollapsed && <span className="font-medium text-sm">{item.name}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
      </div>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Top Navbar */}
        <header className="bg-white shadow-sm border-b border-gray-200 h-16 shrink-0 flex items-center justify-between px-6 z-10">
          <div className="flex items-center space-x-4">
            <span className="text-sm font-bold text-gray-400 uppercase tracking-widest">{t('layout.workspaceLabel')}</span>
            <select
              className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 text-sm font-semibold text-gray-700 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              value={selectedWorkspace}
              onChange={handleWorkspaceChange}
            >
              {workspaces.map(ws => (
                <option key={ws.name} value={ws.name}>
                  {ws.name === 'default' ? t('layout.workspaceDefaultAll') : ws.name}
                </option>
              ))}
            </select>
            <div className="h-5 w-px bg-gray-200" />
            {/* Global model picker */}
            <div className="relative" ref={modelPickerRef}>
              <button
                onClick={openModelPicker}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                  PROVIDER_CONFIG[displayModel.provider]?.color || 'bg-gray-50 border-gray-200 text-gray-700'
                }`}
              >
                {currentStatusDot()}
                <Cpu className="w-3.5 h-3.5" />
                <span>{PROVIDER_CONFIG[displayModel.provider]?.label || displayModel.provider}</span>
                {displayModel.model && <><span className="opacity-50">·</span><span className="max-w-32 truncate">{displayModel.model}</span></>}
                {displayModel.source === 'global' && <span className="opacity-40 italic text-[10px]">{t('layout.modelPicker.globalSuffix')}</span>}
                {displayModel.source === 'workspace_default' && <span className="opacity-40 italic text-[10px]">{t('layout.modelPicker.defaultSuffix')}</span>}
                <ChevronDown className="w-3 h-3 opacity-60" />
              </button>
              {showModelPicker && (
                <div className="absolute left-0 top-full mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 min-w-64 py-1">
                  {/* Global — DEFAULT_PROVIDER from .env, lowest priority fallback */}
                  <button
                    onClick={() => handleModelSwitch('global', '')}
                    className={`w-full text-left flex items-center gap-2 px-4 py-2 text-xs hover:bg-gray-50 transition-colors ${
                      displayModel.source === 'global' ? 'bg-indigo-50' : ''
                    }`}
                  >
                    {statusDot(globalDefault.provider)}
                    <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium shrink-0 bg-gray-50 border-gray-200 text-gray-600">
                      {t('layout.modelPicker.global')}
                    </span>
                    <span className="text-gray-500 italic truncate">
                      {PROVIDER_CONFIG[globalDefault.provider]?.label || globalDefault.provider}
                      {globalDefault.model ? ` · ${globalDefault.model}` : ''}
                    </span>
                    {displayModel.source === 'global' && (
                      <span className="ml-auto text-indigo-500 font-bold shrink-0">✓</span>
                    )}
                  </button>
                  {availableModels.length > 0 && <div className="border-t border-gray-100 my-1" />}
                  {/* Every enabled catalog model (grouped by provider), with marks
                      for the global model and this workspace's default model. */}
                  {availableModels.map(({ provider, model }, idx) => {
                    const showHeader = idx === 0 || availableModels[idx - 1].provider !== provider;
                    const wd = workspaceModel.workspace_default || {};
                    const isGlobal = provider === globalDefault.provider && model === globalDefault.model;
                    const isDefault = wd.provider === provider && wd.model === model;
                    const isActive = displayModel.source !== 'global'
                      && displayModel.provider === provider && displayModel.model === model;
                    return (
                      <div key={`${provider}:${model}`}>
                        {showHeader && (
                          <p className="px-4 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                            {PROVIDER_CONFIG[provider]?.label || provider}
                          </p>
                        )}
                        <button
                          onClick={() => handleModelSwitch(provider, model)}
                          className={`w-full text-left flex items-center gap-2 px-4 py-2 text-xs hover:bg-gray-50 transition-colors ${isActive ? 'bg-indigo-50' : ''}`}
                        >
                          {statusDot(provider)}
                          <span className="truncate text-gray-700 font-mono">{model}</span>
                          {isDefault && (
                            <span className="px-1.5 py-0.5 rounded border text-[9px] font-medium shrink-0 bg-indigo-50 border-indigo-200 text-indigo-600 uppercase tracking-wider">{t('layout.modelPicker.defaultBadge')}</span>
                          )}
                          {isGlobal && (
                            <span className="px-1.5 py-0.5 rounded border text-[9px] font-medium shrink-0 bg-gray-50 border-gray-300 text-gray-500 uppercase tracking-wider">{t('layout.modelPicker.globalBadge')}</span>
                          )}
                          {isActive && (
                            <span className="ml-auto text-indigo-500 font-bold shrink-0">✓</span>
                          )}
                        </button>
                      </div>
                    );
                  })}
                  <div className="border-t border-gray-100 mt-1 pt-1">
                    <p className="px-4 py-1.5 text-[10px] text-gray-400">
                      {t('layout.modelPicker.legend')} <span className="text-green-600">●</span> {t('layout.modelPicker.legendAvailable')} &nbsp;
                      <span className="text-red-500">●</span> {t('layout.modelPicker.legendUnreachable')} &nbsp;
                      <span className="text-gray-400">●</span> {t('layout.modelPicker.legendUntested')}
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center space-x-3">
            {/* Notification bell (Plan inbox) */}
            <NotificationBell />
            {/* Live updates toggle with backend status */}
            <button
              onClick={backendOnline ? toggleLiveUpdates : undefined}
              title={!backendOnline ? t('layout.live.backendOffline') : liveUpdates ? t('layout.live.pause') : t('layout.live.resume')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                !backendOnline
                  ? 'bg-red-50 border-red-200 text-red-600 cursor-not-allowed'
                  : liveUpdates
                  ? 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100'
                  : 'bg-gray-50 border-gray-200 text-gray-500 hover:bg-gray-100'
              }`}
            >
              {!backendOnline
                ? <><WifiOff className="w-3.5 h-3.5" />{t('layout.live.offline')}</>
                : liveUpdates
                ? <><Radio className="w-3.5 h-3.5 animate-pulse" />{t('layout.live.online')}</>
                : <><Pause className="w-3.5 h-3.5" />{t('layout.live.paused')}</>}
            </button>
            {/* Theme toggle */}
            <button
              onClick={cycleTheme}
              title={t('layout.theme.tooltip', { theme: t(currentThemeOption.labelKey) })}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-900 text-xs font-medium transition-colors"
            >
              <ThemeIcon className="w-4 h-4" />
              <span>{t(currentThemeOption.labelKey)}</span>
            </button>
            {/* Interface language */}
            <LanguageSwitcher />
            <div className="h-8 w-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-xs">
              JD
            </div>
          </div>
        </header>
        <main className="flex-1 min-h-0 overflow-y-auto">{children}</main>
        {/* The chat that follows the page: a button in the corner everywhere
            but on the Chat page, which is one already. */}
        <PageChatPanel />
      </div>
    </div>
  );
};

export default Layout;
