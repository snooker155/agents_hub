import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import { getNodes, startNode, stopNode, restartNode, deleteNode, getNodeLogs, getAgents, getWorkspaces, getWorkspace, setWorkspaceAgentMode, getSettings } from '../api';
import {
  Play,
  RotateCw,
  Square,
  Trash2,
  FileText,
  RefreshCw,
  Plus,
  X,
  Loader,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Server,
  Activity,
  Globe,
  ExternalLink,
  Box,
} from 'lucide-react';

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS = {
  running:  { dot: 'bg-green-500 animate-pulse', badge: 'bg-green-100 text-green-800',  label: 'Running' },
  starting: { dot: 'bg-yellow-400 animate-pulse', badge: 'bg-yellow-100 text-yellow-800', label: 'Starting' },
  stopping: { dot: 'bg-orange-400 animate-pulse', badge: 'bg-orange-100 text-orange-800', label: 'Stopping' },
  stopped:  { dot: 'bg-gray-400',                badge: 'bg-gray-100 text-gray-600',    label: 'Stopped' },
  failed:   { dot: 'bg-red-500',                 badge: 'bg-red-100 text-red-700',      label: 'Failed' },
  completed:{ dot: 'bg-blue-400',                badge: 'bg-blue-100 text-blue-700',    label: 'Completed' },
};

