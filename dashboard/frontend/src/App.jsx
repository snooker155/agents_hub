import { useState } from 'react'
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import TaskManager from './components/swe/TaskManager'
import AgentManager from './components/swe/AgentManager'

const HomeView = () => (
  <div className="p-8 max-w-7xl mx-auto">
    <header className="mb-10 text-center">
      <h1 className="text-5xl font-black tracking-tight text-slate-900 mb-4">Unified Autonomous Network</h1>
      <p className="text-xl text-slate-500 max-w-2xl mx-auto">
        A closed-loop system where Telecom Research meets AI-driven Software Engineering for self-evolution.
      </p>
    </header>

    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-12">
      <StatCard title="Network Status" value="Healthy" color="green" />
      <StatCard title="Evolution Tasks" value="14 Active" color="blue" />
      <StatCard title="Agent Pool" value="8 Online" color="purple" />
      <StatCard title="System Uptime" value="99.9%" color="indigo" />
    </div>

    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <section className="bg-white p-8 rounded-3xl shadow-sm border border-slate-200">
        <h2 className="text-2xl font-bold mb-6 flex items-center gap-3">
          <div className="w-3 h-8 bg-blue-500 rounded-full"></div>
          Operational Domain (Telecom)
        </h2>
        <div className="space-y-6">
           <div className="flex items-center justify-between p-4 bg-slate-50 rounded-2xl border border-slate-100">
              <div>
                <div className="text-sm font-bold text-slate-400 uppercase tracking-widest">Active Simulator</div>
                <div className="text-xl font-bold text-slate-900">ai-ran-sim: O-RAN</div>
              </div>
              <div className="bg-green-100 text-green-700 px-3 py-1 rounded-full text-xs font-black uppercase">Running</div>
           </div>
           <div className="grid grid-cols-2 gap-4">
              <div className="p-4 bg-blue-50 rounded-2xl border border-blue-100">
                <div className="text-2xl font-black text-blue-700">12 ms</div>
                <div className="text-xs font-bold text-blue-500 uppercase tracking-tighter">Avg. Latency</div>
              </div>
              <div className="p-4 bg-indigo-50 rounded-2xl border border-indigo-100">
                <div className="text-2xl font-black text-indigo-700">1.2 Gbps</div>
                <div className="text-xs font-bold text-indigo-500 uppercase tracking-tighter">Network Throughput</div>
              </div>
           </div>
        </div>
      </section>

      <section className="bg-white p-8 rounded-3xl shadow-sm border border-slate-200">
        <h2 className="text-2xl font-bold mb-6 flex items-center gap-3">
          <div className="w-3 h-8 bg-purple-500 rounded-full"></div>
          Evolutionary Domain (SWE)
        </h2>
        <div className="bg-slate-900 p-6 rounded-2xl mb-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
            <div className="w-32 h-32 bg-white rounded-full"></div>
          </div>
          <div className="text-sm font-bold text-blue-400 uppercase tracking-widest mb-2">Self-Evolution in Progress</div>
          <div className="text-2xl font-bold text-white mb-4 leading-tight">Implementing Predictive Handover xApp</div>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-xs font-bold text-white">BE</div>
            <div className="text-sm text-slate-300 font-medium italic">Agent BE-1 is optimizing the logic...</div>
          </div>
          <div className="w-full bg-slate-800 rounded-full h-2">
            <div className="bg-blue-500 h-2 rounded-full w-3/4 shadow-[0_0_8px_rgba(59,130,246,0.5)] animate-pulse"></div>
          </div>
        </div>
        <button className="w-full py-4 bg-slate-900 text-white rounded-2xl font-bold hover:bg-slate-800 transition-all hover:scale-[1.02] active:scale-[0.98]">
          View Task Pipeline
        </button>
      </section>
    </div>
  </div>
)

const StatCard = ({ title, value, color }) => {
  const colors = {
    green: 'text-green-600 bg-green-50 border-green-100',
    blue: 'text-blue-600 bg-blue-50 border-blue-100',
    purple: 'text-purple-600 bg-purple-50 border-purple-100',
    indigo: 'text-indigo-600 bg-indigo-50 border-indigo-100',
  }
  return (
    <div className="bg-white p-6 rounded-3xl shadow-sm border border-slate-200 flex flex-col justify-center items-center text-center">
      <h3 className="text-slate-400 text-xs font-black uppercase tracking-widest mb-2">{title}</h3>
      <div className={`text-3xl font-black ${colors[color].split(' ')[0]}`}>{value}</div>
    </div>
  )
}

