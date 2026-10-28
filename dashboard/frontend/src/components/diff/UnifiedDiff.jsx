import { Fragment, useMemo } from 'react';
import { parseUnifiedDiff, withWordMarks } from './diffRows';

/**
 * A unified diff (the text `difflib.unified_diff` or `git diff` prints) drawn
 * as a table: old and new line numbers, the sign, and the line, with removed
 * and added lines tinted. A run of removed lines followed by a run of added
 * ones is paired line by line, and the words that changed inside a pair are
 * marked, so a one word edit in a long prompt paragraph is easy to spot.
 *
 * The `---` / `+++` file headers are dropped (the caller names the part), and
 * the hunk headers become a thin separator with the hunk's line range.
 */

const ROW_STYLE = {
  ctx: { row: '', gutter: 'text-gray-400', sign: 'text-gray-300', mark: '' },
  del: { row: 'bg-red-50', gutter: 'text-red-400 bg-red-50', sign: 'text-red-500', mark: 'bg-red-200 rounded-sm' },
  add: { row: 'bg-emerald-50', gutter: 'text-emerald-500 bg-emerald-50', sign: 'text-emerald-600', mark: 'bg-emerald-200 rounded-sm' },
};

export default function UnifiedDiff({ diff, rows: givenRows, className = '' }) {
  const rows = useMemo(() => withWordMarks(givenRows || parseUnifiedDiff(diff)), [diff, givenRows]);
  return (
    <div className={`rounded-lg border border-gray-200 bg-white overflow-hidden ${className}`}>
      <table className="w-full table-fixed border-collapse font-mono text-[12px] leading-5">
        <colgroup>
          <col className="w-10" />
          <col className="w-10" />
          <col className="w-5" />
          <col />
        </colgroup>
        <tbody>
          {rows.map((r, i) => {
            if (r.type === 'hunk') {
              return (
                <tr key={i} className="bg-gray-50 border-y border-gray-100 first:border-t-0">
                  <td colSpan={3} className="px-2 py-0.5 text-center text-gray-300 select-none">⋯</td>
                  <td className="px-2 py-0.5 text-gray-400 truncate">
                    {r.text.replace(/ @@.*$/, ' @@')}
                    {r.context && <span className="ml-2 text-gray-500">{r.context}</span>}
                  </td>
                </tr>
              );
            }
            const st = ROW_STYLE[r.type];
            return (
              <tr key={i} className={st.row}>
                <td className={`px-2 text-right align-top select-none tabular-nums ${st.gutter}`}>{r.type === 'add' ? '' : r.old}</td>
                <td className={`px-2 text-right align-top select-none tabular-nums border-r border-gray-100 ${st.gutter}`}>{r.type === 'del' ? '' : r.new}</td>
                <td className={`pl-1.5 align-top select-none ${st.sign}`}>{r.type === 'add' ? '+' : r.type === 'del' ? '−' : ''}</td>
                <td className="pr-3 align-top whitespace-pre-wrap break-words text-gray-800">
                  {r.parts
                    ? r.parts.map((p, k) => (
                      <Fragment key={k}>{p.changed ? <span className={st.mark}>{p.text}</span> : p.text}</Fragment>
                    ))
                    : (r.text || ' ')}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
