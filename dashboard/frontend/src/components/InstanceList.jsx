import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity, Box, ChevronDown, ChevronRight, MessageSquare, RefreshCw,
  Search, Server, Square,
} from 'lucide-react';

import { getInstances, stopInstance } from '../api';
import { useStreamEvent, useLiveRefetch } from './stream';
import { KIND_ICONS, STATE_STYLES, formatDuration, relativeTime } from './instanceUtils';
import { useI18n } from '../i18n';

/*
 * The list of live agent copies, shared by the Instances page and the per-agent
 * Instances tab.
 *
 * Two things make this list different from Messages. It shows *copies*, not the
 * work they did — a node sitting in standby has no run in flight and still
 * belongs here. And it is built for a thousand rows: the server filters,
 * orders and pages in SQL, updates arrive as per-row deltas that patch one line
 * instead of refetching the list, and identical copies of one agent collapse
 * into a group you expand only when you care.
 */

const PAGE_SIZE = 100;

export function StateBadge({ state, t }) {
  const style = STATE_STYLES[state] || STATE_STYLES.finished;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${style.badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
      {t(`instances.states.${state}`, { defaultValue: state })}
    </span>
  );
}

/** Carrier link: a node and a container have their own pages; say so. */
function CarrierCell({ instance, t }) {
  if (instance.node_id) {
    return (
      <Link
        to={`/nodes/${instance.node_id}`}
        onClick={(e) => e.stopPropagation()}
        className="text-xs text-indigo-600 hover:underline inline-flex items-center gap-1"
      >
        <Server className="w-3 h-3" />
        {instance.container_name || `${String(instance.node_id).slice(0, 8)}`}
      </Link>
    );
  }
  if (instance.container_name) {
    return (
      <Link to="/containers" onClick={(e) => e.stopPropagation()}
            className="text-xs text-indigo-600 hover:underline inline-flex items-center gap-1">
        <Box className="w-3 h-3" />{instance.container_name}
      </Link>
    );
  }
  return <span className="text-xs text-gray-400">{t('instances.carrier.local')}</span>;
}

