import { ArrowDownWideNarrow, ArrowUpNarrowWide, Loader } from 'lucide-react';

import { useI18n } from '../i18n';
import { MAX_LOADED, PAGE_SIZES } from './listPagingState';

/* The controls and the load-more button for useListPaging (./listPagingState.js). */

const FIELD = 'border border-gray-200 rounded-lg text-sm text-gray-700 bg-white focus:outline-none';
const LABEL = 'block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider';

/** Page size and sort pickers for a filter bar. `options` are sort keys,
 * labelled from listPaging.fields unless `labels` names them. `compact` drops
 * the labels and shrinks the fields for an inline toolbar. */
export function ListPagingControls({ paging, options, labels = {}, compact = false }) {
  const { t } = useI18n();
  const pad = compact ? 'px-2 py-1.5' : 'px-3 py-2';
  const OrderIcon = paging.order === 'asc' ? ArrowUpNarrowWide : ArrowDownWideNarrow;
  const sortPicker = (
    <div className="flex items-stretch gap-1">
      <select
        aria-label={t('listPaging.sortBy')}
        className={`${FIELD} ${pad} ${compact ? '' : 'flex-1 min-w-0'}`}
        value={paging.sort}
        onChange={(e) => paging.setSort(e.target.value)}
      >
        {options.map((key) => (
          <option key={key} value={key}>{labels[key] || t(`listPaging.fields.${key}`)}</option>
        ))}
      </select>
      <button
        type="button"
        onClick={paging.toggleOrder}
        title={paging.order === 'asc' ? t('listPaging.ascending') : t('listPaging.descending')}
        aria-label={paging.order === 'asc' ? t('listPaging.ascending') : t('listPaging.descending')}
        className={`${compact ? 'px-1.5' : 'px-2'} border border-gray-200 rounded-lg bg-white text-gray-500 hover:bg-gray-50`}
      >
        <OrderIcon className="w-4 h-4" />
      </button>
    </div>
  );
  const sizePicker = (
    <select
      aria-label={t('listPaging.pageSize')}
      className={`${FIELD} ${pad} ${compact ? '' : 'w-full'}`}
      value={paging.pageSize}
      onChange={(e) => paging.setPageSize(e.target.value)}
    >
      {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
    </select>
  );

  if (compact) {
    return <>{sortPicker}{sizePicker}</>;
  }
  return (
    <>
      <div className="flex-1 min-w-[180px]">
        <span className={LABEL}>{t('listPaging.sortBy')}</span>
        {sortPicker}
      </div>
      <div className="w-24">
        <span className={LABEL}>{t('listPaging.pageSize')}</span>
        {sizePicker}
      </div>
    </>
  );
}

/** Under a list: how many rows are shown and a button that loads the next
 * batch of the chosen size. */
export function ListLoadMore({ paging, shown, total, loading = false }) {
  const { t } = useI18n();
  if (!total) return null;
  const remaining = Math.max(0, total - shown);
  const canLoad = remaining > 0 && paging.limit < MAX_LOADED;
  return (
    <div className="flex flex-col items-center gap-2">
      {canLoad && (
        <button
          type="button"
          onClick={paging.loadMore}
          disabled={loading}
          className="inline-flex items-center gap-2 px-4 py-2 text-sm text-indigo-600 bg-white border border-gray-200 rounded-lg hover:bg-indigo-50 disabled:opacity-60"
        >
          {loading && <Loader className="w-4 h-4 animate-spin" />}
          {t('listPaging.loadMore', { count: Math.min(paging.pageSize, remaining) })}
        </button>
      )}
      <p className="text-xs text-gray-400">{t('listPaging.shown', { shown, total })}</p>
    </div>
  );
}
