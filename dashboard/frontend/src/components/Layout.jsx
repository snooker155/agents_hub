import React from 'react';
import { Link, useLocation } from 'react-router-dom';
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

  const menuItems = [
    { name: 'Dashboard', path: '/', icon: LayoutDashboard },
    { name: 'Tasks', path: '/tasks', icon: CheckSquare },
    { name: 'Agent Nodes', path: '/agents', icon: Shield },
    { name: 'Apply YAML', path: '/manifest', icon: FileCode },
    { name: 'Toolbox', path: '/tools', icon: Wrench },
    { name: 'Workspaces', path: '/workspaces', icon: Folder },
    { name: 'Shared Memory', path: '/memory', icon: Database },
    { name: 'Agent Factory', path: '/factory', icon: Factory },
  ];

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
            const isActive = location.pathname === item.path;
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
      <div className="flex-1 overflow-auto">
        <main className="p-8">{children}</main>
      </div>
    </div>
  );
};

export default Layout;