function InstanceRow({ instance, showAgent, onStop, busy, t }) {
  const KindIcon = KIND_ICONS[instance.kind] || Activity;
  return (
    <tr className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
      <td className="px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <KindIcon className="w-4 h-4 text-gray-400 shrink-0" />
          <Link to={`/instances/${instance.instance_id}`}
                className="text-sm font-medium text-gray-900 hover:text-indigo-600 truncate">
            {instance.label || instance.instance_id}
          </Link>
        </div>
        {instance.task_title && (
          <div className="text-[11px] text-gray-400 truncate pl-6">{instance.task_title}</div>
        )}
      </td>
      {showAgent && (
        <td className="px-3 py-2 whitespace-nowrap">
          <Link to={`/agents/${instance.agent_id}`}
                className="text-xs text-gray-600 hover:text-indigo-600">{instance.agent_id}</Link>
        </td>
      )}
      <td className="px-3 py-2 whitespace-nowrap"><StateBadge state={instance.state} t={t} /></td>
      <td className="px-3 py-2 max-w-[280px]">
        <div className="text-xs text-gray-600 truncate" title={instance.last_activity || ''}>
          {instance.last_activity || <span className="text-gray-300">—</span>}
        </div>
        <div className="text-[11px] text-gray-400">{relativeTime(instance.last_activity_at, t)}</div>
      </td>
      <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap text-right">
        {instance.runs_count || 0}
      </td>
      <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap text-right">
        {(instance.total_tokens || 0).toLocaleString()}
      </td>
      <td className="px-3 py-2 text-xs text-gray-600 whitespace-nowrap text-right">
        {formatDuration(instance.total_duration_ms)}
      </td>
      <td className="px-3 py-2 whitespace-nowrap"><CarrierCell instance={instance} t={t} /></td>
      <td className="px-3 py-2 whitespace-nowrap text-right">
        <div className="inline-flex items-center gap-1">
          <Link to={`/instances/${instance.instance_id}`}
                title={t('instances.actions.message')}
                className="p-1.5 rounded hover:bg-indigo-50 text-indigo-600">
            <MessageSquare className="w-3.5 h-3.5" />
          </Link>
          {['active', 'starting'].includes(instance.state) && (
            <button type="button" onClick={() => onStop(instance)} disabled={busy}
                    title={t('instances.actions.stop')}
                    className="p-1.5 rounded hover:bg-red-50 text-red-600 disabled:opacity-40">
              <Square className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

export default function InstanceList({
  agentId = null,
  workspace = undefined,
  liveUpdates = true,
  showFilters = true,
  showAgentColumn = true,
  defaultLiveOnly = false,
}) {
  const { t } = useI18n();
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [busyIds, setBusyIds] = useState({});
  const [collapsed, setCollapsed] = useState({});

  const [filterState, setFilterState] = useState('');
  const [filterKind, setFilterKind] = useState('');
  const [liveOnly, setLiveOnly] = useState(defaultLiveOnly);
  const [query, setQuery] = useState('');
  const [grouped, setGrouped] = useState(false);

  const params = useMemo(() => ({
    workspace,
    agent_id: agentId || undefined,
    state: filterState || undefined,
    kind: filterKind || undefined,
    live: liveOnly ? true : undefined,
    q: query.trim() || undefined,
    limit: PAGE_SIZE,
  }), [workspace, agentId, filterState, filterKind, liveOnly, query]);

  const fetchPage = useCallback(async (nextOffset = 0, append = false) => {
    if (!append) setLoading(true);
    try {
      const res = await getInstances({ ...params, offset: nextOffset });
      const data = res.data || {};
      setItems((prev) => (append ? [...prev, ...(data.items || [])] : (data.items || [])));
      setCounts(data.counts || {});
      setTotal(data.total || 0);
      setOffset(nextOffset);
    } catch (e) {
      console.error('Failed to load instances', e);
      if (!append) { setItems([]); setTotal(0); }
    } finally {
      setLoading(false);
    }
  }, [params]);

  // Debounce the search box so typing does not fire a query per keystroke.
  const searchTimer = useRef(null);
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => fetchPage(0, false), query ? 250 : 0);
    return () => { if (searchTimer.current) clearTimeout(searchTimer.current); };
  }, [fetchPage, query]);

  // Per-row deltas: patch the line in place. A delta for a row we do not hold
  // (a copy that was just created, or one that now matches the filter) falls
  // through to the debounced refetch below.
  useStreamEvent('app', 'instances.delta', useCallback((ev) => {
    if (!liveUpdates || !Array.isArray(ev.items)) return;
    setItems((prev) => {
      if (!prev.length) return prev;
      const byId = new Map(prev.map((i) => [i.instance_id, i]));
      let touched = false;
      for (const delta of ev.items) {
        const current = byId.get(delta.instance_id);
        if (!current) continue;
        if (agentId && delta.agent_id && delta.agent_id !== agentId) continue;
        byId.set(delta.instance_id, { ...current, ...delta });
        touched = true;
      }
      return touched ? prev.map((i) => byId.get(i.instance_id) || i) : prev;
    });
  }, [liveUpdates, agentId]));

  useLiveRefetch(() => fetchPage(0, false), {
    type: 'instances.changed', enabled: liveUpdates,
  });

  const handleStop = async (instance) => {
    setBusyIds((b) => ({ ...b, [instance.instance_id]: true }));
    try {
      await stopInstance(instance.instance_id);
      await fetchPage(0, false);
    } catch (e) {
      console.error('Failed to stop instance', e);
    } finally {
      setBusyIds((b) => { const next = { ...b }; delete next[instance.instance_id]; return next; });
    }
  };

  const groups = useMemo(() => {
    if (!grouped) return null;
    const map = new Map();
    for (const item of items) {
      const key = item.agent_id || '—';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(item);
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [grouped, items]);

  const showAgent = showAgentColumn && !agentId;
  const hasMore = items.length < total;

  const columns = (
    <tr className="text-[11px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
      <th className="px-3 py-2 text-left font-medium">{t('instances.columns.instance')}</th>
      {showAgent && <th className="px-3 py-2 text-left font-medium">{t('instances.columns.agent')}</th>}
      <th className="px-3 py-2 text-left font-medium">{t('instances.columns.state')}</th>
      <th className="px-3 py-2 text-left font-medium">{t('instances.columns.doing')}</th>
      <th className="px-3 py-2 text-right font-medium">{t('instances.columns.runs')}</th>
      <th className="px-3 py-2 text-right font-medium">{t('instances.columns.tokens')}</th>
      <th className="px-3 py-2 text-right font-medium">{t('instances.columns.duration')}</th>
      <th className="px-3 py-2 text-left font-medium">{t('instances.columns.carrier')}</th>
      <th className="px-3 py-2" />
    </tr>
  );

  return (
    <div className="space-y-3">
      {/* Counts strip — the whole point of the page: what is alive right now. */}
      <div className="flex flex-wrap items-center gap-2">
        {['live', 'active', 'standby', 'finished', 'failed'].map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              if (key === 'live') { setLiveOnly((v) => !v); setFilterState(''); }
              else { setFilterState((v) => (v === key ? '' : key)); setLiveOnly(false); }
            }}
            className={`px-2.5 py-1 rounded-lg border text-xs transition-colors ${
              (key === 'live' && liveOnly) || filterState === key
                ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
            }`}
          >
            <span className="font-semibold">{counts[key] ?? 0}</span>{' '}
            {t(`instances.counts.${key}`)}
          </button>
        ))}
      </div>

      {showFilters && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('instances.filters.searchPlaceholder')}
              className="pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg w-64 bg-white"
            />
          </div>
          <select value={filterKind} onChange={(e) => setFilterKind(e.target.value)}
                  className="px-2 py-1.5 text-sm border border-gray-200 rounded-lg bg-white">
            <option value="">{t('instances.filters.allKinds')}</option>
            {Object.keys(KIND_ICONS).map((k) => (
              <option key={k} value={k}>{t(`instances.kinds.${k}`)}</option>
            ))}
          </select>
          <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 px-2">
            <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
            {t('instances.filters.groupByAgent')}
          </label>
          <button type="button" onClick={() => fetchPage(0, false)}
                  className="p-1.5 rounded-lg border border-gray-200 bg-white text-gray-500 hover:bg-gray-50"
                  title={t('instances.actions.refresh')}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <span className="text-xs text-gray-400 ml-auto">
            {t('instances.showing', { shown: items.length, total })}
          </span>
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {loading && !items.length ? (
          <div className="p-8 text-center text-sm text-gray-400">{t('instances.loading')}</div>
        ) : !items.length ? (
          <div className="p-8 text-center">
            <Activity className="w-8 h-8 text-gray-300 mx-auto mb-2" />
            <div className="text-sm text-gray-500">{t('instances.empty.title')}</div>
            <div className="text-xs text-gray-400 mt-1">{t('instances.empty.hint')}</div>
          </div>
        ) : grouped ? (
          <div>
            {groups.map(([agent, rows]) => {
              const isCollapsed = collapsed[agent];
              const live = rows.filter((r) => r.is_live).length;
              return (
                <div key={agent} className="border-b border-gray-100 last:border-0">
                  <button
                    type="button"
                    onClick={() => setCollapsed((c) => ({ ...c, [agent]: !c[agent] }))}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 text-left"
                  >
                    {isCollapsed ? <ChevronRight className="w-4 h-4 text-gray-400" />
                                 : <ChevronDown className="w-4 h-4 text-gray-400" />}
                    <span className="text-sm font-medium text-gray-800">{agent}</span>
                    <span className="text-xs text-gray-400">
                      {t('instances.groupCount', { live, total: rows.length })}
                    </span>
                  </button>
                  {!isCollapsed && (
                    <div className="overflow-x-auto">
                      <table className="w-full">
                        <thead>{columns}</thead>
                        <tbody>
                          {rows.map((instance) => (
                            <InstanceRow key={instance.instance_id} instance={instance}
                                         showAgent={false} onStop={handleStop}
                                         busy={!!busyIds[instance.instance_id]} t={t} />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>{columns}</thead>
              <tbody>
                {items.map((instance) => (
                  <InstanceRow key={instance.instance_id} instance={instance}
                               showAgent={showAgent} onStop={handleStop}
                               busy={!!busyIds[instance.instance_id]} t={t} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {hasMore && (
          <button
            type="button"
            onClick={() => fetchPage(offset + PAGE_SIZE, true)}
            className="w-full py-2.5 text-sm text-indigo-600 hover:bg-indigo-50 border-t border-gray-100"
          >
            {t('instances.loadMore', { count: total - items.length })}
          </button>
        )}
      </div>
    </div>
  );
}
