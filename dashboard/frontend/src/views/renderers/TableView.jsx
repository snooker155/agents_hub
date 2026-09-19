import React, { useMemo, useState } from 'react';
import { ArrowUpDown, ArrowUp, ArrowDown, Search, Download, ChevronLeft, ChevronRight } from 'lucide-react';
import { safeEval } from '../runtimes/expr';
import { useI18n } from '../../i18n';

// Table renderer — sortable, filterable, paginated, CSV-exportable;
// dependency-free. Columns may be strings or objects ({name} or {key,label},
// optionally {computed,formula} evaluated against the row); rows may be arrays
// (positional) or objects (keyed by column key/name). Large tables can carry
// rows via the envelope's data.inline.

const PAGE_SIZE = 50;

function columnName(col) {
  return typeof col === 'string' ? col : (col?.label ?? col?.name ?? col?.key ?? '');
}

function columnKey(col) {
  return typeof col === 'string' ? col : (col?.key ?? col?.name ?? '');
}

function cellValue(row, col, index) {
  if (col && typeof col === 'object' && col.computed && col.formula && !Array.isArray(row)) {
    const v = safeEval(col.formula, row);
    return Number.isFinite(v) ? v : undefined;
  }
  if (Array.isArray(row)) return row[index];
  return row?.[columnKey(col)];
}

function formatCell(value) {
  if (value == null) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function toCsv(columns, rows) {
  const esc = (v) => {
    const s = formatCell(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map((c) => esc(columnName(c))).join(',');
  const body = rows.map((r) => columns.map((c, i) => esc(cellValue(r, c, i))).join(','));
  return [head, ...body].join('\n');
}

export default function TableView({ view }) {
  const { t } = useI18n();
  // Memoised so the `|| []` fallback does not re-sort every row on each render.
  const columns = useMemo(() => view?.spec?.columns || [], [view]);
  const rows = useMemo(() => {
    const inline = view?.data?.inline;
    if (Array.isArray(inline)) return inline;
    return view?.spec?.rows || [];
  }, [view]);

  const [sort, setSort] = useState({ index: null, dir: 1 });
  const [filter, setFilter] = useState('');
  const [page, setPage] = useState(0);

  const filteredRows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => columns.some((c, i) => formatCell(cellValue(r, c, i)).toLowerCase().includes(q)));
  }, [rows, columns, filter]);

  const sortedRows = useMemo(() => {
    if (sort.index == null) return filteredRows;
    const col = columns[sort.index];
    const copy = [...filteredRows];
    copy.sort((a, b) => {
      const av = cellValue(a, col, sort.index);
      const bv = cellValue(b, col, sort.index);
      if (av == null) return 1;
      if (bv == null) return -1;
      const an = typeof av === 'number' ? av : parseFloat(av);
      const bn = typeof bv === 'number' ? bv : parseFloat(bv);
      if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * sort.dir;
      return String(av).localeCompare(String(bv)) * sort.dir;
    });
    return copy;
  }, [filteredRows, columns, sort]);

  const pages = Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const pageRows = sortedRows.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);

  const toggleSort = (index) =>
    setSort((s) => (s.index === index ? { index, dir: -s.dir } : { index, dir: 1 }));

  const exportCsv = () => {
    const blob = new Blob([toCsv(columns, sortedRows)], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(view?.title || 'table').replace(/[^\w-]+/g, '_')}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (!columns.length) {
    return <div className="text-sm text-gray-500">Table spec missing `columns`.</div>;
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <div className="relative flex-1 max-w-xs">
          <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={filter}
            onChange={(e) => { setFilter(e.target.value); setPage(0); }}
            placeholder={t('viewTableView.filterRows')}
            className="w-full pl-7 pr-2 py-1 text-sm rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800"
          />
        </div>
        <span className="text-xs text-gray-400">
          {filter ? `${sortedRows.length} of ${rows.length}` : `${rows.length}`} rows
        </span>
        <button
          onClick={exportCsv}
          title={t('viewTableView.exportCsv')}
          className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          <Download className="w-3.5 h-3.5" /> {t('viewTableView.csv')}
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>
              {columns.map((col, i) => {
                const active = sort.index === i;
                const Icon = !active ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
                return (
                  <th
                    key={i}
                    onClick={() => toggleSort(i)}
                    className="px-3 py-2 text-left font-semibold text-gray-700 dark:text-gray-200 border-b border-gray-200 dark:border-gray-700 cursor-pointer select-none whitespace-nowrap"
                  >
                    <span className="inline-flex items-center gap-1">
                      {columnName(col)}
                      <Icon className={`w-3 h-3 ${active ? 'text-indigo-600' : 'text-gray-300'}`} />
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, ri) => (
              <tr key={ri} className={ri % 2 === 0 ? 'bg-white dark:bg-gray-900' : 'bg-gray-50 dark:bg-gray-800/50'}>
                {columns.map((col, ci) => (
                  <td key={ci} className="px-3 py-2 text-gray-700 dark:text-gray-300 border-b border-gray-100 dark:border-gray-800 whitespace-nowrap">
                    {formatCell(cellValue(row, col, ci))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!sortedRows.length && <div className="text-sm text-gray-400 py-3">{filter ? 'No matching rows.' : 'No rows.'}</div>}
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 mt-2 text-xs text-gray-500">
          <button onClick={() => setPage(Math.max(0, current - 1))} disabled={current === 0}
            className="p-1 rounded border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800">
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <span>page {current + 1} / {pages}</span>
          <button onClick={() => setPage(Math.min(pages - 1, current + 1))} disabled={current >= pages - 1}
            className="p-1 rounded border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800">
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
