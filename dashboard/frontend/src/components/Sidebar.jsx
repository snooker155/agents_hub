import React from 'react';
import { Home, Zap, Network, ClipboardList, LayoutDashboard } from 'lucide-react';

const Sidebar = ({ activeTab, setActiveTab }) => {
  const menuItems = [
    { id: 'home', icon: Home, label: 'Home' },
    { id: 'simulator', icon: Zap, label: 'RAN Simulator' },
    { id: 'agents', icon: Network, label: 'Agent Hub' },
    { id: 'tasks', icon: ClipboardList, label: 'Task Manager' },
  ];

  return (
    <div className="w-64 bg-slate-900 text-white flex flex-col h-screen sticky top-0">
      <div className="p-6 flex items-center gap-3 border-b border-slate-800">
        <LayoutDashboard className="text-blue-400" size={32} />
        <span className="text-xl font-bold tracking-tight">Unified AN</span>
      </div>
      <nav className="flex-1 mt-6">
        {menuItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={`w-full flex items-center gap-4 px-6 py-4 transition-colors ${
              activeTab === item.id
                ? 'bg-blue-600 text-white border-r-4 border-blue-300'
                : 'text-slate-400 hover:bg-slate-800 hover:text-white'
            }`}
          >
            <item.icon size={20} />
            <span className="font-medium">{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="p-4 border-t border-slate-800">
        <div className="text-xs text-slate-500 uppercase font-bold px-2 mb-2">System Status</div>
        <div className="flex items-center gap-2 px-2">
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
          <span className="text-sm text-slate-300">Operational</span>
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
