import { useState, useEffect } from 'react';
import {
  Globe,
  Search,
  Link2,
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  Trash2,
  RefreshCw,
  X,
  FileText,
  Bot,
  Folder,
  EyeOff,
  ArrowRight,
} from 'lucide-react';
import { getWebLogs, getWebLogStats, getWebLogEntry, clearWebLogs } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const SEVERITY_STYLES = {
  high: 'bg-red-50 text-red-700 border-red-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  low: 'bg-sky-50 text-sky-700 border-sky-200',
  none: 'bg-gray-50 text-gray-500 border-gray-200',
};

const STATUS_STYLES = {
  ok: 'bg-green-50 text-green-700 border-green-200',
  refused: 'bg-orange-50 text-orange-700 border-orange-200',
  error: 'bg-red-50 text-red-700 border-red-200',
  unsupported: 'bg-gray-50 text-gray-600 border-gray-200',
  not_configured: 'bg-gray-50 text-gray-600 border-gray-200',
};

const Badge = ({ className = '', children, title }) => (
  <span title={title} className={`text-[10px] px-2 py-0.5 rounded border font-semibold uppercase ${className}`}>
    {children}
  </span>
);

/**
 * Web access log.
 *
 * Every web_search and fetch_url call, with the text the agent actually
 * received. Two questions this page answers: did the tool read the response
 * correctly (extraction is lossy — HTML stripped, JSON re-serialised, text
 * truncated), and was the response trying something (injection phrasing,
 * instructions hidden from human readers, envelope escapes, credential leaks).
 * Flags are signals for review, not blocks — enforcement lives in the SSRF
 * guard, the domain policy and the capability model.
 */
