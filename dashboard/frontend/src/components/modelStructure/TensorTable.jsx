import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Search } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';
import { DTYPE_COLOR_SPEC, dtypeColorKey, formatBytes, formatCount } from './graph';

// The tensors of the selected block: sortable by name, parameters or bytes,
// filtered by a substring of the name, dtype or role.

const SORTABLE = ['name', 'parameters', 'bytes'];

function SortHeader({ column, sort, onSort, label, align = 'left' }) {
  const { t } = useI18n();
  const active = sort.key === column;
  const Icon = sort.dir === 'asc' ? ArrowUp : ArrowDown;
  return (
    <th className={`px-3 py-2 font-medium ${align === 'right' ? 'text-right' : 'text-left'}`}>
      <button
        type="button"
        onClick={() => onSort(column)}
        title={t('modelStructure.table.sortBy', { column: label })}
        className={`inline-flex items-center gap-1 hover:text-indigo-600 ${active ? 'text-indigo-600' : ''}`}
      >
        {label}
        {active && <Icon className="h-3 w-3" />}
      </button>
    </th>
  );
}

export default function TensorTable({ block }) {
  const { t } = useI18n();
  const colors = useThemeColors(DTYPE_COLOR_SPEC);
  const [sort, setSort] = useState({ key: 'bytes', dir: 'desc' });
  const [filter, setFilter] = useState('');

  const tensors = useMemo(() => block?.tensors || [], [block]);
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const matched = q
      ? tensors.filter((tn) => [tn.name, tn.dtype, tn.role].some((v) => v && String(v).toLowerCase().includes(q)))
      : tensors;
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...matched].sort((a, b) => {
      if (sort.key === 'name') return dir * String(a.name || '').localeCompare(String(b.name || ''), undefined, { numeric: true });
      return dir * ((Number(a[sort.key]) || 0) - (Number(b[sort.key]) || 0));
    });
  }, [tensors, filter, sort]);

  const onSort = (key) => {
    if (!SORTABLE.includes(key)) return;
    setSort((s) => (s.key === key
      ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: key === 'name' ? 'asc' : 'desc' }));
  };

  if (!block) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500">
        {t('modelStructure.table.selectHint')}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-gray-200 bg-white" data-testid="tensor-table">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">
            {t('modelStructure.table.title', { block: block.label })}
          </h2>
          <p className="text-xs text-gray-500">
            {t('modelStructure.table.count', { shown: rows.length, total: tensors.length })}
          </p>
        </div>
        <label className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('modelStructure.table.filter')}
            aria-label={t('modelStructure.table.filter')}
            className="w-56 rounded-md border border-gray-300 bg-white py-1.5 pl-7 pr-2 text-sm focus:outline-none"
          />
        </label>
      </div>
      {tensors.length === 0 ? (
        <p className="p-4 text-sm text-gray-500">{t('modelStructure.table.none')}</p>
      ) : rows.length === 0 ? (
        <p className="p-4 text-sm text-gray-500">{t('modelStructure.table.noMatch')}</p>
      ) : (
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 text-xs uppercase tracking-wider text-gray-500">
              <tr>
                <SortHeader column="name" sort={sort} onSort={onSort} label={t('modelStructure.table.name')} />
                <th className="px-3 py-2 text-left font-medium">{t('modelStructure.table.shape')}</th>
                <th className="px-3 py-2 text-left font-medium">{t('modelStructure.table.dtype')}</th>
                <SortHeader column="parameters" sort={sort} onSort={onSort} label={t('modelStructure.table.parameters')} align="right" />
                <SortHeader column="bytes" sort={sort} onSort={onSort} label={t('modelStructure.table.bytes')} align="right" />
                <th className="px-3 py-2 text-left font-medium">{t('modelStructure.table.role')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((tn) => (
                <tr key={tn.name} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 font-mono text-xs text-gray-800">{tn.name}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-gray-600">
                    {Array.isArray(tn.shape) ? `[${tn.shape.join(', ')}]` : ''}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-gray-700">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-sm" style={{ background: colors[dtypeColorKey(tn.dtype)] }} />
                      {tn.dtype}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-gray-700" title={String(tn.parameters ?? '')}>
                    {formatCount(tn.parameters)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-gray-700" title={String(tn.bytes ?? '')}>
                    {formatBytes(tn.bytes)}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-gray-500">{tn.role || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
