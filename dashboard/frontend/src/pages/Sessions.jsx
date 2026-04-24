import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  getSessions,
  stopSession,
  deleteSession,
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
  MessageSquare,
} from 'lucide-react';

// ---- helpers ----------------------------------------------------------------

const STATUS_STYLES = {
  running:   { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Loader },
  completed: { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  failed:    { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  error:     { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  stopped:   { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Square },
  stop:      { bg: 'bg-orange-100', text: 'text-orange-700', icon: Square },
  pending:   { bg: 'bg-gray-100',   text: 'text-gray-500',   icon: Clock },
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

export default function Sessions() {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const [sessions, setSessions]     = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading]       = useState(true);

  const [filterWorkspace, setFilterWorkspace] = useState(workspaceFilter || '');
  const [filterStatus,    setFilterStatus]    = useState('');
  const [filterFlow,      setFilterFlow]      = useState('');
  const [filterFrom,      setFilterFrom]      = useState('');
  const [filterTo,        setFilterTo]        = useState('');

  const [stopping,     setStopping]     = useState({});
  const [deleting,     setDeleting]     = useState({});
  const [selectedIds,  setSelectedIds]  = useState({});
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const selectAllRef = useRef(null);

  const effectiveWorkspace = filterWorkspace || '';

  const fetchSessions = useCallback(async () => {
    try {
      const params = {};
      if (effectiveWorkspace) params.workspace = effectiveWorkspace;
      if (filterStatus) params.status = filterStatus;
      if (filterFrom)   params.from_date = filterFrom;
      if (filterTo)     params.to_date = filterTo;
      if (filterFlow === 'true')  params.is_flow = true;
      if (filterFlow === 'false') params.is_flow = false;
      const res = await getSessions(params);
      setSessions(res.data);
    } catch (err) {
      console.error('Failed to load sessions', err);
    } finally {
      setLoading(false);
    }
  }, [effectiveWorkspace, filterStatus, filterFrom, filterTo, filterFlow]);

  useEffect(() => { setFilterWorkspace(workspaceFilter || ''); }, [workspaceFilter]);

  useEffect(() => {
    getWorkspaces().then(r => setWorkspaces(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchSessions();
    if (!liveUpdates) return;
    const id = setInterval(fetchSessions, 5000);
    return () => clearInterval(id);
  }, [fetchSessions, liveUpdates]);

  useEffect(() => {
    const visible = new Set(sessions.map(s => s.session_id));
    setSelectedIds(prev => {
      const next = {};
      Object.entries(prev).forEach(([id, checked]) => {
        if (checked && visible.has(id)) next[id] = true;
      });
      return next;
    });
  }, [sessions]);

  const handleStop = async (sessionId) => {
    setStopping(s => ({ ...s, [sessionId]: true }));
    try {
      await stopSession(sessionId);
      await fetchSessions();
    } catch (err) {
      console.error('Failed to stop session', err);
    } finally {
      setStopping(s => ({ ...s, [sessionId]: false }));
    }
  };

  const handleDelete = async (session) => {
    if (!session?.session_id) return;
    if (!window.confirm(`Delete session "${session.title || session.session_id}"?`)) return;
    setDeleting(s => ({ ...s, [session.session_id]: true }));
    try {
      await deleteSession(session.session_id);
      setSelectedIds(prev => { const next = { ...prev }; delete next[session.session_id]; return next; });
      await fetchSessions();
    } catch (err) {
      window.alert(err?.response?.data?.detail || 'Failed to delete session');
    } finally {
      setDeleting(s => ({ ...s, [session.session_id]: false }));
    }
  };

  const checkedIds = sessions.filter(s => selectedIds[s.session_id]).map(s => s.session_id);
  const allSelected = sessions.length > 0 && checkedIds.length === sessions.length;
  const someSelected = checkedIds.length > 0 && !allSelected;

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected;
  }, [someSelected]);

  const toggleSelectAll = () => {
    if (allSelected) { setSelectedIds({}); return; }
    const next = {};
    sessions.forEach(s => { next[s.session_id] = true; });
    setSelectedIds(next);
  };

  const handleBulkDelete = async () => {
    if (!checkedIds.length) return;
    if (!window.confirm(`Delete ${checkedIds.length} selected session(s)?`)) return;
    setBulkDeleting(true);
    try {
      const results = await Promise.all(
        checkedIds.map(async (sid) => {
          try {
            await deleteSession(sid);
            return { sid, ok: true };
          } catch (err) {
            return { sid, ok: false, msg: err?.response?.data?.detail || 'Failed' };
          }
        })
      );
      const failed = results.filter(r => !r.ok);
      setSelectedIds({});
      await fetchSessions();
      if (failed.length) {
        window.alert(`Some deletions failed (${failed.length}). Example: ${failed[0].sid} (${failed[0].msg})`);
      }
    } finally {
      setBulkDeleting(false);
    }
  };

  const allStatuses = ['running', 'completed', 'failed', 'stopped', 'pending', 'awaiting_approval'];
  const hasFilters = filterStatus || filterFrom || filterTo || filterWorkspace || filterFlow;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Sessions</h1>
          <p className="text-sm text-gray-500 mt-1">Process-level execution contexts grouping one or more agent runs</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleBulkDelete}
            disabled={!checkedIds.length || bulkDeleting}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
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
              {selectedWorkspace && <option value={selectedWorkspace}>Current: {selectedWorkspace}</option>}
              {workspaces.map(ws => <option key={ws.name} value={ws.name}>{ws.name}</option>)}
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
              {allStatuses.map(s => <option key={s} value={s}>{s}</option>)}
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
              onClick={() => { setFilterWorkspace(''); setFilterStatus(''); setFilterFlow(''); setFilterFrom(''); setFilterTo(''); }}
              className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              <X className="w-4 h-4" /> Clear
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
            <p className="text-gray-400 text-xs mt-1">Sessions are created when you start a new agent run.</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="hidden md:grid grid-cols-[36px_minmax(0,2.3fr)_1fr_1fr_0.7fr_0.7fr_0.6fr_140px] gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200">
              <div className="flex items-center justify-center">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
              </div>
              <div>Session</div>
              <div className="text-center">Status</div>
              <div className="text-center">Workspace</div>
              <div className="text-center">Messages</div>
              <div className="text-center">Duration</div>
              <div className="text-center">Type</div>
              <div className="text-right">Actions</div>
            </div>
            <div className="divide-y divide-gray-100">
              {sessions.map(session => (
                <div
                  key={session.session_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/sessions/${session.session_id}`)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/sessions/${session.session_id}`); } }}
                  className="grid grid-cols-1 md:grid-cols-[36px_minmax(0,2.3fr)_1fr_1fr_0.7fr_0.7fr_0.6fr_140px] gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset"
                >
                  <div className="hidden md:flex items-center justify-center">
                    <input
                      type="checkbox"
                      checked={!!selectedIds[session.session_id]}
                      onChange={() => setSelectedIds(prev => ({ ...prev, [session.session_id]: !prev[session.session_id] }))}
                      onClick={e => e.stopPropagation()}
                      className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    />
                  </div>

                  <div className="min-w-0">
                    <div className="font-medium text-gray-900 truncate">
                      {session.title || <span className="text-gray-400 italic">Untitled</span>}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 truncate">{session.session_id}</div>
                    {(session.agents || []).length > 0 && (
                      <div className="text-xs text-gray-400 mt-0.5 truncate">
                        {session.agents.join(', ')}
                      </div>
                    )}
                  </div>

                  <div className="md:self-center md:flex md:justify-center">
                    <StatusBadge status={session.status} />
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    {session.workspace || '—'}
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <span className="inline-flex items-center gap-1 text-xs text-gray-600">
                      <MessageSquare className="w-3 h-3" />
                      {session.message_count || 0}
                    </span>
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center text-xs">
                    {duration(session.created_at, session.finished_at)}
                  </div>

                  <div className="md:self-center md:flex md:justify-center">
                    {session.is_flow ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-100 text-violet-700">
                        <Workflow className="w-3 h-3" />
                        Flow
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </div>

                  <div className="flex md:justify-end md:self-center">
                    <div className="inline-flex items-center gap-2">
                      {session.status === 'running' && (
                        <button
                          onClick={e => { e.stopPropagation(); handleStop(session.session_id); }}
                          disabled={stopping[session.session_id]}
                          title="Stop all runs in session"
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                        >
                          {stopping[session.session_id]
                            ? <Loader className="w-3.5 h-3.5 animate-spin" />
                            : <Square className="w-3.5 h-3.5" />}
                          Stop
                        </button>
                      )}
                      <button
                        onClick={e => { e.stopPropagation(); handleDelete(session); }}
                        disabled={deleting[session.session_id] || session.status === 'running'}
                        title={session.status === 'running' ? 'Stop session before deleting' : 'Delete session'}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      >
                        {deleting[session.session_id]
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

      {!loading && sessions.length > 0 && (
        <p className="text-xs text-gray-400 text-right">
          {sessions.length} session{sessions.length !== 1 ? 's' : ''} shown
        </p>
      )}
    </div>
  );
}