function StatusBadge({ status }) {
  const s = STATUS[status] || STATUS.stopped;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold ${s.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.dot}`} />
      {s.label}
    </span>
  );
}

function uptime(startedAt, finishedAt) {
  if (!startedAt) return '—';
  const end = finishedAt ? new Date(finishedAt) : new Date();
  const secs = Math.max(0, Math.floor((end - new Date(startedAt)) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Logs modal ────────────────────────────────────────────────────────────────

function LogsModal({ node, onClose }) {
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef(null);
  const { liveUpdates } = useWorkspace();

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await getNodeLogs(node.node_id);
        if (!cancelled) { setLogs(r.data.logs || '(empty)'); setLoading(false); }
      } catch {
        if (!cancelled) { setLogs('Failed to load logs.'); setLoading(false); }
      }
    };
    load();
    // Auto-refresh logs every 3s while node is active (running/starting/stopping)
    const isActive = ['running', 'starting', 'stopping'].includes(node.status);
    if (!liveUpdates || !isActive) return () => { cancelled = true; };
    const interval = setInterval(load, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [node.node_id, node.status, liveUpdates]);

  useEffect(() => { bottomRef.current?.scrollIntoView(); }, [logs]);

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <StatusBadge status={node.status} />
            <span className="text-gray-200 text-sm font-semibold">
              {node.agent_name || node.agent_id}
            </span>
            <span className="text-gray-500 text-xs">{node.node_id.slice(0, 12)}…</span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-5">
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader className="w-5 h-5 animate-spin text-indigo-400" />
            </div>
          ) : (
            <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logs}</pre>
          )}
          <div ref={bottomRef} />
        </div>
        {['running', 'starting', 'stopping'].includes(node.status) && (
          <div className="px-5 py-2 border-t border-gray-800 text-xs text-gray-500 flex items-center gap-1.5">
            <Activity className="w-3 h-3 animate-pulse text-green-500" />
            Live — refreshing every 3s
          </div>
        )}
      </div>
    </div>
  );
}

// ── Start Node modal ──────────────────────────────────────────────────────────

function StartNodeModal({ onClose, onStarted, agents, defaultAgentId, nodes, currentWorkspace, wsCapacityOverrides, wsAllowedAgents, agentMode, wsAgentMode, onSetAgentMode }) {
  const [agentId, setAgentId] = useState(defaultAgentId || '');
  const [label, setLabel] = useState('');
  const [nodeType, setNodeType] = useState('worker');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const isDefaultWs = !currentWorkspace || currentWorkspace === 'default';

  // Filter agents: if workspace has allowed_agents, restrict to those
  const visibleAgents = currentWorkspace && wsAllowedAgents && wsAllowedAgents.length > 0
    ? agents.filter(a => wsAllowedAgents.includes(a.id))
    : agents;

  const wsCapacity = !isDefaultWs && agentId ? (wsCapacityOverrides[agentId] ?? 1) : null;
  const runningForSelection = agentId && currentWorkspace
    ? nodes.filter(n => n.agent_id === agentId && n.workspace === currentWorkspace && (n.status === 'running' || n.status === 'starting')).length
    : 0;
  const capacityExceeded = wsCapacity !== null && runningForSelection >= wsCapacity;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!agentId) { setError('Select an agent'); return; }
    if (capacityExceeded) { setError(`Node limit (${wsCapacity}) reached for this agent in workspace "${currentWorkspace}"`); return; }
    setError('');
    setSubmitting(true);
    try {
      const r = await startNode({ agent_id: agentId, workspace: currentWorkspace || null, label: label || null, node_type: nodeType });
      onStarted(r.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to start node');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
        <div className="flex items-center justify-between p-5 border-b">
          <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
            <Play className="w-4 h-4 text-indigo-600" />
            Start Node
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>
          )}

          {/* Agent mode badge */}
          <div className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs border ${
            agentMode === 'docker'
              ? 'bg-blue-50 border-blue-200 text-blue-800'
              : 'bg-gray-50 border-gray-200 text-gray-700'
          }`}>
            <div className="flex items-center gap-2">
              {agentMode === 'docker'
                ? <Box className="w-3.5 h-3.5 text-blue-500" />
                : <Server className="w-3.5 h-3.5 text-gray-400" />}
              <span>Agent mode:</span>
              <span className="font-semibold">
                {agentMode === 'docker' ? 'Docker container' : 'Local process'}
              </span>
              {currentWorkspace && wsAgentMode && (
                <span className="text-xs opacity-60">(workspace)</span>
              )}
            </div>
            {onSetAgentMode ? (
              <button
                type="button"
                onClick={() => onSetAgentMode(agentMode === 'docker' ? 'local' : 'docker')}
                className={`text-xs font-medium px-2 py-0.5 rounded-full border transition-colors ${
                  agentMode === 'docker'
                    ? 'border-blue-300 text-blue-700 hover:bg-blue-100'
                    : 'border-gray-300 text-gray-600 hover:bg-gray-100'
                }`}
              >
                Switch to {agentMode === 'docker' ? 'local' : 'docker'}
              </button>
            ) : (
              <span className="text-xs opacity-50">global setting</span>
            )}
          </div>

          {currentWorkspace && currentWorkspace !== 'default' && (
            <div className="flex items-center gap-2 text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
              <span className="text-gray-500">Workspace:</span>
              <span className=" font-semibold text-gray-800">{currentWorkspace}</span>
              {agentId && wsCapacity !== null && (
                <span className={`ml-auto font-medium ${capacityExceeded ? 'text-orange-600' : 'text-gray-400'}`}>
                  {runningForSelection}/{wsCapacity} running
                </span>
              )}
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
              Agent <span className="text-red-500">*</span>
            </label>
            <select
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
            >
              <option value="">— Select agent —</option>
              {visibleAgents.map(a => (
                <option key={a.id} value={a.id}>{a.name} {a.domain ? `(${a.domain})` : ''}</option>
              ))}
            </select>
            {capacityExceeded && (
              <p className="text-xs text-orange-600 mt-1 font-medium">
                Node limit reached — stop a running node first
              </p>
            )}
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
              Label <span className="text-gray-400 font-normal normal-case">(optional)</span>
            </label>
            <input
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="e.g. production, dev-worker"
              value={label}
              onChange={e => setLabel(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
              Node Type
            </label>
            <div className="flex gap-2">
              {[
                { value: 'worker', label: 'Task Worker', desc: 'Polls for assigned tasks' },
                { value: 'service', label: 'HTTP Service', desc: 'Serves external requests' },
              ].map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setNodeType(opt.value)}
                  className={`flex-1 rounded-lg border px-3 py-2.5 text-left transition-colors ${
                    nodeType === opt.value
                      ? opt.value === 'service'
                        ? 'border-violet-400 bg-violet-50 text-violet-800'
                        : 'border-indigo-400 bg-indigo-50 text-indigo-800'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  <div className="text-xs font-semibold">{opt.label}</div>
                  <div className="text-xs opacity-60 mt-0.5">{opt.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200">
              Cancel
            </button>
            <button type="submit" disabled={submitting || !agentId || capacityExceeded}
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed">
              {submitting ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {submitting ? 'Starting…' : 'Start Node'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Summary chips ─────────────────────────────────────────────────────────────

function SummaryChips({ nodes }) {
  const counts = nodes.reduce((acc, n) => {
    const s = n.status || 'stopped';
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});

  const chips = [
    { key: 'running',  label: 'Running',  color: 'text-green-700 bg-green-100' },
    { key: 'starting', label: 'Starting', color: 'text-yellow-700 bg-yellow-100' },
    { key: 'stopping', label: 'Stopping', color: 'text-orange-700 bg-orange-100' },
    { key: 'stopped',  label: 'Stopped',  color: 'text-gray-600 bg-gray-100' },
    { key: 'failed',   label: 'Failed',   color: 'text-red-700 bg-red-100' },
  ].filter(c => counts[c.key]);

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {chips.map(c => (
        <span key={c.key} className={`px-3 py-1 rounded-full text-xs font-semibold ${c.color}`}>
          {counts[c.key]} {c.label}
        </span>
      ))}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Nodes() {
  const navigate = useNavigate();
  const { liveUpdates, workspaceFilter } = useWorkspace();
  const [nodes, setNodes]       = useState([]);
  const [agents, setAgents]     = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading]   = useState(true);
  // agentId -> capacity override for current workspace filter
  const [wsCapacityOverrides, setWsCapacityOverrides] = useState({});

  const [showStart, setShowStart]     = useState(false);
  const [startAgentId, setStartAgentId] = useState('');
  const [logsNode, setLogsNode]       = useState(null);
  const [busyNodes, setBusyNodes]     = useState({});

  const fetchNodes = useCallback(async () => {
    try {
      const r = await getNodes(workspaceFilter);
      setNodes(r.data);
    } catch (err) {
      console.error('Failed to load nodes', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter]);

  useEffect(() => {
    Promise.all([getAgents(), getWorkspaces()])
      .then(([ar, wr]) => { setAgents(ar.data); setWorkspaces(wr.data); })
      .catch(() => {});
  }, []);

  const [wsAllowedAgents, setWsAllowedAgents] = useState([]);
  // null = no workspace override (global setting applies), 'local' | 'docker' = workspace-specific
  const [wsAgentMode, setWsAgentMode] = useState(null);
  const [globalAgentMode, setGlobalAgentMode] = useState('local');

  // Load global agent mode once on mount
  useEffect(() => {
    getSettings().then(r => setGlobalAgentMode(r.data?.agent_mode || 'local')).catch(() => {});
  }, []);

  // Load capacity overrides, allowed agents, and agent mode for the current workspace
  useEffect(() => {
    const ws = workspaceFilter;
    if (!ws || ws === 'default') {
      setWsCapacityOverrides({});
      setWsAllowedAgents([]);
      setWsAgentMode(null);
      return;
    }
    getWorkspace(ws)
      .then(r => {
        setWsCapacityOverrides(r.data?.metadata?.agent_capacity_overrides || {});
        setWsAllowedAgents(r.data?.metadata?.allowed_agents || []);
        setWsAgentMode(r.data?.metadata?.settings?.agent_mode || null);
      })
      .catch(() => { setWsCapacityOverrides({}); setWsAllowedAgents([]); setWsAgentMode(null); });
  }, [workspaceFilter]);

  const handleSetWsAgentMode = async (mode) => {
    if (!workspaceFilter || workspaceFilter === 'default') return;
    try {
      await setWorkspaceAgentMode(workspaceFilter, mode);
      setWsAgentMode(mode);
    } catch (e) {
      console.error('Failed to set workspace agent mode', e);
    }
  };

  useEffect(() => {
    fetchNodes();
    if (!liveUpdates) return;
    const id = setInterval(fetchNodes, 4000);
    return () => clearInterval(id);
  }, [fetchNodes, liveUpdates]);

  const handleStop = async (nodeId) => {
    setBusyNodes(b => ({ ...b, [nodeId]: 'stopping' }));
    try { await stopNode(nodeId); await fetchNodes(); }
    catch (e) { console.error(e); }
    finally { setBusyNodes(b => { const n = { ...b }; delete n[nodeId]; return n; }); }
  };

  const handleDelete = async (nodeId) => {
    if (!window.confirm('Remove this node record?')) return;
    setBusyNodes(b => ({ ...b, [nodeId]: 'deleting' }));
    try { await deleteNode(nodeId); await fetchNodes(); }
    catch (e) { alert(e.response?.data?.detail || 'Cannot delete'); }
    finally { setBusyNodes(b => { const n = { ...b }; delete n[nodeId]; return n; }); }
  };

  const handleStart = async (node) => {
    // Capacity check before starting
    const ws = node.workspace;
    if (ws && ws !== 'default') {
      const capacity = wsCapacityOverrides[node.agent_id] ?? 1;
      const running = nodes.filter(n => n.agent_id === node.agent_id && n.workspace === ws && (n.status === 'running' || n.status === 'starting')).length;
      if (running >= capacity) {
        alert(`Cannot start: node limit (${capacity}) reached for this agent in workspace "${ws}". Stop a running node first.`);
        return;
      }
    }
    setBusyNodes(b => ({ ...b, [node.node_id]: 'starting' }));
    try {
      await startNode({ agent_id: node.agent_id, workspace: node.workspace || null, label: node.label || null });
      await fetchNodes();
    } catch (e) { alert(e.response?.data?.detail || 'Cannot start'); }
    finally { setBusyNodes(b => { const n = { ...b }; delete n[node.node_id]; return n; }); }
  };

  const handleRestart = async (node) => {
    setBusyNodes(b => ({ ...b, [node.node_id]: 'restarting' }));
    try {
      if ((node.execution_mode || 'local') === 'docker' && node.container_name) {
        // Docker mode: restart the existing container in place — no new record created.
        await restartNode(node.node_id);
      } else {
        // Local mode: stop the process and start a fresh subprocess.
        await stopNode(node.node_id);
        await startNode({ agent_id: node.agent_id, workspace: node.workspace || null, label: node.label || null });
        for (let i = 0; i < 12; i++) {
          try {
            await deleteNode(node.node_id);
            break;
          } catch (_) {
            await new Promise((r) => setTimeout(r, 500));
          }
        }
      }
      await fetchNodes();
    } catch (e) { alert(e.response?.data?.detail || 'Cannot restart'); }
    finally { setBusyNodes(b => { const n = { ...b }; delete n[node.node_id]; return n; }); }
  };

  const openStart = (agentId = '') => { setStartAgentId(agentId); setShowStart(true); };

  // Group nodes by agent for the table header groupings
  const grouped = nodes.reduce((acc, n) => {
    const key = n.agent_id;
    if (!acc[key]) acc[key] = { name: n.agent_name || n.agent_id, domain: n.agent_domain || '', nodes: [] };
    acc[key].nodes.push(n);
    return acc;
  }, {});

  const runningCount = nodes.filter(n => n.status === 'running' || n.status === 'starting').length;

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Server className="w-6 h-6 text-indigo-600" />
            Nodes
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Running agent instances · {runningCount} active
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button onClick={fetchNodes}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button onClick={() => openStart()}
            className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
            <Plus className="w-4 h-4" />
            Start Node
          </button>
        </div>
      </div>

      {/* Summary chips */}
      {nodes.length > 0 && <SummaryChips nodes={nodes} />}

      {/* Content */}
      {loading ? (
        <div className="flex justify-center py-20 bg-white rounded-xl border border-gray-200">
          <Loader className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
      ) : nodes.length === 0 ? (
        <div className="bg-white rounded-xl border border-dashed border-gray-300 py-20 text-center">
          <Server className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm mb-4">No nodes running yet.</p>
          <button onClick={() => openStart()}
            className="text-sm text-indigo-600 hover:underline">
            Start the first node
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([agentId, group]) => {
            const runningInGroup = group.nodes.filter(n => n.status === 'running' || n.status === 'starting').length;
            const isDefaultWs = !workspaceFilter || workspaceFilter === 'default';
            const groupCapacity = isDefaultWs ? Infinity : (wsCapacityOverrides[agentId] ?? 1);
            const groupAtCapacity = runningInGroup >= groupCapacity;
            return (
              <div key={agentId} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                {/* Agent group header */}
                <div className="flex items-center justify-between px-5 py-3 bg-gray-50 border-b border-gray-200">
                  <div className="flex items-center gap-3">
                    <span className="font-semibold text-gray-800 text-sm">{group.name}</span>
                    {group.domain && (
                      <span className="text-[10px] font-bold uppercase tracking-wide text-gray-400 bg-gray-200 px-2 py-0.5 rounded">
                        {group.domain}
                      </span>
                    )}
                    <span className="text-xs text-gray-400">{agentId}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                      groupAtCapacity ? 'bg-orange-100 text-orange-700' : runningInGroup > 0 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                    }`}>
                      {runningInGroup}{groupCapacity !== Infinity ? `/${groupCapacity}` : ''} running
                    </span>
                    {groupAtCapacity && (
                      <span className="text-xs text-orange-600 font-medium">Limit reached</span>
                    )}
                    <button
                      onClick={() => openStart(agentId)}
                      disabled={groupAtCapacity}
                      className="flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-800 border border-indigo-200 hover:border-indigo-400 px-2.5 py-1 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:border-indigo-200 disabled:hover:text-indigo-600"
                    >
                      <Plus className="w-3 h-3" />
                      Add node
                    </button>
                  </div>
                </div>

                {/* Nodes table */}
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Node ID</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Mode</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Label</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Workspace</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Sessions</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Started</th>
                      <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Uptime</th>
                      <th className="text-right px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {[...group.nodes].sort((a, b) => {
                      const order = { running: 0, starting: 1, stopping: 2, failed: 3, completed: 4, stopped: 5 };
                      return (order[a.status] ?? 6) - (order[b.status] ?? 6);
                    }).map(node => {
                      const isActive = node.status === 'running' || node.status === 'starting';
                      const busy = busyNodes[node.node_id];
                      return (
                        <tr
                          key={node.node_id}
                          className="hover:bg-gray-50 transition-colors cursor-pointer"
                          onClick={() => navigate(`/nodes/${node.node_id}`)}
                        >
                          <td className="px-5 py-3">
                            <StatusBadge status={node.status} />
                          </td>
                          <td className="px-5 py-3">
                            <span className=" text-xs text-gray-600">
                              {node.node_id.slice(0, 8)}
                              <span className="text-gray-400">…</span>
                            </span>
                            {node.is_default && (
                              <span className="ml-2 text-[9px] font-bold uppercase bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded">
                                default
                              </span>
                            )}
                            {node.is_exposed && (
                              <span className="ml-1.5 inline-flex items-center gap-0.5 text-[9px] font-bold uppercase bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
                                <Globe className="w-2.5 h-2.5" />
                                exposed
                              </span>
                            )}
                          </td>
                          <td className="px-5 py-3">
                            <div className="flex flex-col gap-1">
                              {node.execution_mode === 'docker'
                                ? (
                                  <span className="inline-flex items-center gap-1 text-[10px] font-semibold bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 rounded-full">
                                    <Box className="w-3 h-3" />
                                    Container
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center gap-1 text-[10px] font-semibold bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                                    Local
                                  </span>
                                )
                              }
                              {(node.node_type || 'worker') === 'service' && (
                                <span className="inline-flex items-center gap-1 text-[10px] font-semibold bg-violet-50 text-violet-700 border border-violet-200 px-2 py-0.5 rounded-full">
                                  HTTP Service
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-5 py-3 text-gray-600 text-xs">{node.label || '—'}</td>
                          <td className="px-5 py-3">
                            {node.workspace
                              ? <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{node.workspace}</span>
                              : <span className="text-gray-400 text-xs">—</span>
                            }
                          </td>
                          <td className="px-5 py-3 text-gray-700 text-xs">
                            <span className="inline-flex items-center px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 font-semibold">
                              {node.running_sessions_count ?? 0}
                            </span>
                          </td>
                          <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{fmtDate(node.started_at)}</td>
                          <td className="px-5 py-3 text-gray-600 text-xs whitespace-nowrap">
                            {uptime(node.started_at, node.finished_at)}
                          </td>
                          <td className="px-5 py-3">
                            <div className="flex items-center justify-end gap-1.5" onClick={e => e.stopPropagation()}>
                              {/* Open detail */}
                              <button
                                onClick={() => navigate(`/nodes/${node.node_id}`)}
                                title="Open node detail"
                                className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                              >
                                <ExternalLink className="w-4 h-4" />
                              </button>

                              {/* Logs */}
                              <button
                                onClick={() => setLogsNode(node)}
                                title="View logs"
                                className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                              >
                                <FileText className="w-4 h-4" />
                              </button>

                              {/* Restart (active nodes) */}
                              {isActive && (
                                <button
                                  onClick={() => handleRestart(node)}
                                  disabled={!!busy}
                                  title="Restart node"
                                  className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors disabled:opacity-40"
                                >
                                  {busy === 'restarting' ? <Loader className="w-4 h-4 animate-spin" /> : <RotateCw className="w-4 h-4" />}
                                </button>
                              )}

                              {/* Stop (active nodes) */}
                              {isActive && (
                                <button
                                  onClick={() => handleStop(node.node_id)}
                                  disabled={!!busy}
                                  title="Stop node"
                                  className="p-1.5 rounded text-gray-400 hover:text-orange-600 hover:bg-orange-50 transition-colors disabled:opacity-40"
                                >
                                  {busy === 'stopping' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                                </button>
                              )}

                              {/* Start (stopped/failed) */}
                              {!isActive && (
                                <button
                                  onClick={() => handleStart(node)}
                                  disabled={!!busy || groupAtCapacity}
                                  title={groupAtCapacity ? `Node limit (${groupCapacity}) reached` : 'Start new node with same config'}
                                  className="p-1.5 rounded text-gray-400 hover:text-green-600 hover:bg-green-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                  {busy === 'starting' ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                                </button>
                              )}

                              {/* Delete (stopped/failed) */}
                              {!isActive && (
                                <button
                                  onClick={() => handleDelete(node.node_id)}
                                  disabled={!!busy}
                                  title="Remove record"
                                  className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                                >
                                  {busy === 'deleting' ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}

      {/* Modals */}
      {showStart && (
        <StartNodeModal
          onClose={() => setShowStart(false)}
          onStarted={() => { setShowStart(false); fetchNodes(); }}
          agents={agents}
          defaultAgentId={startAgentId}
          nodes={nodes}
          currentWorkspace={workspaceFilter && workspaceFilter !== 'default' ? workspaceFilter : ''}
          wsCapacityOverrides={wsCapacityOverrides}
          wsAllowedAgents={wsAllowedAgents}
          agentMode={wsAgentMode || globalAgentMode}
          wsAgentMode={wsAgentMode}
          onSetAgentMode={workspaceFilter && workspaceFilter !== 'default' ? handleSetWsAgentMode : null}
        />
      )}
      {logsNode && (
        <LogsModal node={logsNode} onClose={() => setLogsNode(null)} />
      )}
    </div>
  );
}
