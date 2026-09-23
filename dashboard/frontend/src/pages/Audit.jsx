import React, { useCallback, useEffect, useState } from 'react';
import {
  ChevronDown, ChevronLeft, ChevronRight, Download, History, Loader, RefreshCw,
} from 'lucide-react';
import { getAuditLog, getAuditActions, auditExportUrl, getWorkspaces } from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import DateInput from '../components/DateInput';
import { useI18n } from '../i18n';

const inputCls = 'border border-gray-200 rounded-lg px-3 py-2 text-sm w-full '
  + 'focus:ring-2 focus:ring-indigo-500 focus:outline-none';

const PAGE_SIZE = 50;

const EMPTY_FILTERS = {
  actor: '', action: '', workspace: '', since: '', until: '', text: '', result: '',
};

/**
 * The audit trail (docs/audit.md): who did what, when, to which object.
 *
 * Read-only. Who sees which rows is decided entirely on the backend: an
 * administrator reads everything, everyone else reads their own actions plus
 * the rows of a workspace they own. This page renders whatever
 * `GET /api/audit` hands back and never tries to second-guess the scope.
 */
export default function Audit() {
  const { t } = useI18n();
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [actions, setActions] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [openRow, setOpenRow] = useState(null);

  const load = useCallback(async (nextOffset) => {
    setLoading(true);
    try {
      const params = { ...filters, limit: PAGE_SIZE, offset: nextOffset };
      Object.keys(params).forEach((k) => { if (params[k] === '') delete params[k]; });
      const { data } = await getAuditLog(params);
      setRows(Array.isArray(data.items) ? data.items : []);
      setTotal(Number(data.total) || 0);
      setOffset(nextOffset);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('audit.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [filters, t]);

  // A filter change always restarts at the first page: an offset kept from
  // the previous filter set would land on an arbitrary, unrelated page.
  useEffect(() => { load(0); }, [load]);

  useEffect(() => {
    getAuditActions().then(({ data }) => setActions(Array.isArray(data) ? data : [])).catch(() => {});
    getWorkspaces().then(({ data }) => setWorkspaces(Array.isArray(data) ? data : [])).catch(() => {});
  }, []);

  const setField = (key) => (value) => setFilters((f) => ({ ...f, [key]: value }));

  const clearFilters = () => setFilters(EMPTY_FILTERS);

  const canPrev = offset > 0;
  const canNext = offset + rows.length < total;

  return (
    <PageContainer>
      <PageHeader
        icon={History}
        title={t('audit.title')}
        description={t('audit.description')}
        actions={(
          <>
            <a
              href={auditExportUrl(filters, 'csv')}
              target="_blank" rel="noreferrer"
              className="flex items-center gap-1.5 border border-gray-200 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700"
            >
              <Download className="w-3.5 h-3.5" /> {t('audit.exportCsv')}
            </a>
            <a
              href={auditExportUrl(filters, 'jsonl')}
              target="_blank" rel="noreferrer"
              className="flex items-center gap-1.5 border border-gray-200 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700"
            >
              <Download className="w-3.5 h-3.5" /> {t('audit.exportJsonl')}
            </a>
            <button type="button" onClick={() => load(offset)}
              className="flex items-center gap-1.5 border border-gray-200 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700">
              <RefreshCw className="w-3.5 h-3.5" /> {t('common.refresh')}
            </button>
          </>
        )}
      />

      {error && (
        <p className="mb-4 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      <div className="mb-4 bg-white border border-gray-200 rounded-xl p-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.actor')}</label>
          <input value={filters.actor} onChange={(e) => setField('actor')(e.target.value)}
            placeholder={t('audit.filters.actorPlaceholder')} className={inputCls} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.action')}</label>
          <select value={filters.action} onChange={(e) => setField('action')(e.target.value)} className={inputCls}>
            <option value="">{t('audit.filters.allActions')}</option>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.workspace')}</label>
          <select value={filters.workspace} onChange={(e) => setField('workspace')(e.target.value)} className={inputCls}>
            <option value="">{t('audit.filters.allWorkspaces')}</option>
            {workspaces.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.result')}</label>
          <select value={filters.result} onChange={(e) => setField('result')(e.target.value)} className={inputCls}>
            <option value="">{t('audit.filters.allResults')}</option>
            <option value="ok">{t('audit.results.ok')}</option>
            <option value="denied">{t('audit.results.denied')}</option>
            <option value="error">{t('audit.results.error')}</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.since')}</label>
          <DateInput mode="datetime" valueFormat="iso" className={inputCls}
            value={filters.since} onChange={setField('since')} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.until')}</label>
          <DateInput mode="datetime" valueFormat="iso" className={inputCls}
            value={filters.until} onChange={setField('until')} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('audit.filters.text')}</label>
          <input value={filters.text} onChange={(e) => setField('text')(e.target.value)}
            placeholder={t('audit.filters.textPlaceholder')} className={inputCls} />
        </div>
        <div className="col-span-2 sm:col-span-3 lg:col-span-7 flex justify-end">
          <button type="button" onClick={clearFilters}
            className="text-xs font-medium text-gray-500 hover:text-gray-800">
            {t('audit.filters.clear')}
          </button>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        {loading ? (
          <p className="p-6 text-sm text-gray-500 flex items-center gap-2">
            <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
          </p>
        ) : rows.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">{t('audit.empty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-400">
                <tr>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.time')}</th>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.actor')}</th>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.action')}</th>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.object')}</th>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.workspace')}</th>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.result')}</th>
                  <th className="text-left px-4 py-2 font-semibold">{t('audit.columns.ip')}</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <React.Fragment key={row.id}>
                    <tr className="border-t border-gray-100">
                      <td className="px-4 py-2 whitespace-nowrap text-gray-500 text-xs">{row.at}</td>
                      <td className="px-4 py-2 text-gray-800">
                        {row.actor_name || row.actor_id || t('audit.system')}
                        {row.actor_kind && (
                          <span className="ml-1.5 text-xs text-gray-400">({row.actor_kind})</span>
                        )}
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-indigo-700">{row.action}</td>
                      <td className="px-4 py-2 text-gray-600 text-xs">
                        {row.object_type ? `${row.object_type}:${row.object_id ?? ''}` : (row.path || '—')}
                      </td>
                      <td className="px-4 py-2 text-gray-600 text-xs">{row.workspace || '—'}</td>
                      <td className="px-4 py-2 text-xs">
                        <span className={row.result === 'ok' || row.result === '200'
                          ? 'text-green-600' : 'text-red-600'}>
                          {row.result}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-gray-500 text-xs">{row.ip || '—'}</td>
                      <td className="px-4 py-2 text-right">
                        <button type="button"
                          onClick={() => setOpenRow((id) => (id === row.id ? null : row.id))}
                          className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800">
                          <ChevronDown className={`w-3.5 h-3.5 transition-transform ${openRow === row.id ? 'rotate-180' : ''}`} />
                          {t('audit.details')}
                        </button>
                      </td>
                    </tr>
                    {openRow === row.id && (
                      <tr className="border-t border-gray-100 bg-gray-50">
                        <td colSpan={8} className="px-4 py-3">
                          <pre className="text-xs text-gray-700 whitespace-pre-wrap break-all">
                            {JSON.stringify(row.details || {}, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {total > 0 && (
        <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
          <span>{t('audit.pagination.showing', { from: offset + 1, to: offset + rows.length, total })}</span>
          <div className="flex items-center gap-2">
            <button type="button" disabled={!canPrev} onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
              className="inline-flex items-center gap-1 border border-gray-200 rounded-lg px-2 py-1 disabled:opacity-40 hover:bg-gray-50">
              <ChevronLeft className="w-3.5 h-3.5" /> {t('audit.pagination.prev')}
            </button>
            <button type="button" disabled={!canNext} onClick={() => load(offset + PAGE_SIZE)}
              className="inline-flex items-center gap-1 border border-gray-200 rounded-lg px-2 py-1 disabled:opacity-40 hover:bg-gray-50">
              {t('audit.pagination.next')} <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
