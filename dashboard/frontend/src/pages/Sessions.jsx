import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
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
  PlayCircle,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n, statusLabel } from '../i18n';
import DateInput from '../components/DateInput';
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
  const { t } = useI18n();
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'running' ? 'animate-spin' : ''}`} />
      {statusLabel(status, t)}
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

// One window of sessions per request — large enough for a single round-trip
// in the common case, small enough that the first paint never waits on the
// whole table.
const PAGE_SIZE = 200;

export default function Sessions() {
  const { t } = useI18n();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const [sessions, setSessions]     = useState([]);
  const [totalSessions, setTotalSessions] = useState(0);
  const [offset, setOffset]         = useState(0);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading]       = useState(true);

  const [filterWorkspace, setFilterWorkspace] = useState(selectedWorkspace || '');
  const [filterStatus,    setFilterStatus]    = useState('');
  const [filterFlow,      setFilterFlow]      = useState('');
  const [filterFrom,      setFilterFrom]      = useState('');
  const [filterTo,        setFilterTo]        = useState('');

  const [stopping,     setStopping]     = useState({});
  const [deleting,     setDeleting]     = useState({});
  const [selectedIds,  setSelectedIds]  = useState({});
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const selectAllRef = useRef(null);

  // The workspace filter is only offered in the default workspace. In a specific
  // workspace the page is locked to it and the dropdown is hidden.
  const isDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';
  const effectiveWorkspace = filterWorkspace || '';

  // The Workspace column is only shown in the default workspace. The grid
  // template literals are written out in full so Tailwind's JIT can detect them.
  const mdGridCols = isDefaultWorkspace
    ? 'md:grid-cols-[36px_minmax(0,2.3fr)_1fr_1fr_0.7fr_0.7fr_0.6fr_140px]'
    : 'md:grid-cols-[36px_minmax(0,2.3fr)_1fr_0.7fr_0.7fr_0.6fr_140px]';

  // The backend filters, orders and pages in SQL and answers {items, total, ...};
  // the page asks for one window and grows it on demand.
  const fetchSessions = useCallback(async (nextOffset = 0, append = false) => {
    try {
      const params = { limit: PAGE_SIZE, offset: nextOffset };
      if (effectiveWorkspace) params.workspace = effectiveWorkspace;
      if (filterStatus) params.status = filterStatus;
      if (filterFrom)   params.from_date = filterFrom;
      if (filterTo)     params.to_date = filterTo;
      if (filterFlow === 'true')  params.is_flow = true;
      if (filterFlow === 'false') params.is_flow = false;
      const res = await getSessions(params);
      const data = res.data || {};
      const items = Array.isArray(data) ? data : (data.items || []);
      setSessions(prev => (append ? [...prev, ...items] : items));
      setTotalSessions(Array.isArray(data) ? items.length : (data.total || 0));
      setOffset(nextOffset);
    } catch (err) {
      console.error('Failed to load sessions', err);
    } finally {
      setLoading(false);
    }
  }, [effectiveWorkspace, filterStatus, filterFrom, filterTo, filterFlow]);

  useEffect(() => { setFilterWorkspace(selectedWorkspace || ''); }, [selectedWorkspace]);

  useEffect(() => {
    getWorkspaces().then(r => setWorkspaces(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchSessions(0, false);
  }, [fetchSessions, liveUpdates]);
  useLiveRefetch(() => fetchSessions(0, false), { type: 'sessions.changed', enabled: liveUpdates });

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
    if (!window.confirm(t('sessions.confirmDelete', { name: session.title || session.session_id }))) return;
    setDeleting(s => ({ ...s, [session.session_id]: true }));
    try {
      await deleteSession(session.session_id);
      setSelectedIds(prev => { const next = { ...prev }; delete next[session.session_id]; return next; });
      await fetchSessions();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('sessions.deleteFailed'));
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
    if (!window.confirm(t('sessions.confirmBulkDelete', { count: checkedIds.length }))) return;
    setBulkDeleting(true);
    try {
      const results = await Promise.all(
        checkedIds.map(async (sid) => {
          try {
            await deleteSession(sid);
            return { sid, ok: true };
          } catch (err) {
            return { sid, ok: false, msg: err?.response?.data?.detail || t('common.failed') };
          }
        })
      );
      const failed = results.filter(r => !r.ok);
      setSelectedIds({});
      await fetchSessions();
      if (failed.length) {
        window.alert(t('sessions.bulkDeletePartial', { count: failed.length, id: failed[0].sid, reason: failed[0].msg }));
      }
    } finally {
      setBulkDeleting(false);
    }
  };

  const allStatuses = ['running', 'completed', 'failed', 'stopped', 'pending', 'awaiting_approval'];
  const hasFilters = filterStatus || filterFrom || filterTo || filterFlow || (isDefaultWorkspace && filterWorkspace);

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={PlayCircle}
        title={t('sessions.sessions')}
        description={t('sessions.processLevelExecutionContextsGrouping')}
        actions={<>
          <button
            onClick={handleBulkDelete}
            disabled={!checkedIds.length || bulkDeleting}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
          >
            {bulkDeleting ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            {t('sessions.deleteSelected')}
          </button>
          <button
            onClick={fetchSessions}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('sessions.refresh')}
          </button>
        </>}
      />

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          {isDefaultWorkspace && (
            <div className="flex-1 min-w-[160px]">
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('sessions.workspace')}</label>
              <select
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={filterWorkspace}
                onChange={e => setFilterWorkspace(e.target.value)}
              >
                <option value="">{t('sessions.allWorkspaces')}</option>
                {workspaces.map(ws => <option key={ws.name} value={ws.name}>{ws.name}</option>)}
              </select>
            </div>
          )}

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('sessions.status')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}
            >
              <option value="">{t('sessions.allStatuses')}</option>
              {allStatuses.map(s => <option key={s} value={s}>{statusLabel(s, t)}</option>)}
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('sessions.type')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFlow}
              onChange={e => setFilterFlow(e.target.value)}
            >
              <option value="">{t('sessions.allTypes')}</option>
              <option value="true">{t('sessions.flow')}</option>
              <option value="false">{t('sessions.standalone')}</option>
            </select>
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('sessions.from')}</label>
            <DateInput
              mode="datetime"
              valueFormat="iso"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFrom}
              onChange={setFilterFrom}
            />
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('sessions.to')}</label>
            <DateInput
              mode="datetime"
              valueFormat="iso"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterTo}
              onChange={setFilterTo}
            />
          </div>

          {hasFilters && (
            <button
              onClick={() => { setFilterWorkspace(isDefaultWorkspace ? '' : (selectedWorkspace || '')); setFilterStatus(''); setFilterFlow(''); setFilterFrom(''); setFilterTo(''); }}
              className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              <X className="w-4 h-4" /> {t('sessions.clear')}
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
            <p className="text-gray-500 text-sm">{t('sessions.noSessionsFound')}</p>
            <p className="text-gray-400 text-xs mt-1">{t('sessions.sessionsAreCreatedWhenYou')}</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className={`hidden md:grid ${mdGridCols} gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200`}>
              <div className="flex items-center justify-center">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
              </div>
              <div>{t('sessions.session')}</div>
              <div className="text-center">{t('sessions.status')}</div>
              {isDefaultWorkspace && <div className="text-center">{t('sessions.workspace')}</div>}
              <div className="text-center">{t('sessions.messages')}</div>
              <div className="text-center">{t('sessions.duration')}</div>
              <div className="text-center">{t('sessions.type')}</div>
              <div className="text-right">{t('sessions.actions')}</div>
            </div>
            <div className="divide-y divide-gray-100">
              {sessions.map(session => (
                <div
                  key={session.session_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/sessions/${session.session_id}`)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/sessions/${session.session_id}`); } }}
                  className={`grid grid-cols-1 ${mdGridCols} gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset`}
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
                      {session.title || <span className="text-gray-400 italic">{t('sessions.untitled')}</span>}
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

                  {isDefaultWorkspace && (
                    <div className="text-sm text-gray-700 md:self-center md:text-center">
                      {session.workspace || '—'}
                    </div>
                  )}

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
                        {t('sessions.flow')}
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
                          title={t('sessions.stopAllRunsInSession')}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                        >
                          {stopping[session.session_id]
                            ? <Loader className="w-3.5 h-3.5 animate-spin" />
                            : <Square className="w-3.5 h-3.5" />}
                          {t('common.stop')}
                        </button>
                      )}
                      <button
                        onClick={e => { e.stopPropagation(); handleDelete(session); }}
                        disabled={deleting[session.session_id] || session.status === 'running'}
                        title={session.status === 'running' ? t('sessions.stopBeforeDeleting') : t('sessions.deleteSession')}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      >
                        {deleting[session.session_id]
                          ? <Loader className="w-3.5 h-3.5 animate-spin" />
                          : <Trash2 className="w-3.5 h-3.5" />}
                        {t('common.delete')}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {sessions.length < totalSessions && (
              <button
                type="button"
                onClick={() => fetchSessions(offset + PAGE_SIZE, true)}
                className="w-full py-2.5 text-sm text-indigo-600 hover:bg-indigo-50 border-t border-gray-100"
              >
                {t('sessions.loadMore', { count: totalSessions - sessions.length })}
              </button>
            )}
          </div>
        )}
      </div>

      {!loading && sessions.length > 0 && (
        <p className="text-xs text-gray-400 text-right">
          {t('sessions.shownOfTotal', { shown: sessions.length, total: totalSessions })}
        </p>
      )}
    </PageContainer>
  );
}
