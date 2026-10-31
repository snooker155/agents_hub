/** The Cluster page's card and plain table, shared by its parts. */
import { useI18n } from '../../i18n';
import { InfoHint } from '../PageLayout';

/** ``aside`` sits on the title row, at its right end (a legend, a filter). */
export function Card({ icon: Icon, title, hint, aside, children }) {
  const { t } = useI18n();
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 mb-3">
        <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2">
          <Icon className="w-4 h-4 text-indigo-500" /> {title}
          {hint && <InfoHint text={hint} label={t('common.info')} className="-ml-1 font-normal" />}
        </h3>
        {aside}
      </div>
      {children}
    </div>
  );
}

export function Table({ columns, rows, empty }) {
  if (!rows.length) return <p className="text-sm text-gray-500 py-2">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
            {columns.map((c) => <th key={c.key} className="py-1.5 pr-3 font-semibold">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.key || i} className="border-b border-gray-50 last:border-0 align-top">
              {columns.map((c) => (
                <td key={c.key} className="py-1.5 pr-3 text-gray-800">{c.render ? c.render(row) : row[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
