/**
 * What a task waits for, small enough for a board card or a subtask row: one
 * chip per task it depends on, amber while that task is not done, grey with a
 * check once it is. Each chip opens the task it names.
 */
import { Check, Hourglass } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useI18n, statusLabel } from '../../i18n';

/**
 * @param {object} props
 * @param {string[]} props.ids        the task's `depends`.
 * @param {Map<string, object>} props.byId  the tasks known here, by id.
 * @param {object} [props.linkState]  router state the links carry on.
 * @param {number} [props.max]        chips shown before "+N".
 */
export default function DependencyChips({ ids, byId, linkState, max = 3, className = '' }) {
  const { t } = useI18n();
  if (!ids || ids.length === 0) return null;
  const deps = ids.map((id) => byId.get(id) || { id, title: id.slice(0, 8) });
  const pending = deps.filter((d) => d.status !== 'done').length;
  const shown = deps.slice(0, max);
  const rest = deps.length - shown.length;
  return (
    <span className={`inline-flex items-center gap-1 flex-wrap min-w-0 ${className}`} data-testid="dependency-chips">
      <span className={`inline-flex items-center gap-0.5 text-xs ${pending ? 'text-amber-600' : 'text-gray-400'}`}>
        <Hourglass className="w-3 h-3" />
        {pending ? t('taskDetails.waitsFor') : t('taskDetails.waitedFor')}
      </span>
      {shown.map((d) => {
        const done = d.status === 'done';
        return (
          <Link
            key={d.id}
            to={`/tasks/${d.id}`}
            state={linkState}
            onClick={(e) => e.stopPropagation()}
            title={`${d.title}${d.status ? ` · ${statusLabel(d.status, t)}` : ''}`}
            data-done={done}
            className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs border max-w-[140px] ${
              done
                ? 'bg-gray-50 text-gray-400 border-gray-200 hover:text-gray-600'
                : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'
            }`}
          >
            {done && <Check className="w-3 h-3 flex-shrink-0" />}
            <span className="truncate">{d.key || d.title}</span>
          </Link>
        );
      })}
      {rest > 0 && <span className="text-xs text-gray-400">+{rest}</span>}
    </span>
  );
}