const WebLogs = () => {
  const { t } = useI18n();
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [entry, setEntry] = useState(null);
  const [entryLoading, setEntryLoading] = useState(false);
  const [filters, setFilters] = useState({ kind: '', status: '', min_severity: '', search: '' });
  const [search, setSearch] = useState('');

  const fetchData = async (next = filters) => {
    try {
      const params = { limit: 200 };
      Object.entries(next).forEach(([k, v]) => { if (v) params[k] = v; });
      const [logs, s] = await Promise.all([getWebLogs(params), getWebLogStats()]);
      setRows(logs.data.items || []);
      setTotal(logs.data.total || 0);
      setStats(s.data);
    } catch (error) {
      console.error('Error fetching web logs:', error);
    } finally {
      setLoading(false);
    }
  };

  // Initial load only. Filter changes call fetchData(next) directly with the
  // new filters, so depending on it here would fetch every page twice.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { fetchData(); }, []);

  const applyFilter = (patch) => {
    const next = { ...filters, ...patch };
    setFilters(next);
    setLoading(true);
    fetchData(next);
  };

  const submitSearch = (e) => {
    e.preventDefault();
    applyFilter({ search });
  };

  const openEntry = async (id) => {
    setEntryLoading(true);
    try {
      const { data } = await getWebLogEntry(id);
      setEntry(data);
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setEntryLoading(false);
    }
  };

  const handleClear = async () => {
    if (!window.confirm(t('webLogs.confirmClear'))) return;
    try {
      const { data } = await clearWebLogs();
      setEntry(null);
      setLoading(true);
      await fetchData();
      alert(t('webLogs.clearedEntries', { count: data.deleted }));
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    }
  };

  const target = (row) => row.kind === 'search' ? row.query : (row.final_url || row.url);
  const when = (ts) => { try { return new Date(ts).toLocaleString(); } catch { return ts; } };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Globe}
        title={t('webLogs.webRequests')}
        description={<>
          {t('webLogs.descriptionBefore')}{' '}
          <code className="text-xs bg-gray-100 px-1 rounded">web_search</code>{' '}
          {t('webLogs.descriptionAnd')}{' '}
          <code className="text-xs bg-gray-100 px-1 rounded">fetch_url</code>{' '}
          {t('webLogs.descriptionAfter')}
        </>}
        actions={<>
          <button
            onClick={() => { setLoading(true); fetchData(); }}
            className="inline-flex items-center px-3 py-2 text-sm font-semibold border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4 mr-1.5" /> {t('webLogs.refresh')}
          </button>
          <button
            onClick={handleClear}
            className="inline-flex items-center px-3 py-2 text-sm font-semibold border border-red-200 text-red-600 rounded-lg hover:bg-red-50"
          >
            <Trash2 className="w-4 h-4 mr-1.5" /> {t('webLogs.clearLog')}
          </button>
        </>}
      />

      {stats && !stats.enabled && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded-xl px-4 py-3">
          {t('webLogs.loggingOffBefore')} (<code className="text-xs">WEB_LOG_ENABLED=false</code>).{' '}
          {t('webLogs.loggingOffAfter')}
        </div>
      )}

      {/* Counters */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          {[
            { label: t('webLogs.cards.callsRecorded'), value: stats.total, icon: Globe, tone: 'text-indigo-600' },
            { label: t('webLogs.cards.searches'), value: stats.by_kind?.search || 0, icon: Search, tone: 'text-gray-600' },
            { label: t('webLogs.cards.fetches'), value: stats.by_kind?.fetch || 0, icon: Link2, tone: 'text-gray-600' },
            {
              label: t('webLogs.cards.failedOrRefused'),
              value: (stats.by_status?.error || 0) + (stats.by_status?.refused || 0),
              icon: AlertTriangle,
              tone: 'text-orange-600',
            },
            { label: t('webLogs.cards.flagged'), value: stats.flagged || 0, icon: ShieldAlert, tone: 'text-red-600' },
          ].map((card) => {
            const Icon = card.icon;
            return (
              <div key={card.label} className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
                <div className="flex items-center gap-2 text-[11px] uppercase font-semibold text-gray-400">
                  <Icon className={`w-3.5 h-3.5 ${card.tone}`} /> {card.label}
                </div>
                <div className="text-2xl font-bold text-gray-800 mt-1">{card.value}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm flex flex-wrap items-center gap-3">
        <select
          value={filters.kind}
          onChange={(e) => applyFilter({ kind: e.target.value })}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
        >
          <option value="">{t('webLogs.allCalls')}</option>
          <option value="search">{t('webLogs.searches')}</option>
          <option value="fetch">{t('webLogs.fetches')}</option>
        </select>
        <select
          value={filters.status}
          onChange={(e) => applyFilter({ status: e.target.value })}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
        >
          <option value="">{t('webLogs.anyOutcome')}</option>
          <option value="ok">{t('webLogs.succeeded')}</option>
          <option value="refused">{t('webLogs.refusedByPolicy')}</option>
          <option value="error">{t('webLogs.errored')}</option>
          <option value="unsupported">{t('webLogs.unsupportedType')}</option>
          <option value="not_configured">{t('webLogs.notConfigured')}</option>
        </select>
        <select
          value={filters.min_severity}
          onChange={(e) => applyFilter({ min_severity: e.target.value })}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
        >
          <option value="">{t('webLogs.anySeverity')}</option>
          <option value="low">{t('webLogs.flaggedLowAndUp')}</option>
          <option value="medium">{t('webLogs.mediumAndUp')}</option>
          <option value="high">{t('webLogs.highOnly')}</option>
        </select>
        <form onSubmit={submitSearch} className="relative flex-1 min-w-[220px]">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('webLogs.searchQueryUrlAgentOr')}
            className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          />
        </form>
        <span className="text-xs text-gray-400">{t('webLogs.matching', { count: total })}</span>
      </div>

      {/* Rows */}
      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
          <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
          <p className="text-gray-500 font-medium">{t('webLogs.loadingWebCalls')}</p>
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
          <Globe className="w-10 h-10 text-gray-300 mb-3" />
          <p className="text-gray-500 font-medium">{t('webLogs.noWebCallsRecordedYet')}</p>
          <p className="text-gray-400 text-sm mt-1 max-w-md text-center">
            {t('webLogs.callsAppearHereAsSoon')}
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-400">
              <tr>
                <th className="text-left font-semibold px-4 py-2">{t('webLogs.when')}</th>
                <th className="text-left font-semibold px-4 py-2">{t('webLogs.call')}</th>
                <th className="text-left font-semibold px-4 py-2">{t('webLogs.caller')}</th>
                <th className="text-left font-semibold px-4 py-2">{t('webLogs.outcome')}</th>
                <th className="text-left font-semibold px-4 py-2">{t('webLogs.review')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => openEntry(row.id)}
                  className="hover:bg-indigo-50/40 cursor-pointer"
                >
                  <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{when(row.ts)}</td>
                  <td className="px-4 py-2 max-w-[420px]">
                    <div className="flex items-center gap-2">
                      {row.kind === 'search'
                        ? <Search className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                        : <Link2 className="w-3.5 h-3.5 text-gray-400 shrink-0" />}
                      <span className="truncate text-gray-800" title={target(row)}>{target(row) || '—'}</span>
                    </div>
                    <div className="text-[11px] text-gray-400 pl-5">
                      {row.kind === 'search'
                        ? `${row.provider || t('webLogs.noProvider')} · ${t('webLogs.resultCount', { count: row.result_count ?? 0 })}${row.cache_hit ? ` · ${t('webLogs.cached')}` : ''}`
                        : `${row.content_type || t('webLogs.unknownType')} · ${t('webLogs.charCount', { count: row.body_chars })}${row.truncated ? ` ${t('webLogs.truncatedSuffix')}` : ''}`}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">
                    {row.agent_id ? (
                      <span className="inline-flex items-center gap-1"><Bot className="w-3 h-3" /> {row.agent_id}</span>
                    ) : <span className="text-gray-300">—</span>}
                    {row.workspace && (
                      <span className="inline-flex items-center gap-1 ml-2 text-gray-400">
                        <Folder className="w-3 h-3" /> {row.workspace}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    <Badge className={STATUS_STYLES[row.status] || STATUS_STYLES.unsupported}>
                      {row.status === 'not_configured' ? t('webLogs.notConfiguredStatus') : row.status}
                    </Badge>
                    <span className="text-[11px] text-gray-400 ml-2">
                      {row.http_status ? `${row.http_status} · ` : ''}{row.duration_ms ?? 0} ms
                    </span>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap">
                    {row.max_severity && row.max_severity !== 'none' ? (
                      <Badge
                        className={SEVERITY_STYLES[row.max_severity]}
                        title={(row.flag_codes || []).join(', ')}
                      >
                        {row.max_severity} · {row.flag_count}
                      </Badge>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[11px] text-gray-400">
                        <ShieldCheck className="w-3.5 h-3.5" /> {t('webLogs.clean')}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail drawer */}
      {(entry || entryLoading) && (
        <div className="fixed inset-0 bg-black/40 flex justify-end z-50" onClick={() => setEntry(null)}>
          <div
            className="bg-white w-full max-w-3xl h-full overflow-y-auto shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {entryLoading || !entry ? (
              <div className="flex items-center justify-center h-full">
                <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin" />
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between px-6 py-4 border-b border-gray-100 sticky top-0 bg-white z-10">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      {entry.kind === 'search'
                        ? <Search className="w-4 h-4 text-indigo-600" />
                        : <Link2 className="w-4 h-4 text-indigo-600" />}
                      <h3 className="text-lg font-bold text-gray-800">
                        {entry.kind === 'search' ? t('webLogs.webSearchTitle') : t('webLogs.pageFetchTitle')}
                      </h3>
                      <Badge className={STATUS_STYLES[entry.status] || STATUS_STYLES.unsupported}>
                        {entry.status === 'not_configured' ? t('webLogs.notConfiguredStatus') : entry.status}
                      </Badge>
                      {entry.max_severity && entry.max_severity !== 'none' && (
                        <Badge className={SEVERITY_STYLES[entry.max_severity]}>
                          {t('webLogs.riskLevel', { level: entry.max_severity })}
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-gray-600 break-all">{target(entry)}</p>
                  </div>
                  <button onClick={() => setEntry(null)} className="text-gray-400 hover:text-gray-600 ml-4">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <div className="p-6 space-y-6">
                  {/* Request facts */}
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
                    {[
                      [t('webLogs.facts.when'), when(entry.ts)],
                      [t('webLogs.facts.workspace'), entry.workspace || '—'],
                      [t('webLogs.facts.agent'), entry.agent_id || '—'],
                      [t('webLogs.facts.session'), entry.session_id || '—'],
                      [t('webLogs.facts.task'), entry.task_id || '—'],
                      [t('webLogs.facts.duration'), `${entry.duration_ms ?? 0} ms`],
                      ...(entry.kind === 'search'
                        ? [
                            [t('webLogs.facts.provider'), entry.provider || '—'],
                            [t('webLogs.facts.resultsReturned'), `${entry.result_count ?? 0}`],
                            [t('webLogs.facts.resultsBlocked'), `${entry.blocked_results ?? 0}`],
                            [t('webLogs.facts.servedFromCache'), entry.cache_hit ? t('common.yes') : t('common.no')],
                          ]
                        : [
                            [t('webLogs.facts.httpStatus'), entry.http_status ?? '—'],
                            [t('webLogs.facts.contentType'), entry.content_type || '—'],
                            [t('webLogs.facts.bytesReceived'), entry.response_bytes ?? '—'],
                            [t('webLogs.facts.textExtracted'), `${t('webLogs.charCount', { count: (entry.body || '').length })}${entry.truncated ? ` ${t('webLogs.truncatedSuffix')}` : ''}`],
                          ]),
                    ].map(([label, value]) => (
                      <div key={label}>
                        <div className="text-[11px] uppercase font-semibold text-gray-400">{label}</div>
                        <div className="text-gray-700 break-all">{value}</div>
                      </div>
                    ))}
                  </div>

                  {entry.error && (
                    <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
                      {entry.error}
                    </div>
                  )}

                  {/* Redirect chain */}
                  {(entry.redirects || []).length > 0 && (
                    <div>
                      <h4 className="text-xs uppercase font-semibold text-gray-400 mb-2">
                        {t('webLogs.redirectsEveryHopReChecked')}
                      </h4>
                      <div className="text-xs text-gray-600 space-y-1 bg-gray-50 border border-gray-100 rounded-lg p-3">
                        <div className="break-all">{entry.url}</div>
                        {entry.redirects.map((hop, i) => (
                          <div key={i} className="flex items-start gap-1 break-all">
                            <ArrowRight className="w-3 h-3 mt-0.5 text-gray-400 shrink-0" /> {hop}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Flags */}
                  <div>
                    <h4 className="text-xs uppercase font-semibold text-gray-400 mb-2">
                      {t('webLogs.securityReview')} ({(entry.flags || []).length})
                    </h4>
                    {(entry.flags || []).length === 0 ? (
                      <div className="flex items-center gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-4 py-3">
                        <ShieldCheck className="w-4 h-4 text-green-600" />
                        {t('webLogs.nothingFlaggedHeuristicsOnlyAn')}
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {entry.flags.map((flag, i) => (
                          <div
                            key={i}
                            className={`rounded-lg border px-4 py-3 ${SEVERITY_STYLES[flag.severity] || SEVERITY_STYLES.none}`}
                          >
                            <div className="flex items-center gap-2 mb-1">
                              <ShieldAlert className="w-4 h-4 shrink-0" />
                              <span className="text-xs font-bold uppercase">{flag.severity}</span>
                              <code className="text-xs">{flag.code}</code>
                              <span className="text-[11px] opacity-70">
                                {t('webLogs.flagIn', { where: flag.where })}{flag.count > 1 ? ` · ${t('webLogs.matchCount', { count: flag.count })}` : ''}
                              </span>
                            </div>
                            <div className="text-sm">{flag.detail}</div>
                            {flag.excerpt && (
                              <div className="mt-2 text-xs font-mono bg-white/70 rounded p-2 break-all">
                                {flag.excerpt}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Hidden text */}
                  {entry.hidden_text && (
                    <div>
                      <h4 className="text-xs uppercase font-semibold text-gray-400 mb-2 flex items-center gap-1">
                        <EyeOff className="w-3.5 h-3.5" /> {t('webLogs.textThePageHidFrom')}
                      </h4>
                      <p className="text-xs text-gray-500 mb-2">
                        {t('webLogs.hiddenTextHint')}
                      </p>
                      <pre className="text-xs font-mono bg-gray-900 text-gray-100 rounded-lg p-4 overflow-x-auto max-h-72 whitespace-pre-wrap break-words">
                        {entry.hidden_text}
                      </pre>
                    </div>
                  )}

                  {/* Response */}
                  <div>
                    <h4 className="text-xs uppercase font-semibold text-gray-400 mb-2 flex items-center gap-1">
                      <FileText className="w-3.5 h-3.5" /> {t('webLogs.whatTheAgentRead')}
                    </h4>
                    <p className="text-xs text-gray-500 mb-2">
                      {t('webLogs.responseHint')}
                    </p>
                    <pre className="text-xs font-mono bg-gray-50 border border-gray-100 rounded-lg p-4 overflow-x-auto max-h-[32rem] whitespace-pre-wrap break-words text-gray-700">
                      {entry.body || t('webLogs.emptyBody')}
                    </pre>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </PageContainer>
  );
};

export default WebLogs;
