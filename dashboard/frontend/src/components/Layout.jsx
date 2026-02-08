import React, { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useWorkspace } from './WorkspaceContext';
import { getWorkspaces } from '../api';
import {
  LayoutDashboard,
  Users,
  CheckSquare,
  Folder,
  Database,
  Factory,
  Zap,
  FileCode,
  Wrench,
  Shield
} from 'lucide-react';

const Layout = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { selectedWorkspace, setSelectedWorkspace } = useWorkspace();
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

  const menuItems = [
    { name: 'Workspaces', path: '/workspaces', icon: Folder },
    { name: 'Dashboard', path: '/', icon: LayoutDashboard },
    { name: 'Orchestrator', path: '/orchestrator', icon: Zap },
    { name: 'Tasks', path: '/tasks', icon: CheckSquare },
    { name: 'Agent Nodes', path: '/agents', icon: Shield },
    { name: 'Apply YAML', path: '/manifest', icon: FileCode },
    { name: 'Toolbox', path: '/tools', icon: Wrench },
    { name: 'Shared Memory', path: '/memory', icon: Database },
    { name: 'Agent Factory', path: '/factory', icon: Factory },
  ];

  const handleWorkspaceChange = (e) => {
    const newWs = e.target.value;
    setSelectedWorkspace(newWs);

    // Redirect if on a task-related page
    if (location.pathname.startsWith('/tasks') || location.pathname.startsWith('/workspaces/')) {
      navigate('/');
    }
  };

  return (
    <div className="flex h-screen bg-gray-100">
      {/* Sidebar */}
      <div className="w-64 bg-white shadow-md">
        <div className="p-6">
          <h1 className="text-2xl font-bold text-indigo-600">Orchestrator</h1>
        </div>
        <nav className="mt-6">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = item.path === '/'
              ? location.pathname === '/'
              : location.pathname.startsWith(item.path);
            return (
              <Link
                key={item.name}
                to={item.path}
                className={`flex items-center px-6 py-3 text-gray-700 hover:bg-indigo-50 hover:text-indigo-600 transition-colors ${
                  isActive ? 'bg-indigo-50 text-indigo-600 border-r-4 border-indigo-600' : ''
                }`}
              >
                <Icon className="w-5 h-5 mr-3" />
                <span className="font-medium">{item.name}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto flex flex-col">
        {/* Top Navbar */}
        <header className="bg-white shadow-sm h-16 flex items-center justify-between px-8 z-10">
          <div className="flex items-center space-x-4">
            <span className="text-sm font-bold text-gray-400 uppercase tracking-widest">Workspace:</span>
            <select
              className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 text-sm font-semibold text-gray-700 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              value={selectedWorkspace}
              onChange={handleWorkspaceChange}
            >
              {workspaces.map(ws => (
                <option key={ws.name} value={ws.name}>{ws.name}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center space-x-4">
            <div className="h-8 w-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-600 font-bold text-xs">
              JD
            </div>
          </div>
        </header>
        <main className="p-8 flex-1">{children}</main>
      </div>
    </div>
  );
};

export default Layout;
