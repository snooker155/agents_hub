import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch, useStreamEvent } from '../components/stream';
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
  Send,
  Terminal,
  Server,
  MessageSquare,
  ScrollText,
  Gamepad2,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { ExternalRunBadge } from '../components/RunOriginBadges';
import { isExternalRun } from '../components/runOrigin';
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

// Origin channel of a run (set at creation, see run_manager.run_log_path).
const CHANNEL_META = {
  local:         { icon: Terminal,      bg: 'bg-gray-100',    text: 'text-gray-700' },
  node:          { icon: Server,        bg: 'bg-emerald-100', text: 'text-emerald-700' },
  continuation:  { icon: RefreshCw,     bg: 'bg-amber-100',   text: 'text-amber-700' },
  chat:          { icon: MessageSquare, bg: 'bg-indigo-100',  text: 'text-indigo-700' },
  chat_flow:     { icon: Workflow,      bg: 'bg-violet-100',  text: 'text-violet-700' },
  chat_delegate: { icon: Send,          bg: 'bg-blue-100',    text: 'text-blue-700' },
  telegram:      { icon: Send,          bg: 'bg-sky-100',     text: 'text-sky-700' },
  flow:          { icon: Workflow,      bg: 'bg-violet-100',  text: 'text-violet-700' },
  http:          { icon: Globe,         bg: 'bg-cyan-100',    text: 'text-cyan-700' },
  external:      { icon: Globe,         bg: 'bg-cyan-100',    text: 'text-cyan-700' },
  sim:           { icon: Gamepad2,      bg: 'bg-fuchsia-100', text: 'text-fuchsia-700' },
};

function ChannelBadge({ channel }) {
  const { t } = useI18n();
  if (!channel) return <span className="text-xs text-gray-400">—</span>;
  const meta = CHANNEL_META[channel];
  const c = meta
    ? { ...meta, label: t(`messages.channels.${channel}`) }
    : { label: channel, icon: AlertCircle, bg: 'bg-gray-100', text: 'text-gray-500' };
  const Icon = c.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${c.bg} ${c.text}`}>
      <Icon className="w-3 h-3" />
      {c.label}
    </span>
  );
}

// What kind of run this is, which for an external one is the answer to a
// different question than Flow/Standalone: it was not run here at all.
function TypeBadge({ run }) {
  if (isExternalRun(run)) return <ExternalRunBadge run={run} />;
  const isFlow = run?.is_flow;
  const Icon = isFlow ? Workflow : Terminal;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${
      isFlow ? 'bg-violet-100 text-violet-700' : 'bg-gray-100 text-gray-700'
    }`}>
      <Icon className="w-3 h-3" />
      {isFlow ? 'Flow' : 'Standalone'}
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

// One window of runs per request. Large enough that the common case is a single
// round-trip, small enough that the first paint never waits on a full table.
const PAGE_SIZE = 200;

// ---- Main page --------------------------------------------------------------

