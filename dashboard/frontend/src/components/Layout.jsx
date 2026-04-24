import { useState, useEffect, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useWorkspace } from './WorkspaceContext';
import { useTheme } from './ThemeContext';
import { getWorkspaces, getSettings, getWorkspaceModel, updateWorkspaceModel, testProvider, getHealth } from '../api';
import {
  LayoutDashboard,
  CheckSquare,
  Folder,
  Database,
  Factory,
  FileCode,
  Wrench,
  Users,
  PlayCircle,
  MessageCircle,
  MessageSquare,
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
  WifiOff,
} from 'lucide-react';

const PROVIDER_CONFIG = {
  openai:    { label: 'OpenAI',    color: 'text-green-700 bg-green-50 border-green-200' },
  anthropic: { label: 'Anthropic', color: 'text-orange-700 bg-orange-50 border-orange-200' },
  google:    { label: 'Google',    color: 'text-blue-700 bg-blue-50 border-blue-200' },
  ollama:    { label: 'Ollama',    color: 'text-purple-700 bg-purple-50 border-purple-200' },
  lmstudio:  { label: 'LM Studio', color: 'text-teal-700 bg-teal-50 border-teal-200' },
};

const THEME_OPTIONS = [
  { value: 'light',  icon: Sun,     label: 'Light' },
  { value: 'dark',   icon: Moon,    label: 'Dark' },
  { value: 'system', icon: Monitor, label: 'System' },
];