const SimulatorPlaceholder = () => (
  <div className="p-8 h-full flex flex-col max-w-7xl mx-auto">
    <header className="flex justify-between items-center mb-10">
      <div>
        <h1 className="text-4xl font-black tracking-tight">RAN Simulator</h1>
        <p className="text-slate-500 mt-1">Operational Domain: High-Fidelity O-RAN Environment</p>
      </div>
      <div className="flex gap-4">
        <button className="bg-slate-200 text-slate-700 px-6 py-3 rounded-2xl font-bold hover:bg-slate-300 transition-colors">Reset</button>
        <button className="bg-blue-600 text-white px-8 py-3 rounded-2xl font-bold shadow-lg shadow-blue-200 hover:bg-blue-700 transition-all hover:scale-105 active:scale-95">Start Simulation</button>
      </div>
    </header>
    <div className="flex-1 bg-slate-900 rounded-[2.5rem] overflow-hidden relative border-[12px] border-slate-800 shadow-2xl">
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="text-center">
           <div className="w-20 h-20 border-[6px] border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-6"></div>
           <p className="text-slate-400 text-lg font-bold">Synchronizing with Simulator Backend...</p>
        </div>
      </div>
      <div className="absolute top-8 left-8 flex flex-col gap-4">
        <div className="bg-slate-800/60 backdrop-blur-xl p-5 rounded-3xl border border-slate-700/50 text-white w-56 shadow-2xl">
          <div className="text-[10px] text-blue-400 uppercase font-black tracking-widest mb-1">Active UEs</div>
          <div className="text-4xl font-black font-mono">1,240</div>
        </div>
        <div className="bg-slate-800/60 backdrop-blur-xl p-5 rounded-3xl border border-slate-700/50 text-white w-56 shadow-2xl">
          <div className="text-[10px] text-purple-400 uppercase font-black tracking-widest mb-1">Base Stations</div>
          <div className="text-4xl font-black font-mono">16</div>
        </div>
      </div>
      <div className="absolute bottom-8 right-8">
         <div className="bg-slate-800/60 backdrop-blur-xl p-6 rounded-3xl border border-slate-700/50 text-white max-w-sm">
            <div className="text-[10px] text-slate-400 uppercase font-black tracking-widest mb-3">Recent Logs</div>
            <div className="font-mono text-xs space-y-2 opacity-80">
              <div className="text-green-400">[08:24:12] Handover successful: UE-9012 -&gt; gNB-4</div>
              <div className="text-blue-400">[08:24:15] Power saving mode engaged on gNB-2</div>
              <div className="text-slate-300">[08:24:18] Traffic surge detected on Slice 01</div>
            </div>
         </div>
      </div>
    </div>
  </div>
)

function App() {
  const navigate = useNavigate();
  const location = useLocation();

  // Map route to active tab
  const getActiveTab = () => {
    const path = location.pathname;
    if (path === '/') return 'home';
    if (path.startsWith('/simulator')) return 'simulator';
    if (path.startsWith('/agents')) return 'agents';
    if (path.startsWith('/tasks')) return 'tasks';
    return 'home';
  }

  const handleTabChange = (tab) => {
    switch (tab) {
      case 'home': navigate('/'); break;
      case 'simulator': navigate('/simulator'); break;
      case 'agents': navigate('/agents'); break;
      case 'tasks': navigate('/tasks'); break;
      default: navigate('/');
    }
  }

  return (
    <div className="flex min-h-screen bg-slate-50 font-sans text-slate-900 selection:bg-blue-100 selection:text-blue-900">
      <Sidebar activeTab={getActiveTab()} setActiveTab={handleTabChange} />
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<HomeView />} />
          <Route path="/simulator" element={<SimulatorPlaceholder />} />
          <Route path="/agents" element={<div className="p-8 max-w-7xl mx-auto"><AgentManager /></div>} />
          <Route path="/tasks" element={<div className="p-8 max-w-7xl mx-auto"><TaskManager /></div>} />
          <Route path="/tasks/:id" element={<div className="p-8 max-w-7xl mx-auto"><h1 className="text-2xl font-bold">Task Details View</h1></div>} />
        </Routes>
      </main>
    </div>
  )
}

export default App