export default function Messages() {
  const { t } = useI18n();
  const { selectedWorkspace, liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const [messages, setMessages]       = useState([]);
  const [totalMessages, setTotalMessages] = useState(0);
  const [offset, setOffset]           = useState(0);
  const [agents, setAgents]           = useState([]);
  const [workspaces, setWorkspaces]   = useState([]);
  const [loading, setLoading]         = useState(true);

  const [filterWorkspace, setFilterWorkspace] = useState(selectedWorkspace || '');
  const [filterAgent,     setFilterAgent]     = useState('');
  const [filterStatus,    setFilterStatus]    = useState('');
  const [filterFlow,      setFilterFlow]      = useState('');
  const [filterChannel,   setFilterChannel]   = useState('');
  const [filterFrom,      setFilterFrom]      = useState('');
  const [filterTo,        setFilterTo]        = useState('');

  const [stopping,      setStopping]      = useState({});
  const [deleting,      setDeleting]      = useState({});
  const [selectedIds,   setSelectedIds]   = useState({});
  const [bulkDeleting,  setBulkDeleting]  = useState(false);
  const selectAllRef = useRef(null);

  // The workspace filter is only offered in the default workspace. In a specific
  // workspace the page is locked to it and the dropdown is hidden.
  const isDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';
  const effectiveWorkspace = filterWorkspace || '';

  // The Workspace column is only shown in the default workspace. The grid
  // template literals are written out in full so Tailwind's JIT can detect them.
  const mdGridCols = isDefaultWorkspace
    ? 'md:grid-cols-[36px_minmax(0,2.3fr)_1fr_1.1fr_1fr_1fr_0.7fr_0.6fr_140px]'
    : 'md:grid-cols-[36px_minmax(0,2.3fr)_1fr_1.1fr_1fr_0.7fr_0.6fr_140px]';

  // The backend filters and pages in SQL and answers {items, total, ...}; the
  // page asks for one window at a time and grows it on demand, so a workspace
  // with a hundred thousand runs costs the same first paint as an empty one.
  const fetchMessages = useCallback(async (nextOffset = 0, append = false) => {
    try {
      const params = { limit: PAGE_SIZE, offset: nextOffset };
      if (effectiveWorkspace) params.workspace = effectiveWorkspace;
      if (filterAgent)  params.agent_id = filterAgent;
      if (filterStatus) params.status   = filterStatus;
      if (filterFrom)   params.from_date = filterFrom;
      if (filterTo)     params.to_date   = filterTo;
      if (filterFlow === 'true')  params.is_flow = true;
      if (filterFlow === 'false') params.is_flow = false;
      if (filterChannel) params.channel = filterChannel;
      const res = await getMessages(params);
      const data = res.data || {};
      const items = Array.isArray(data) ? data : (data.items || []);
      setMessages(prev => (append ? [...prev, ...items] : items));
      setTotalMessages(Array.isArray(data) ? items.length : (data.total || 0));
      setOffset(nextOffset);
    } catch (err) {
      console.error('Failed to load messages', err);
    } finally {
      setLoading(false);
    }
  }, [effectiveWorkspace, filterAgent, filterStatus, filterFrom, filterTo, filterFlow, filterChannel]);

  useEffect(() => {
    setFilterWorkspace(selectedWorkspace || '');
  }, [selectedWorkspace]);

  useEffect(() => {
    getWorkspaces().then(r => setWorkspaces(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    getAgents(effectiveWorkspace || undefined).then(r => setAgents(r.data)).catch(() => {});
    setFilterAgent('');
  }, [effectiveWorkspace]);

  useEffect(() => {
    setLoading(true);
    fetchMessages(0, false);
  }, [fetchMessages, liveUpdates]);
  // Coarse invalidations still arrive from bulk operations; a single run's
  // change comes as a delta below and patches its row without a refetch.
  useLiveRefetch(() => fetchMessages(0, false), { type: 'runs.changed', enabled: liveUpdates });
  useStreamEvent('app', 'runs.delta', useCallback((ev) => {
    if (!liveUpdates || !Array.isArray(ev.items)) return;
    setMessages(prev => {
      if (!prev.length) return prev;
      const deltas = new Map(ev.items.map(d => [d.run_id, d]));
      let touched = false;
      const next = prev.map(m => {
        const delta = deltas.get(m.run_id);
        if (!delta) return m;
        touched = true;
        return { ...m, ...delta };
      });
      return touched ? next : prev;
    });
  }, [liveUpdates]));

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
    if (!window.confirm(t('messages.confirmDelete', { id: msg.run_id }))) return;
    setDeleting(s => ({ ...s, [msg.run_id]: true }));
    try {
      await deleteMessage(msg.run_id, { delete_log: true });
      setSelectedIds(prev => { const next = { ...prev }; delete next[msg.run_id]; return next; });
      await fetchMessages();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('messages.deleteFailed'));
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
    if (!window.confirm(t('messages.confirmBulkDelete', { count: checkedIds.length }))) return;
    setBulkDeleting(true);
    try {
      const results = await Promise.all(
        checkedIds.map(async (runId) => {
          try {
            await deleteMessage(runId, { delete_log: true });
            return { runId, ok: true };
          } catch (err) {
            return { runId, ok: false, msg: err?.response?.data?.detail || t('common.failed') };
          }
        })
      );
      const failed = results.filter(r => !r.ok);
      setSelectedIds({});
      await fetchMessages();
      if (failed.length) {
        window.alert(t('messages.bulkDeletePartial', { count: failed.length, id: failed[0].runId, reason: failed[0].msg }));
      }
    } finally {
      setBulkDeleting(false);
    }
  };

  const allStatuses = ['running', 'completed', 'failed', 'error', 'stopped', 'stop'];
  const hasFilters = filterAgent || filterStatus || filterFrom || filterTo || filterFlow || filterChannel || (isDefaultWorkspace && filterWorkspace);

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={ScrollText}
        title={t('messages.messages')}
        description={filterWorkspace
          ? t('messages.descriptionInWorkspace', { workspace: filterWorkspace })
          : t('messages.descriptionAllWorkspaces')}
        actions={<>
          <button
            onClick={handleBulkDelete}
            disabled={!checkedIds.length || bulkDeleting}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
            title={checkedIds.length ? t('messages.deleteCountSelected', { count: checkedIds.length }) : t('messages.selectRowsToDelete')}
          >
            {bulkDeleting ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            {t('messages.deleteSelected')}
          </button>
          <button
            onClick={fetchMessages}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('messages.refresh')}
          </button>
        </>}
      />

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          {isDefaultWorkspace && (
            <div className="flex-1 min-w-[160px]">
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.workspace')}</label>
              <select
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={filterWorkspace}
                onChange={e => setFilterWorkspace(e.target.value)}
              >
                <option value="">{t('messages.allWorkspaces')}</option>
                {workspaces.map(ws => (
                  <option key={ws.name} value={ws.name}>{ws.name}</option>
                ))}
              </select>
            </div>
          )}

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.agent')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterAgent}
              onChange={e => setFilterAgent(e.target.value)}
            >
              <option value="">{t('messages.allAgents')}</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.status')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}
            >
              <option value="">{t('messages.allStatuses')}</option>
              {allStatuses.map(s => (
                <option key={s} value={s}>{statusLabel(s, t)}</option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.type')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFlow}
              onChange={e => setFilterFlow(e.target.value)}
            >
              <option value="">{t('messages.allTypes')}</option>
              <option value="true">{t('messages.flow')}</option>
              <option value="false">{t('messages.standalone')}</option>
            </select>
          </div>

          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.channel')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterChannel}
              onChange={e => setFilterChannel(e.target.value)}
            >
              <option value="">{t('messages.allChannels')}</option>
              {Object.keys(CHANNEL_META).map((value) => (
                <option key={value} value={value}>{t(`messages.channels.${value}`)}</option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.from')}</label>
            <DateInput
              mode="datetime"
              valueFormat="iso"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterFrom}
              onChange={setFilterFrom}
            />
          </div>

          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('messages.to')}</label>
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
              onClick={() => {
                setFilterWorkspace(isDefaultWorkspace ? '' : (selectedWorkspace || ''));
                setFilterAgent('');
                setFilterStatus('');
                setFilterFlow('');
                setFilterChannel('');
                setFilterFrom('');
                setFilterTo('');
              }}
              className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50"
            >
              <X className="w-4 h-4" />
              {t('messages.clear')}
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
            <p className="text-gray-500 text-sm">{t('messages.noMessagesFound')}</p>
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
              <div>{t('messages.message')}</div>
              <div className="text-center">{t('messages.status')}</div>
              <div className="text-center">{t('messages.agent')}</div>
              <div className="text-center">{t('messages.type')}</div>
              {isDefaultWorkspace && <div className="text-center">{t('messages.workspace')}</div>}
              <div className="text-center">{t('messages.duration')}</div>
              <div className="text-center">{t('messages.channel')}</div>
              <div className="text-right">{t('messages.actions')}</div>
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
                  className={`grid grid-cols-1 ${mdGridCols} gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset`}
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
                      {msg.task_title || <span className="text-gray-400 italic">{t('messages.noTitle')}</span>}
                    </div>
                    <div className="text-xs text-gray-500 mt-1 truncate">{msg.run_id}</div>
                    {msg.session_id && (
                      <div className="text-xs text-indigo-500 mt-0.5 truncate" title={`Session: ${msg.session_id}`}>
                        session: {msg.session_id.slice(0, 8)}…
                      </div>
                    )}
                  </div>

                  <div className="md:self-center md:flex md:justify-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('messages.status')}</div>
                    <StatusBadge status={msg.status} />
                  </div>

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('messages.agent')}</div>
                    <span className="text-xs">{msg.agent_id || '—'}</span>
                  </div>

                  <div className="md:self-center md:flex md:justify-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('messages.type')}</div>
                    <TypeBadge run={msg} />
                  </div>

                  {isDefaultWorkspace && (
                    <div className="text-sm text-gray-700 md:self-center md:text-center">
                      <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('messages.workspace')}</div>
                      {msg.workspace || '—'}
                    </div>
                  )}

                  <div className="text-sm text-gray-700 md:self-center md:text-center">
                    <div className="md:hidden text-[11px] uppercase tracking-wide text-gray-400 mb-1">{t('messages.duration')}</div>
                    {duration(msg.started_at, msg.finished_at)}
                  </div>

                  <div className="md:self-center md:flex md:flex-wrap md:justify-center gap-1">
                    <ChannelBadge channel={msg.channel} />
                  </div>

                  <div className="flex md:justify-end md:self-center">
                    <div className="inline-flex items-center gap-2">
                      {msg.status === 'running' && (
                        <button
                          onClick={e => { e.stopPropagation(); handleStop(msg.run_id); }}
                          disabled={stopping[msg.run_id]}
                          title={t('messages.stop')}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                        >
                          {stopping[msg.run_id]
                            ? <Loader className="w-3.5 h-3.5 animate-spin" />
                            : <Square className="w-3.5 h-3.5" />}
                          {t('common.stop')}
                        </button>
                      )}
                      <button
                        onClick={e => { e.stopPropagation(); handleDelete(msg); }}
                        disabled={deleting[msg.run_id] || msg.status === 'running'}
                        title={msg.status === 'running' ? t('messages.stopBeforeDeleting') : t('common.delete')}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      >
                        {deleting[msg.run_id]
                          ? <Loader className="w-3.5 h-3.5 animate-spin" />
                          : <Trash2 className="w-3.5 h-3.5" />}
                        {t('common.delete')}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {messages.length < totalMessages && (
              <button
                type="button"
                onClick={() => fetchMessages(offset + PAGE_SIZE, true)}
                className="w-full py-2.5 text-sm text-indigo-600 hover:bg-indigo-50 border-t border-gray-100"
              >
                {t('messages.loadMore', { count: totalMessages - messages.length })}
              </button>
            )}
          </div>
        )}
      </div>

      {!loading && messages.length > 0 && (
        <p className="text-xs text-gray-400 text-right">
          {t('messages.shownOfTotal', { shown: messages.length, total: totalMessages })}
        </p>
      )}
    </PageContainer>
  );
}
