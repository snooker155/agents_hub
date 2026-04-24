import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  getMessages,
  stopMessage,
  deleteMessage,
  getAgents,
  getWorkspaces,
} from '../api';
import {
  Square,
  RefreshCw,
  X,
  CheckCircle,
  XCircle,
  Clock,
  AlertCircle,
  Loader,
  Trash2,
  Workflow,
  Globe,
} from 'lucide-react';

// ---- helpers ----------------------------------------------------------------

const STATUS_STYLES = {
  running:   { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Loader },
  completed: { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  failed:    { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  error:     { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  stopped:   { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Square },
  stop:      { bg: 'bg-orange-100', text: 'text-orange-700', icon: Square },
};

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'running' ? 'animate-spin' : ''}`} />
      {status}
    </span>
  );
}

function duration(started, finished) {
  if (!started) return '—';
  const end = finished ? new Date(finished) : new Date();
  const secs = Math.max(0, Math.round((end - new Date(started)) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

// ---- Main page --------------------------------------------------------------

export default function Messages() {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const [messages, setMessages]       = useState([]);
  const [agents, setAgents]           = useState([]);
  const [workspaces, setWorkspaces]   = useState([]);
  const [loading, setLoading]         = useState(true);

  const [filterWorkspace, setFilterWorkspace] = useState(workspaceFilter || '');
  const [filterAgent,     setFilterAgent]     = useState('');
  const [filterStatus,    setFilterStatus]    = useState('');
  const [filterFlow,      setFilterFlow]      = useState('');
  const [filterFrom,      setFilterFrom]      = useState('');
  const [filterTo,        setFilterTo]        = useState('');

  const [stopping,      setStopping]      = useState({});
  const [deleting,      setDeleting]      = useState({});
  const [selectedIds,   setSelectedIds]   = useState({});
  const [bulkDeleting,  setBulkDeleting]  = useState(false);
  const selectAllRef = useRef(null);

  const effectiveWorkspace = filterWorkspace || '';

  const fetchMessages = useCallback(async () => {
    try {
      const params = {};
      if (effectiveWorkspace) params.workspace = effectiveWorkspace;
      if (filterAgent)  params.agent_id = filterAgent;
      if (filterStatus) params.status   = filterStatus;
      if (filterFrom)   params.from_date = filterFrom;
      if (filterTo)     params.to_date   = filterTo;
      if (filterFlow === 'true')  params.is_flow = true;
      if (filterFlow === 'false') params.is_flow = false;
      const res = await getMessages(params);
      setMessages(res.data);
    } catch (err) {
      console.error('Failed to load messages', err);
    } finally {
      setLoading(false);
    }
  }, [effectiveWorkspace, filterAgent, filterStatus, filterFrom, filterTo, filterFlow]);

  useEffect(() => {
    setFilterWorkspace(workspaceFilter || '');
  }, [workspaceFilter]);

  useEffect(() => {
    getWorkspaces().then(r => setWorkspaces(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    getAgents(effectiveWorkspace || undefined).then(r => setAgents(r.data)).catch(() => {});
    setFilterAgent('');
  }, [effectiveWorkspace]);

  useEffect(() => {
    setLoading(true);
    fetchMessages();
    if (!liveUpdates) return;
    const id = setInterval(fetchMessages, 5000);
    return () => clearInterval(id);
  }, [fetchMessages, liveUpdates]);

  useEffect(() => {
    const visible = new Set(messages.map(m => m.run_id));
    setSelectedIds(prev => {
      const next = {};
      Object.entries(prev).forEach(([id, checked]) => {
        if (checked && visible.has(id)) next[id] = true;
      });
      return next;
    });
  }, [messages]);

  const handleStop = async (runId) => {
    setStopping(s => ({ ...s, [runId]: true }));
    try {
      await stopMessage(runId);
      await fetchMessages();
    } catch (err) {
      console.error('Failed to stop message', err);
    } finally {
      setStopping(s => ({ ...s, [runId]: false }));
    }
  };

  const handleDelete = async (msg) => {
    if (!msg?.run_id) return;
    if (!window.confirm(`Delete message ${msg.run_id}?`)) return;
    setDeleting(s => ({ ...s, [msg.run_id]: true }));
    try {
      await deleteMessage(msg.run_id, { delete_log: true });
      setSelectedIds(prev => { const next = { ...prev }; delete next[msg.run_id]; return next; });
      await fetchMessages();
    } catch (err) {
      window.alert(err?.response?.data?.detail || 'Failed to delete message');
    } finally {
      setDeleting(s => ({ ...s, [msg.run_id]: false }));
    }
  };

  const checkedIds = messages.filter(m => selectedIds[m.run_id]).map(m => m.run_id);
  const allSelected = messages.length > 0 && checkedIds.length === messages.length;
  const someSelected = checkedIds.length > 0 && !allSelected;

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected;
  }, [someSelected]);

  const toggleSelectAll = () => {
    if (allSelected) { setSelectedIds({}); return; }
    const next = {};
    messages.forEach(m => { next[m.run_id] = true; });
    setSelectedIds(next);
  };

  const toggleSelectRow = (runId) => {
    setSelectedIds(prev => ({ ...prev, [runId]: !prev[runId] }));
  };

  const handleBulkDelete = async () => {
    if (!checkedIds.length) return;
    if (!window.confirm(`Delete ${checkedIds.length} selected message(s)?`)) return;
    setBulkDeleting(true);
    try {
      const results = await Promise.all(
        checkedIds.map(async (runId) => {
          try {
            await deleteMessage(runId, { delete_log: true });
            return { runId, ok: true };
          } catch (err) {
            return { runId, ok: false, msg: err?.response?.data?.detail || 'Failed' };
          }
        })
      );
      const failed = results.filter(r => !r.ok);
      setSelectedIds({});
      await fetchMessages();
      if (failed.length) {
        window.alert(`Some deletions failed (${failed.length}). Example: ${failed[0].runId} (${failed[0].msg})`);
      }
    } finally {
      setBulkDeleting(false);
    }
  };

  const allStatuses = ['running', 'completed', 'failed', 'error', 'stopped', 'stop'];
  const hasFilters = filterAgent || filterStatus || filterFrom || filterTo || filterWorkspace || filterFlow;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Messages</h1>
          <p className="text-sm text-gray-500 mt-1">Individual agent run logs across all workspaces</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleBulkDelete}
            disabled={!checkedIds.length || bulkDeleting}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
            title={checkedIds.length ? `Delete ${checkedIds.length} selected` : 'Select rows to delete'}
          >
            {bulkDeleting ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            Delete Selected
          </button>
          <button
            onClick={fetchMessages}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">Workspace</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterWorkspace}
              onChange={e => setFilterWorkspace(e.target.value)}
            >
              <option value="">All workspaces</option>
              {selectedWorkspace && (
                <option value={selectedWorkspace}>Current: {selectedWorkspace}</option>
              )}
              {workspaces.map(ws => (
                <option key={ws.name} value={ws.name}>{ws.name}</option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">Agent</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterAgent}
              onChange={e => setFilterAgent(e.target.value)}
            >
              <option value="">All agents</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">Status</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              {allStatuses.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">Type</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFlow}
              onChange={e => setFilterFlow(e.target.value)}
            >
              <option value="">All types</option>
              <option value="true">Flow</option>
              <option value="false">Standalone</option>
            </select>
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">From</label>
            <input
              type="datetime-local"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFrom}
              onChange={e => setFilterFrom(e.target.value ? new Date(e.target.value).toISOString() : '')}
            />
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">To</label>
            <input
              type="datetime-local"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterTo}
              onChange={e => setFilterTo(e.target.value ? new Date(e.target.value).toISOString() : '')}
            />
          </div>

          {hasFilters && (
            <button
              onClick={() => {
                setFilterWorkspace('');
                setFilterAgent('');
                setFilterStatus('');
                setFilterFlow('');
                setFilterFrom('');
                setFilterTo('');
              }}
              className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              <X className="w-4 h-4" />
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Messages list */}
      <div className="space-y-4">
        {loading ? (
          <div className="bg-white rounded-xl border border-gray-200 flex justify-center py-16">
            <Loader className="w-6 h-6 animate-spin text-indigo-500" />
          </div>
        ) : messages.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
            <Clock className="w-10 h-10 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500 text-sm">No messages found.</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="hidden md:grid grid-cols-[36px_minmax(0,2.3fr)_1fr_1.1fr_1fr_0.7fr_0.6fr_140px] gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200">
              <div className="flex items-center justify-center">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
              </div>
              <div>Message</div>
              <div className="text-center">Status</div>
              <div className="text-center">Agent</div>
              <div className="text-center">Workspace</div>
              <div className="text-center">Duration</div>
              <div className="text-center">Type</div>
              <div className="text-right">Actions</div>
            </div>
            <div className="divide-y divide-gray-100">
              {messages.map(msg => (
                <div
                  key={msg.run_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/messages/${msg.run_id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/messages/${msg.run_id}`); }
                  }}
                  className="grid grid-cols-1 md:grid-cols-[36px_minmax(0,2.3fr)_1fr_1.1fr_1fr_0.7fr_0.6fr_140px] gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset"
                >
                  <div className="hidden md:flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={!!selectedIds[msg.run_id]}
                      onChange={() => toggleSelectRow(msg.run_id)}
                      onClick={e => e.stopPropagation()}
                      className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    />
                  </div>

                  <div className="min-w-0">
                    <div className="md:hidden mb-1">
                      <input
                        type="checkbox"
                        checked={!!selectedIds[msg.run_id]}
                        onChange={() => toggleSelectRow(msg.run_id)}
                        onClick={e => e.stopPropagation()}
                        className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                      />
                    </div>
                    <div className="font-medium text-gray-900 truncate">
                      {msg.task_title || <span className="text-gray-400 italic">No title</span>}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 truncate">{msg.run_id}</div>
                    {msg.session_id && (
                      <div className="text-xs text-indigo-500 mt-0.5 truncate" title={`Session: ${msg.session_id}`}>
                        session: {msg.session_id.slice(0, 8)}…
                      </div>
                    )}
                  </div>

                  <div className="md:self-center md:flex md:justify-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Status</div>
                    <StatusBadge status={msg.status} />
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Agent</div>
                    <span className="text-xs">{msg.agent_id || '—'}</span>
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Workspace</div>
                    {msg.workspace || '—'}
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Duration</div>
                    {duration(msg.started_at, msg.finished_at)}
                  </div>

                  <div className="md:self-center md:flex md:flex-wrap md:justify-center gap-1">
                    {msg.is_flow && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-100 text-violet-700">
                        <Workflow className="w-3 h-3" />
                        Flow
                      </span>
                    )}
                    {msg.session_type === 'http' && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-100 text-cyan-700">
                        <Globe className="w-3 h-3" />
                        External
                      </span>
                    )}
                    {!msg.is_flow && msg.session_type !== 'http' && (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </div>

                  <div className="flex md:justify-end md:self-center">
                    <div className="inline-flex items-center gap-2">
                      {msg.status === 'running' && (
                        <button
                          onClick={e => { e.stopPropagation(); handleStop(msg.run_id); }}
                          disabled={stopping[msg.run_id]}
                          title="Stop"
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                        >
                          {stopping[msg.run_id]
                            ? <Loader className="w-3.5 h-3.5 animate-spin" />
                            : <Square className="w-3.5 h-3.5" />}
                          Stop
                        </button>
                      )}
                      <button
                        onClick={e => { e.stopPropagation(); handleDelete(msg); }}
                        disabled={deleting[msg.run_id] || msg.status === 'running'}
                        title={msg.status === 'running' ? 'Stop before deleting' : 'Delete'}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      >
                        {deleting[msg.run_id]
                          ? <Loader className="w-3.5 h-3.5 animate-spin" />
                          : <Trash2 className="w-3.5 h-3.5" />}
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {!loading && messages.length > 0 && (
        <p className="text-xs text-gray-400 text-right">
          {messages.length} message{messages.length !== 1 ? 's' : ''} shown
        </p>
      )}
    </div>
  );
}
