import React, { useState, useEffect } from 'react';
import axios from 'axios';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import StatCard from './components/StatCard';
import ProcessTable from './components/ProcessTable';
import ExperimentCard from './components/ExperimentCard';
import DecisionTable from './components/DecisionTable';
import Glossary from './components/Glossary';
import { ActionPieChart, ModePieChart, KPILineChart } from './components/Charts';

function App() {
  const [activeTab, setActiveTab] = useState('overview');
  const [connected, setConnected] = useState(false);
  const [metrics, setMetrics] = useState({ n_obs: 0, avg_bler: 0, avg_tpt: 0, p95_latency_ms: 0 });
  const [analytics, setAnalytics] = useState({ action_type_counts: [], mode_counts: [] });
  const [processes, setProcesses] = useState([]);
  const [experiments, setExperiments] = useState([]);
  const [observations, setObservations] = useState([]);
  const [decisions, setDecisions] = useState([]);

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [runRequest, setRunRequest] = useState({ script: '', configs: [], selectedConfig: '', ticks: '' });

  const fetchData = async () => {
    try {
      const [mRes, aRes, pRes, eRes, oRes, dRes] = await Promise.all([
        axios.get('/api/metrics'),
        axios.get('/api/analytics'),
        axios.get('/api/processes'),
        axios.get('/api/experiments'),
        axios.get('/api/observations?limit=100'),
        axios.get('/api/decisions?limit=50')
      ]);

      setMetrics(mRes.data);
      setAnalytics(aRes.data);
      setProcesses(pRes.data.items);
      setExperiments(eRes.data.items);
      setObservations(oRes.data.rows.reverse());
      setDecisions(dRes.data.rows);
      setConnected(true);
    } catch (err) {
      console.error('Fetch error:', err);
      setConnected(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleStopProcess = async (pid) => {
    if (!window.confirm(`Stop process ${pid}?`)) return;
    try {
      await axios.post('/api/experiment/stop', { pid });
      fetchData();
    } catch (err) {
      alert('Failed to stop process');
    }
  };

  const handleOpenRunModal = (script, configs) => {
    setRunRequest({ script, configs, selectedConfig: '', ticks: '' });
    setShowModal(true);
  };

  const handleLaunch = async () => {
    try {
      await axios.post('/api/experiment/run', {
        script: runRequest.script,
        config: runRequest.selectedConfig || null,
        ticks: runRequest.ticks ? parseInt(runRequest.ticks) : null
      });
      setShowModal(false);
      fetchData();
    } catch (err) {
      alert('Failed to launch experiment');
    }
  };

  return (
    <div className="d-flex flex-column" style={{ minHeight: '100vh', backgroundColor: '#f8f9fa' }}>
      <Header connected={connected} />

      <div className="d-flex flex-grow-1">
        <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

        <main className="flex-grow-1 p-4">
          {activeTab === 'overview' && (
            <div className="tab-pane">
              <div className="row g-3 mb-4">
                <div className="col-md-3"><StatCard title="Total Observations" value={metrics.n_obs} /></div>
                <div className="col-md-3"><StatCard title="Avg BLER" value={(metrics.avg_bler * 100).toFixed(2)} unit="%" /></div>
                <div className="col-md-3"><StatCard title="Avg Throughput" value={metrics.avg_tpt.toFixed(2)} unit=" Mbps" /></div>
                <div className="col-md-3"><StatCard title="P95 Latency" value={metrics.p95_latency_ms.toFixed(2)} unit=" ms" /></div>
              </div>

              <div className="row g-3">
                <div className="col-md-8">
                  <ProcessTable processes={processes} onStop={handleStopProcess} />
                </div>
                <div className="col-md-4">
                  <div className="card border-0 shadow-sm h-100">
                    <div className="card-header bg-white border-0 py-3">
                      <h5 className="card-title mb-0">Action Distribution</h5>
                    </div>
                    <div className="card-body">
                      <ActionPieChart data={analytics.action_type_counts} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'experiments' && (
            <div className="tab-pane">
              <div className="row g-3 mb-4">
                <div className="col-12">
                  <div className="card border-0 shadow-sm">
                    <div className="card-header bg-white border-0 py-3 d-flex justify-content-between align-items-center">
                      <h5 className="card-title mb-0">Running Experiments</h5>
                      <span className="text-muted small">{processes.length} process(es)</span>
                    </div>
                    <div className="card-body">
                      <ProcessTable processes={processes} onStop={handleStopProcess} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-4">
                {experiments.map((exp, idx) => {
                  const isRunning = processes.some(p => (p.cmd || '').includes(exp.path) || exp.scripts.some(s => (p.cmd || '').includes(s)));
                  return (
                    <div key={idx} className="col">
                      <ExperimentCard experiment={exp} onRun={handleOpenRunModal} metrics={metrics} isRunning={isRunning} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {activeTab === 'analytics' && (
            <div className="tab-pane">
              <div className="row g-3 mb-4">
                <div className="col-md-6">
                  <div className="card border-0 shadow-sm">
                    <div className="card-header bg-white border-0 py-3">
                      <h5 className="card-title mb-0">Service Modes</h5>
                    </div>
                    <div className="card-body">
                      <ModePieChart data={analytics.mode_counts} />
                    </div>
                  </div>
                </div>
                <div className="col-md-6">
                  <div className="card border-0 shadow-sm">
                    <div className="card-header bg-white border-0 py-3">
                      <h5 className="card-title mb-0">KPI Trends (last 100)</h5>
                    </div>
                    <div className="card-body">
                      <KPILineChart data={observations} />
                    </div>
                  </div>
                </div>
              </div>
              <DecisionTable decisions={decisions} />
            </div>
          )}

          {activeTab === 'glossary' && (
            <Glossary />
          )}
        </main>
      </div>

      {/* Launch Modal */}
      {showModal && (
        <div className="modal fade show d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <div className="modal-dialog">
            <div className="modal-content border-0 shadow">
              <div className="modal-header">
                <h5 className="modal-title">Launch Experiment</h5>
                <button type="button" className="btn-close" onClick={() => setShowModal(false)}></button>
              </div>
              <div className="modal-body">
                <div className="mb-3">
                  <label className="form-label">Script</label>
                  <input type="text" className="form-control" value={runRequest.script} readOnly />
                </div>
                <div className="mb-3">
                  <label className="form-label">Configuration</label>
                  <select
                    className="form-select"
                    value={runRequest.selectedConfig}
                    onChange={(e) => setRunRequest({...runRequest, selectedConfig: e.target.value})}
                  >
                    <option value="">(No config)</option>
                    {runRequest.configs.map(cfg => (
                      <option key={cfg} value={cfg}>{cfg.split('/').pop()}</option>
                    ))}
                  </select>
                </div>
                <div className="mb-3">
                  <label className="form-label">Ticks</label>
                  <input
                    type="number"
                    className="form-control"
                    placeholder="e.g. 500"
                    value={runRequest.ticks}
                    onChange={(e) => setRunRequest({...runRequest, ticks: e.target.value})}
                  />
                </div>
              </div>
              <div className="modal-footer">
                <button className="btn btn-secondary" onClick={() => setShowModal(false)}>Cancel</button>
                <button className="btn btn-primary" onClick={handleLaunch}>Launch</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