const Layout = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { selectedWorkspace, setSelectedWorkspace, liveUpdates, toggleLiveUpdates } = useWorkspace();
  const { theme, setTheme } = useTheme();
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
  const modelPickerRef = useRef(null);

  useEffect(() => {
    const fetchWorkspaces = async () => {
      try {
        const resp = await getWorkspaces();
        setWorkspaces(resp.data);
        if ((!selectedWorkspace || selectedWorkspace === '') && resp.data.length > 0) {
          setSelectedWorkspace(resp.data[0].name);
        }
      } catch (error) {
        console.error('Error fetching workspaces:', error);
      }
    };
    fetchWorkspaces();
  }, [selectedWorkspace, setSelectedWorkspace]);

  // Fetch all configured provider models once — used for the explicit override rows
  useEffect(() => {
    getSettings().then(({ data }) => {
      const avail = [
        { provider: 'openai',    model: data.model },
        { provider: 'anthropic', model: data.anthropic_model },
        { provider: 'google',    model: data.google_model },
        { provider: 'ollama',    model: data.ollama_model },
        { provider: 'lmstudio',  model: data.lmstudio_model },
      ].filter(o => o.model);
      setAvailableModels(avail);
    }).catch(e => console.error('Error fetching settings:', e));
  }, []);

  // Re-fetch workspace model whenever workspace changes
  useEffect(() => {
    if (!selectedWorkspace) return;
    getWorkspaceModel(selectedWorkspace)
      .then(({ data }) => setWorkspaceModel(data))
      .catch(() => setWorkspaceModel(prev => ({ ...prev, override: { provider: '', model: '' }, workspace_default: { provider: '', model: '' } })));
  }, [selectedWorkspace]);

  useEffect(() => {
    const checkBackend = () => {
      getHealth()
        .then(() => setBackendOnline(true))
        .catch(() => setBackendOnline(false));
    };
    checkBackend();
    const interval = setInterval(checkBackend, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false);
      }
    };
    if (showModelPicker) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showModelPicker]);

  // Resolve effective display model using three-tier priority:
  // 1. explicit override (set via picker), 2. workspace default model from settings, 3. global DEFAULT_PROVIDER
  const globalDefault = workspaceModel.global_default || { provider: 'openai', model: '' };
  const displayModel = (() => {
    const op = workspaceModel.override?.provider || '';
    if (op && op !== 'workspace_default') {
      if (op === 'global') return { ...globalDefault, source: 'global' };
      return { provider: op, model: workspaceModel.override.model, source: 'override' };
    }
    const dp = workspaceModel.workspace_default?.provider || '';
    if (dp && dp !== 'global') {
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

  const runProviderTests = (models) => {
    models.forEach(({ provider }) => {
      setProvidersTesting(s => ({ ...s, [provider]: true }));
      testProvider({ provider })
        .then(({ data }) => setProviderStatuses(s => ({ ...s, [provider]: data })))
        .catch(e => setProviderStatuses(s => ({ ...s, [provider]: { ok: false, error: e.message } })))
        .finally(() => setProvidersTesting(s => ({ ...s, [provider]: false })));
    });
  };

  const openModelPicker = () => {
    setShowModelPicker(p => {
      if (!p) {
        const toTest = [...availableModels];
        const wdp = workspaceModel.workspace_default?.provider;
        if (wdp && wdp !== 'global' && !toTest.find(m => m.provider === wdp)) {
          toTest.push({ provider: wdp, model: workspaceModel.workspace_default.model });
        }
        runProviderTests(toTest);
      }
      return !p;
    });
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
      label: 'Main',
      items: [
        { name: 'Chat', path: '/chat', icon: MessageCircle },
        { name: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
      ],
    },
    {
      label: 'Workspace',
      items: [
        { name: 'Workspaces', path: '/workspaces', icon: Folder },
        { name: 'Projects', path: '/projects', icon: FolderGit2 },
        { name: 'Tasks', path: '/tasks', icon: CheckSquare },
        { name: 'Sessions', path: '/sessions', icon: PlayCircle },
        { name: 'Messages', path: '/messages', icon: MessageSquare },
      ],
    },
    {
      label: 'Infrastructure',
      items: [
        { name: 'Agents', path: '/agents', icon: Users },
        { name: 'Orchestrator', path: '/orchestrator', icon: Network },
        { name: 'Nodes', path: '/nodes', icon: Server },
        { name: 'Containers', path: '/containers', icon: Box },
      ],
    },
    {
      label: 'Tools',
      items: [
        { name: 'Agent Flows', path: '/flows', icon: Factory },
        { name: 'Toolbox', path: '/tools', icon: Wrench },
        { name: 'Shared Memory', path: '/memory', icon: Database },
      ],
    },
    {
      label: 'System',
      items: [
        { name: 'Settings', path: '/settings', icon: Settings },
      ],
    },
    {
      label: 'Future Dev',
      disabled: true,
      items: [
        { name: 'Apply YAML', path: '/manifest', icon: FileCode },
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
    <div className="flex h-screen bg-gray-100 overflow-hidden">
      {/* Sidebar */}
      <div className="w-64 bg-white shadow-md border-r border-gray-200 h-screen overflow-y-auto flex flex-col">
        <div className="p-6">
          <h1 className="text-2xl font-bold text-indigo-600">Orchestrator</h1>
        </div>
        <nav className="mt-2 flex-1">
          {menuGroups.map((group, gi) => (
            <div key={group.label}>
              {gi > 0 && <div className="mx-4 my-1 border-t border-gray-100" />}
              <p className="px-6 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400 flex items-center gap-1.5">
                {group.label}
                {group.disabled && (
                  <span className="text-[9px] font-semibold bg-gray-100 text-gray-400 border border-gray-200 rounded px-1 py-0.5 leading-none normal-case tracking-normal">
                    coming soon
                  </span>
                )}
              </p>
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
                      className="flex items-center px-6 py-2.5 text-gray-300 cursor-not-allowed select-none"
                      title="Coming soon"
                    >
                      <Icon className="w-5 h-5 mr-3" />
                      <span className="font-medium text-sm">{item.name}</span>
                    </div>
                  );
                }
                return (
                  <Link
                    key={item.name}
                    to={item.path}
                    className={`flex items-center px-6 py-2.5 text-gray-700 hover:bg-indigo-50 hover:text-indigo-600 transition-colors ${
                      isActive ? 'bg-indigo-50 text-indigo-600 border-r-4 border-indigo-600' : ''
                    }`}
                  >
                    <Icon className="w-5 h-5 mr-3" />
                    <span className="font-medium text-sm">{item.name}</span>
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
        <header className="bg-white shadow-sm border-b border-gray-200 h-16 shrink-0 flex items-center justify-between px-8 z-10">
          <div className="flex items-center space-x-4">
            <span className="text-sm font-bold text-gray-400 uppercase tracking-widest">Workspace:</span>
            <select
              className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 text-sm font-semibold text-gray-700 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              value={selectedWorkspace}
              onChange={handleWorkspaceChange}
            >
              {workspaces.map(ws => (
                <option key={ws.name} value={ws.name}>
                  {ws.name === 'default' ? 'default (All)' : ws.name}
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
                {displayModel.source === 'global' && <span className="opacity-40 italic text-[10px]">(global)</span>}
                {displayModel.source === 'workspace_default' && <span className="opacity-40 italic text-[10px]">(default)</span>}
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
                    <span className="w-2 h-2 rounded-full bg-gray-300 shrink-0" />
                    <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium shrink-0 bg-gray-50 border-gray-200 text-gray-600">
                      Global
                    </span>
                    <span className="text-gray-500 italic truncate">
                      {PROVIDER_CONFIG[globalDefault.provider]?.label || globalDefault.provider}
                      {globalDefault.model ? ` · ${globalDefault.model}` : ''}
                    </span>
                    {displayModel.source === 'global' && (
                      <span className="ml-auto text-indigo-500 font-bold shrink-0">✓</span>
                    )}
                  </button>
                  {/* Workspace Default — resolved from workspace settings in .workspace.json */}
                  {workspaceModel.workspace_default?.provider && workspaceModel.workspace_default.provider !== 'global' && (
                    <button
                      onClick={() => handleModelSwitch('workspace_default', '')}
                      className={`w-full text-left flex items-center gap-2 px-4 py-2 text-xs hover:bg-gray-50 transition-colors ${
                        displayModel.source === 'workspace_default' ? 'bg-indigo-50' : ''
                      }`}
                    >
                      {statusDot(workspaceModel.workspace_default.provider)}
                      <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium shrink-0 bg-indigo-50 border-indigo-200 text-indigo-600">
                        Default
                      </span>
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] font-medium shrink-0 ${PROVIDER_CONFIG[workspaceModel.workspace_default.provider]?.color || 'bg-gray-50 border-gray-200 text-gray-600'}`}>
                        {PROVIDER_CONFIG[workspaceModel.workspace_default.provider]?.label || workspaceModel.workspace_default.provider}
                      </span>
                      <span className="truncate text-gray-700">{workspaceModel.workspace_default.model}</span>
                      {displayModel.source === 'workspace_default' && (
                        <span className="ml-auto text-indigo-500 font-bold shrink-0">✓</span>
                      )}
                    </button>
                  )}
                  {availableModels.filter(({ provider }) =>
                    provider !== globalDefault.provider &&
                    provider !== workspaceModel.workspace_default?.provider
                  ).length > 0 && <div className="border-t border-gray-100 my-1" />}
                  {availableModels
                    .filter(({ provider }) =>
                      provider !== globalDefault.provider &&
                      provider !== workspaceModel.workspace_default?.provider
                    )
                    .map(({ provider, model }) => (
                    <button
                      key={provider}
                      onClick={() => handleModelSwitch(provider, model)}
                      className={`w-full text-left flex items-center gap-2 px-4 py-2 text-xs hover:bg-gray-50 transition-colors ${
                        displayModel.source === 'override' && displayModel.provider === provider ? 'bg-indigo-50' : ''
                      }`}
                    >
                      {statusDot(provider)}
                      <span className={`px-1.5 py-0.5 rounded border text-[10px] font-medium shrink-0 ${PROVIDER_CONFIG[provider]?.color}`}>
                        {PROVIDER_CONFIG[provider]?.label || provider}
                      </span>
                      <span className="truncate text-gray-700">{model}</span>
                      {displayModel.source === 'override' && displayModel.provider === provider && (
                        <span className="ml-auto text-indigo-500 font-bold shrink-0">✓</span>
                      )}
                    </button>
                  ))}
                  <div className="border-t border-gray-100 mt-1 pt-1">
                    <p className="px-4 py-1.5 text-[10px] text-gray-400">
                      Dots: <span className="text-green-600">●</span> available &nbsp;
                      <span className="text-red-500">●</span> unreachable &nbsp;
                      <span className="text-gray-400">●</span> untested
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center space-x-3">
            {/* Live updates toggle with backend status */}
            <button
              onClick={backendOnline ? toggleLiveUpdates : undefined}
              title={!backendOnline ? 'Backend offline' : liveUpdates ? 'Pause live updates' : 'Resume live updates'}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                !backendOnline
                  ? 'bg-red-50 border-red-200 text-red-600 cursor-not-allowed'
                  : liveUpdates
                  ? 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100'
                  : 'bg-gray-50 border-gray-200 text-gray-500 hover:bg-gray-100'
              }`}
            >
              {!backendOnline
                ? <><WifiOff className="w-3.5 h-3.5" />Offline</>
                : liveUpdates
                ? <><Radio className="w-3.5 h-3.5 animate-pulse" />Live</>
                : <><Pause className="w-3.5 h-3.5" />Paused</>}
            </button>
            {/* Theme toggle */}
            <button
              onClick={cycleTheme}
              title={`Theme: ${currentThemeOption.label} (click to cycle)`}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-900 text-xs font-medium transition-colors"
            >
              <ThemeIcon className="w-4 h-4" />
              <span>{currentThemeOption.label}</span>
            </button>
            <div className="h-8 w-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-xs">
              JD
            </div>
          </div>
        </header>
        <main className="p-8 flex-1 min-h-0 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
};

export default Layout;
