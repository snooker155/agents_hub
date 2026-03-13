import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  getSessions,
  createSession,
  stopSession,
  deleteSession,
  getAgents,
  getWorkspaces,
  getSessionInsights,
} from '../api';
import {
  Play,
  Square,
  RefreshCw,
  Plus,
  X,
  CheckCircle,
  XCircle,
  Clock,
  AlertCircle,
  Loader,
  Trash2,
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

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

function fmtPct(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0.0%';
  return `${n.toFixed(1)}%`;
}

// ---- New Session modal ------------------------------------------------------

function NewSessionModal({ onClose, onCreated, workspaces, agents, defaultWorkspace }) {
  const [form, setForm] = useState({
    title: '',
    description: '',
    agent_id: '',
    workspace: defaultWorkspace || '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.title.trim()) { setError('Title is required'); return; }
    if (!form.agent_id) { setError('Select an agent'); return; }
    setError('');
    setSubmitting(true);
    try {
      const res = await createSession({
        title: form.title.trim(),
        description: form.description.trim(),
        agent_id: form.agent_id,
        workspace: form.workspace || null,
      });
      onCreated(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to start session');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold text-gray-800">New Session</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Agent <span className="text-red-500">*</span></label>
            <select
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={form.agent_id}
              onChange={e => set('agent_id', e.target.value)}
            >
              <option value="">— Select agent —</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name} ({a.domain || a.type})</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Task Title <span className="text-red-500">*</span></label>
            <input
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="What should the agent do?"
              value={form.title}
              onChange={e => set('title', e.target.value)}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              rows={3}
              placeholder="Optional detailed description..."
              value={form.description}
              onChange={e => set('description', e.target.value)}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Workspace</label>
            <select
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={form.workspace}
              onChange={e => set('workspace', e.target.value)}
            >
              <option value="">— None / auto —</option>
              {workspaces.map(ws => (
                <option key={ws.name} value={ws.name}>{ws.name}</option>
              ))}
            </select>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {submitting ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {submitting ? 'Starting…' : 'Start Session'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---- Main page --------------------------------------------------------------

export default function Sessions() {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const [sessions, setSessions]       = useState([]);
  const [agents, setAgents]           = useState([]);
  const [workspaces, setWorkspaces]   = useState([]);
  const [loading, setLoading]         = useState(true);
  const [sessionContextById, setSessionContextById] = useState({});

  // local filters — initialized from global workspace context
  const [filterWorkspace, setFilterWorkspace] = useState(workspaceFilter || '');
  const [filterAgent,     setFilterAgent]     = useState('');
  const [filterStatus,    setFilterStatus]    = useState('');
  const [filterFrom,      setFilterFrom]      = useState('');
  const [filterTo,        setFilterTo]        = useState('');

  const [showNew,    setShowNew]    = useState(false);
  const [stopping,  setStopping]   = useState({});
  const [deleting, setDeleting] = useState({});
  const [selectedRunIds, setSelectedRunIds] = useState({});
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const selectAllRef = useRef(null);

  // Derive the effective workspace filter
  const effectiveWorkspace = filterWorkspace || '';

  const fetchSessions = useCallback(async () => {
    try {
      const params = {};
      if (effectiveWorkspace) params.workspace = effectiveWorkspace;
      if (filterAgent)  params.agent_id = filterAgent;
      if (filterStatus) params.status   = filterStatus;
      if (filterFrom)   params.from_date = filterFrom;
      if (filterTo)     params.to_date   = filterTo;
      const res = await getSessions(params);
      setSessions(res.data);
    } catch (err) {
      console.error('Failed to load sessions', err);
    } finally {
      setLoading(false);
    }
  }, [effectiveWorkspace, filterAgent, filterStatus, filterFrom, filterTo]);

  // Sync local filter when global workspace changes
  useEffect(() => {
    setFilterWorkspace(workspaceFilter || '');
  }, [workspaceFilter]);

  // Initial data load (agents + workspaces)
  useEffect(() => {
    getAgents().then(r => setAgents(r.data)).catch(() => {});
    getWorkspaces().then(r => setWorkspaces(r.data)).catch(() => {});
  }, []);

  // Poll sessions
  useEffect(() => {
    setLoading(true);
    fetchSessions();
    if (!liveUpdates) return;
    const id = setInterval(fetchSessions, 5000);
    return () => clearInterval(id);
  }, [fetchSessions, liveUpdates]);

  useEffect(() => {
    let cancelled = false;

    const loadContextUsage = async () => {
      if (!sessions.length) {
        setSessionContextById({});
        return;
      }

      const requests = sessions
        .filter(s => s?.run_id)
        .map(async (s) => {
          try {
            const res = await getSessionInsights(s.run_id);
            return [s.run_id, res.data?.context_window || null];
          } catch {
            return [s.run_id, null];
          }
        });

      const entries = await Promise.all(requests);
      if (cancelled) return;

      setSessionContextById(Object.fromEntries(entries));
    };

    loadContextUsage();
    return () => { cancelled = true; };
  }, [sessions]);

  useEffect(() => {
    // Keep selection only for currently listed rows.
    const visible = new Set(sessions.map((s) => s.run_id));
    setSelectedRunIds((prev) => {
      const next = {};
      Object.entries(prev).forEach(([id, checked]) => {
        if (checked && visible.has(id)) next[id] = true;
      });
      return next;
    });
  }, [sessions]);

  const handleStop = async (runId) => {
    setStopping(s => ({ ...s, [runId]: true }));
    try {
      await stopSession(runId);
      await fetchSessions();
    } catch (err) {
      console.error('Failed to stop session', err);
    } finally {
      setStopping(s => ({ ...s, [runId]: false }));
    }
  };

  const handleCreated = () => {
    setShowNew(false);
    fetchSessions();
  };

  const handleDelete = async (session) => {
    if (!session?.run_id) return;
    if (!window.confirm(`Delete session ${session.run_id}?`)) return;

    setDeleting(s => ({ ...s, [session.run_id]: true }));
    try {
      await deleteSession(session.run_id, { delete_log: true });
      setSessionContextById((prev) => {
        const next = { ...prev };
        delete next[session.run_id];
        return next;
      });
      setSelectedRunIds((prev) => {
        const next = { ...prev };
        delete next[session.run_id];
        return next;
      });
      await fetchSessions();
    } catch (err) {
      const msg = err?.response?.data?.detail || 'Failed to delete session';
      window.alert(msg);
    } finally {
      setDeleting(s => ({ ...s, [session.run_id]: false }));
    }
  };

  const selectedIds = sessions.filter((s) => selectedRunIds[s.run_id]).map((s) => s.run_id);
  const allSelected = sessions.length > 0 && selectedIds.length === sessions.length;
  const someSelected = selectedIds.length > 0 && !allSelected;

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someSelected;
    }
  }, [someSelected]);

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedRunIds({});
      return;
    }
    const next = {};
    sessions.forEach((s) => {
      next[s.run_id] = true;
    });
    setSelectedRunIds(next);
  };

  const toggleSelectRow = (runId) => {
    setSelectedRunIds((prev) => ({
      ...prev,
      [runId]: !prev[runId],
    }));
  };

  const handleBulkDelete = async () => {
    if (!selectedIds.length) return;
    if (!window.confirm(`Delete ${selectedIds.length} selected session(s)?`)) return;

    setBulkDeleting(true);
    try {
      const results = await Promise.all(
        selectedIds.map(async (runId) => {
          try {
            await deleteSession(runId, { delete_log: true });
            return { runId, ok: true };
          } catch (err) {
            return { runId, ok: false, msg: err?.response?.data?.detail || 'Failed to delete' };
          }
        })
      );

      const deletedIds = results.filter((r) => r.ok).map((r) => r.runId);
      const failed = results.filter((r) => !r.ok);

      if (deletedIds.length) {
        setSessionContextById((prev) => {
          const next = { ...prev };
          deletedIds.forEach((id) => delete next[id]);
          return next;
        });
      }

      setSelectedRunIds({});
      await fetchSessions();

      if (failed.length) {
        window.alert(`Deleted ${deletedIds.length}, failed ${failed.length}. Example: ${failed[0].runId} (${failed[0].msg})`);
      }
    } finally {
      setBulkDeleting(false);
    }
  };

  const allStatuses = ['running', 'completed', 'failed', 'error', 'stopped', 'stop'];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Sessions</h1>
          <p className="text-sm text-gray-500 mt-1">Agent run sessions across all workspaces</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleBulkDelete}
            disabled={!selectedIds.length || bulkDeleting}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
            title={selectedIds.length ? `Delete ${selectedIds.length} selected` : 'Select rows to delete'}
          >
            {bulkDeleting ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            Delete Selected
          </button>
          <button
            onClick={fetchSessions}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            Refresh
          </button>
          <button
            onClick={() => setShowNew(true)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" />
            New Session
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          {/* Workspace filter */}
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

          {/* Agent filter */}
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

          {/* Status filter */}
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

          {/* From date */}
          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">From</label>
            <input
              type="datetime-local"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFrom}
              onChange={e => setFilterFrom(e.target.value ? new Date(e.target.value).toISOString() : '')}
            />
          </div>

          {/* To date */}
          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">To</label>
            <input
              type="datetime-local"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterTo}
              onChange={e => setFilterTo(e.target.value ? new Date(e.target.value).toISOString() : '')}
            />
          </div>

          {/* Clear filters */}
          {(filterAgent || filterStatus || filterFrom || filterTo || filterWorkspace) && (
            <button
              onClick={() => {
                setFilterWorkspace('');
                setFilterAgent('');
                setFilterStatus('');
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

      {/* Sessions list */}
      <div className="space-y-4">
        {loading ? (
          <div className="bg-white rounded-xl border border-gray-200 flex justify-center py-16">
            <Loader className="w-6 h-6 animate-spin text-indigo-500" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
            <Clock className="w-10 h-10 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500 text-sm">No sessions found.</p>
            <button
              onClick={() => setShowNew(true)}
              className="mt-4 text-sm text-indigo-600 hover:underline"
            >
              Start a new session
            </button>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="hidden md:grid grid-cols-[36px_minmax(0,2.1fr)_1fr_1.1fr_1fr_1.4fr_0.7fr_140px] gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200">
              <div className="flex items-center justify-center">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  aria-label="Select all sessions"
                />
              </div>
              <div>Session</div>
              <div className="text-center">Status</div>
              <div className="text-center">Agent</div>
              <div className="text-center">Workspace</div>
              <div className="text-center">Context</div>
              <div className="text-center">Duration</div>
              <div className="text-right">Actions</div>
            </div>
            <div className="divide-y divide-gray-100">
              {sessions.map(session => (
                <div
                  key={session.run_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/sessions/${session.run_id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      navigate(`/sessions/${session.run_id}`);
                    }
                  }}
                  className="grid grid-cols-1 md:grid-cols-[36px_minmax(0,2.1fr)_1fr_1.1fr_1fr_1.4fr_0.7fr_140px] gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset"
                >
                  <div className="hidden md:flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={!!selectedRunIds[session.run_id]}
                      onChange={() => toggleSelectRow(session.run_id)}
                      onClick={(e) => e.stopPropagation()}
                      className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                      aria-label={`Select session ${session.run_id}`}
                    />
                  </div>

                  <div className="min-w-0">
                    <div className="md:hidden mb-1">
                      <input
                        type="checkbox"
                        checked={!!selectedRunIds[session.run_id]}
                        onChange={() => toggleSelectRow(session.run_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                        aria-label={`Select session ${session.run_id}`}
                      />
                    </div>
                    <div className="font-medium text-gray-900 truncate">
                      {session.task_title || <span className="text-gray-400 italic">No title</span>}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 font-mono truncate">{session.run_id}</div>
                    <div className="text-xs text-gray-500 mt-1 md:hidden">
                      Started: {fmtDate(session.started_at)}
                    </div>
                  </div>

                  <div className="md:self-center md:flex md:justify-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Status</div>
                    <StatusBadge status={session.status} />
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Agent</div>
                    <span className="font-mono text-xs">{session.agent_id || '—'}</span>
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Workspace</div>
                    {session.workspace || '—'}
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Context</div>
                    {sessionContextById[session.run_id] ? (
                      <>
                        <div className="flex items-center justify-center gap-2 text-[10px] text-gray-500 mb-1">
                          <span className="truncate max-w-[55%]">
                            {sessionContextById[session.run_id]?.model || 'model'}
                          </span>
                          <span>
                            {fmtPct(sessionContextById[session.run_id]?.input_fulfillment_pct)}
                          </span>
                        </div>
                        <div className="w-full h-1.5 rounded bg-gray-100 overflow-hidden">
                          <div
                            className="h-full bg-indigo-500"
                            style={{
                              width: `${Math.min(100, Math.max(0, Number(sessionContextById[session.run_id]?.input_fulfillment_pct || 0))) || 0}%`,
                            }}
                          />
                        </div>
                        <div className="text-[10px] text-gray-500 mt-1">
                          left {sessionContextById[session.run_id]?.input_tokens_remaining || 0}
                        </div>
                      </>
                    ) : (
                      <div className="text-[10px] text-gray-400">—</div>
                    )}
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">Duration</div>
                    {duration(session.started_at, session.finished_at)}
                  </div>

                  <div className="flex md:justify-end md:self-center">
                    <div className="inline-flex items-center gap-2">
                      {session.status === 'running' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleStop(session.run_id);
                          }}
                          disabled={stopping[session.run_id]}
                          title="Stop session"
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                        >
                          {stopping[session.run_id]
                            ? <Loader className="w-3.5 h-3.5 animate-spin" />
                            : <Square className="w-3.5 h-3.5" />}
                          Stop
                        </button>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(session);
                        }}
                        disabled={deleting[session.run_id] || session.status === 'running'}
                        title={session.status === 'running' ? 'Stop session before deleting' : 'Delete session'}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      >
                        {deleting[session.run_id]
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

      {/* Summary */}
      {!loading && sessions.length > 0 && (
        <p className="text-xs text-gray-400 text-right">
          {sessions.length} session{sessions.length !== 1 ? 's' : ''} shown
        </p>
      )}

      {/* Modals */}
      {showNew && (
        <NewSessionModal
          onClose={() => setShowNew(false)}
          onCreated={handleCreated}
          workspaces={workspaces}
          agents={agents}
          defaultWorkspace={selectedWorkspace}
        />
      )}
    </div>
  );
}
