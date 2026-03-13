import { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useWorkspace } from './WorkspaceContext';
import { useTheme } from './ThemeContext';
import { getWorkspaces } from '../api';
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
  Server,
  Settings,
  Sun,
  Moon,
  Monitor,
  Variable,
  Network,
  Radio,
  Pause,
} from 'lucide-react';

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

  const menuGroups = [
    {
      label: 'Main',
      items: [
        { name: 'Chat', path: '/', icon: MessageCircle },
        { name: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
      ],
    },
    {
      label: 'Workspace',
      items: [
        { name: 'Workspaces', path: '/workspaces', icon: Folder },
        { name: 'Tasks', path: '/tasks', icon: CheckSquare },
        { name: 'Sessions', path: '/sessions', icon: PlayCircle },
      ],
    },
    {
      label: 'Infrastructure',
      items: [
        { name: 'Agents', path: '/agents', icon: Users },
        { name: 'Orchestrator', path: '/orchestrator', icon: Network },
        { name: 'Nodes', path: '/nodes', icon: Server },
      ],
    },
    {
      label: 'Tools',
      items: [
        { name: 'Agent Factory', path: '/factory', icon: Factory },
        { name: 'Toolbox', path: '/tools', icon: Wrench },
        { name: 'Apply YAML', path: '/manifest', icon: FileCode },
        { name: 'Shared Memory', path: '/memory', icon: Database },
        { name: 'Env Variables', path: '/env', icon: Variable },
      ],
    },
    {
      label: 'System',
      items: [
        { name: 'Settings', path: '/settings', icon: Settings },
      ],
    },
  ];

  const handleWorkspaceChange = (e) => {
    const newWs = e.target.value;
    setSelectedWorkspace(newWs);
    if (location.pathname.startsWith('/tasks') || location.pathname.startsWith('/workspaces/')) {
      navigate('/');
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
              <p className="px-6 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                {group.label}
              </p>
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive =
                  item.path === '/' || item.path === '/dashboard'
                    ? location.pathname === item.path
                    : location.pathname.startsWith(item.path);
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
          </div>
          <div className="flex items-center space-x-3">
            {/* Live updates toggle */}
            <button
              onClick={toggleLiveUpdates}
              title={liveUpdates ? 'Pause live updates' : 'Resume live updates'}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors ${
                liveUpdates
                  ? 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100'
                  : 'bg-gray-50 border-gray-200 text-gray-500 hover:bg-gray-100'
              }`}
            >
              {liveUpdates
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
