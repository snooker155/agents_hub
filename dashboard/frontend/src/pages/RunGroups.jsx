import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { listRunGroups, stopRunGroup } from '../api';
import {
  Layers,
  Loader,
  RefreshCw,
  Square,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  CheckCircle,
  XCircle,
  Clock,
  AlertCircle,
} from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n, statusLabel } from '../i18n';

// ---- helpers ----------------------------------------------------------------

const KIND_STYLES = {
  flow:      'bg-violet-100 text-violet-700',
  loop:      'bg-amber-100 text-amber-700',
  team:      'bg-blue-100 text-blue-700',
  container: 'bg-emerald-100 text-emerald-700',
};

const STATUS_STYLES = {
  running:     { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Loader },
  pending:     { bg: 'bg-gray-100',   text: 'text-gray-500',   icon: Clock },
  in_progress: { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Loader },
  stopping:    { bg: 'bg-orange-100', text: 'text-orange-700', icon: Square },
  stopped:     { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Square },
  completed:   { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  done:        { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  resolved:    { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  reviewed:    { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  failed:      { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  error:       { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
};

function StatusBadge({ status }) {
  const { t } = useI18n();
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'running' || status === 'in_progress' ? 'animate-spin' : ''}`} />
      {statusLabel(status, t)}
    </span>
  );
}

function KindBadge({ kind }) {
  const { t } = useI18n();
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${KIND_STYLES[kind] || 'bg-gray-100 text-gray-600'}`}>
      {t(`runGroups.kind.${kind}`, { defaultValue: kind })}
    </span>
  );
}

/** The owning record's own page, when it has one. A loop's "detail" is the
 * Loops page itself (there is no per-run route for one), so it links there
 * rather than to nothing. */
function entityLink(group) {
  switch (group.kind) {
    case 'flow':
      return group.parent_id ? `/flows/${group.parent_id}` : null;
    case 'team':
      return group.parent_id ? `/teams/${group.parent_id}` : null;
    case 'loop':
      return '/loops';
    case 'container':
      return `/tasks/${group.id}`;
    default:
      return null;
  }
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

const GRID_COLS = 'md:grid-cols-[28px_1.4fr_0.8fr_1fr_1fr_0.7fr_0.7fr_0.9fr_120px]';

// ---- Main page --------------------------------------------------------------

export default function RunGroups() {
  const { t } = useI18n();
  const { selectedWorkspace, liveUpdates } = useWorkspace();

  const [groups, setGroups] = useState([]);
  const [kinds, setKinds] = useState([]);
  const [filterKind, setFilterKind] = useState('');
  const [loading, setLoading] = useState(true);
  const [stopping, setStopping] = useState({});
  const [expanded, setExpanded] = useState({});

  const isDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';

  const fetchGroups = useCallback(async () => {
    try {
      const params = { limit: 100 };
      if (filterKind) params.kind = filterKind;
      if (!isDefaultWorkspace) params.workspace = selectedWorkspace;
      const res = await listRunGroups(params);
      const data = res.data || {};
      setGroups(data.groups || []);
      setKinds(data.kinds || []);
    } catch (err) {
      console.error('Failed to load run groups', err);
    } finally {
      setLoading(false);
    }
  }, [filterKind, isDefaultWorkspace, selectedWorkspace]);

  useEffect(() => {
    setLoading(true);
    fetchGroups();
  }, [fetchGroups, liveUpdates]);

  useLiveRefetch(fetchGroups, {
    sources: [
      { type: 'flow_runs.changed' },
      { type: 'loop_runs.changed' },
      { type: 'team_runs.changed' },
      { type: 'tasks.changed' },
      { type: 'runs.changed' },
    ],
    enabled: liveUpdates,
  });

  const rowKey = useCallback((g) => `${g.kind}:${g.id}`, []);

  const toggleExpanded = (g) => {
    setExpanded((prev) => ({ ...prev, [rowKey(g)]: !prev[rowKey(g)] }));
  };

  const handleStop = async (g) => {
    if (!window.confirm(t('runGroups.confirmStop', { kind: t(`runGroups.kind.${g.kind}`, { defaultValue: g.kind }) }))) return;
    setStopping((s) => ({ ...s, [rowKey(g)]: true }));
    try {
      await stopRunGroup(g.kind, g.id);
      await fetchGroups();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('runGroups.stopFailed'));
    } finally {
      setStopping((s) => ({ ...s, [rowKey(g)]: false }));
    }
  };

  const total = groups.length;
  const visibleKinds = useMemo(() => (kinds.length ? kinds : ['flow', 'loop', 'team', 'container']), [kinds]);

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Layers}
        title={t('runGroups.title')}
        description={t('runGroups.description')}
        actions={
          <button
            onClick={fetchGroups}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('runGroups.refresh')}
          </button>
        }
      />

      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[160px]">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">
              {t('runGroups.columns.kind')}
            </label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={filterKind}
              onChange={(e) => setFilterKind(e.target.value)}
            >
              <option value="">{t('runGroups.allKinds')}</option>
              {visibleKinds.map((k) => (
                <option key={k} value={k}>{t(`runGroups.kind.${k}`, { defaultValue: k })}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        {loading ? (
          <div className="bg-white rounded-xl border border-gray-200 flex justify-center py-16">
            <Loader className="w-6 h-6 animate-spin text-indigo-500" />
          </div>
        ) : groups.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
            <Layers className="w-10 h-10 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500 text-sm">{t('runGroups.noGroupsFound')}</p>
            <p className="text-gray-400 text-xs mt-1">{t('runGroups.noGroupsHint')}</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className={`hidden md:grid ${GRID_COLS} gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200`}>
              <div />
              <div>{t('runGroups.columns.group')}</div>
              <div className="text-center">{t('runGroups.columns.status')}</div>
              <div>{t('runGroups.columns.started')}</div>
              <div>{t('runGroups.columns.finished')}</div>
              <div className="text-right">{t('runGroups.columns.cost')}</div>
              <div className="text-center">{t('runGroups.columns.children')}</div>
              {isDefaultWorkspace && <div>{t('runGroups.columns.workspace')}</div>}
              <div className="text-right">{t('runGroups.columns.actions')}</div>
            </div>
            <div className="divide-y divide-gray-100">
              {groups.map((g) => {
                const key = rowKey(g);
                const isOpen = !!expanded[key];
                const link = entityLink(g);
                return (
                  <div key={key}>
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => toggleExpanded(g)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleExpanded(g); } }}
                      className={`grid grid-cols-1 ${GRID_COLS} gap-3 px-4 py-3 hover:bg-indigo-50/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-inset`}
                    >
                      <div className="hidden md:flex items-center text-gray-400">
                        {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                      </div>

                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <KindBadge kind={g.kind} />
                          <span className="font-medium text-gray-900 truncate">
                            {g.title || <span className="text-gray-400 italic">{t('runGroups.untitled')}</span>}
                          </span>
                        </div>
                        <div className="text-xs text-gray-500 mt-1 truncate">{g.id}</div>
                      </div>

                      <div className="md:self-center md:flex md:justify-center">
                        <StatusBadge status={g.status} />
                      </div>

                      <div className="text-xs text-gray-600 md:self-center">
                        {g.started_at ? new Date(g.started_at).toLocaleString() : '—'}
                      </div>

                      <div className="text-xs text-gray-600 md:self-center">
                        {g.finished_at ? new Date(g.finished_at).toLocaleString() : duration(g.started_at, g.finished_at)}
                      </div>

                      <div className="text-sm text-gray-700 md:self-center text-right">
                        ${(g.total_cost || 0).toFixed(4)}
                      </div>

                      <div className="text-sm text-gray-700 md:self-center md:text-center">
                        {(g.children || []).length}
                      </div>

                      {isDefaultWorkspace && (
                        <div className="text-sm text-gray-700 md:self-center truncate">
                          {g.workspace || '—'}
                        </div>
                      )}

                      <div className="flex md:justify-end md:self-center">
                        {g.active && (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleStop(g); }}
                            disabled={stopping[key]}
                            title={t('runGroups.stop')}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-md hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                          >
                            {stopping[key]
                              ? <Loader className="w-3.5 h-3.5 animate-spin" />
                              : <Square className="w-3.5 h-3.5" />}
                            {stopping[key] ? t('runGroups.stopping') : t('runGroups.stop')}
                          </button>
                        )}
                      </div>
                    </div>

                    {isOpen && (
                      <div className="bg-gray-50 border-t border-gray-100 px-4 py-3 md:pl-11">
                        <div className="flex items-center justify-between mb-2">
                          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                            {t('runGroups.childRuns')}
                          </p>
                          {link && (
                            <Link
                              to={link}
                              onClick={(e) => e.stopPropagation()}
                              className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800"
                            >
                              <ExternalLink className="w-3 h-3" />
                              {t(`runGroups.entity.${g.kind}`, { defaultValue: t('runGroups.openEntity') })}
                            </Link>
                          )}
                        </div>
                        {(g.children || []).length === 0 ? (
                          <p className="text-xs text-gray-400">{t('runGroups.noChildRuns')}</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {g.children.map((runId) => (
                              <Link
                                key={runId}
                                to={`/messages/${runId}`}
                                onClick={(e) => e.stopPropagation()}
                                className="px-2 py-1 rounded-md text-xs font-mono bg-white border border-gray-200 text-gray-600 hover:border-indigo-300 hover:text-indigo-700"
                              >
                                {runId}
                              </Link>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {!loading && groups.length > 0 && (
        <p className="text-xs text-gray-400 text-right">
          {t('runGroups.shownOfTotal', { shown: groups.length, total })}
        </p>
      )}
    </PageContainer>
  );
}
