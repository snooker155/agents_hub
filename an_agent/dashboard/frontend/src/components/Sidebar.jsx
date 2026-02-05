import React from 'react';
import { LayoutDashboard, Beaker, BarChart3, BookOpen } from 'lucide-react';

const Sidebar = ({ activeTab, setActiveTab }) => {
  const tabs = [
    { id: 'overview', name: 'Overview', icon: <LayoutDashboard size={20} /> },
    { id: 'experiments', name: 'Experiments', icon: <Beaker size={20} /> },
    { id: 'analytics', name: 'Analytics', icon: <BarChart3 size={20} /> },
    { id: 'glossary', name: 'Glossary', icon: <BookOpen size={20} /> },
  ];

  return (
    <div className="sidebar shadow-sm" style={{ width: '250px', height: '100vh', backgroundColor: 'white' }}>
      <div className="list-group list-group-flush">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`list-group-item list-group-item-action border-0 py-3 d-flex align-items-center ${
              activeTab === tab.id ? 'active' : ''
            }`}
            style={{ borderRadius: '0' }}
          >
            <span className="me-3">{tab.icon}</span>
            {tab.name}
          </button>
        ))}
      </div>
    </div>
  );
};

export default Sidebar;
