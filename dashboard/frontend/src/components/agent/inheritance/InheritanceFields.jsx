import { Loader, RotateCcw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useI18n } from '../../../i18n';

/** A field's value, shown in a way that reads at a glance rather than as raw JSON. */
function formatValue(value) {
  if (value === null || value === undefined || value === '') return '·';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '·';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value);
}

/** Who a field's current value comes from: a link to that agent, or "own". */
function SourceBadge({ source, agentId, t }) {
  if (!source || source === agentId) {
    return <span className="text-[11px] text-gray-400">{t('agentInheritance.ownValue')}</span>;
  }
  return (
    <Link to={`/agents/${source}`} className="text-[11px] text-indigo-600 hover:text-indigo-800">
      {source}
    </Link>
  );
}

/**
 * Every inheritable SCALAR_FIELD and MERGED_DICT_FIELD: its current value,
 * where it comes from, and a reset for whichever ones the agent overrides
 * itself (DELETE /agents/{id}/overrides/{field}, agents/inheritance.py).
 * Rendered only for a child (an agent with no parent has nothing to show
 * here: every field is already its own).
 */
export function ScalarFieldsTable({ agentId, fields, onReset, resettingField }) {
  const { t } = useI18n();
  const entries = Object.entries(fields || {});
  if (entries.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-gray-400 border-b border-gray-100">
            <th className="py-2 pr-3 font-semibold">{t('agentInheritance.field')}</th>
            <th className="py-2 pr-3 font-semibold">{t('agentInheritance.value')}</th>
            <th className="py-2 pr-3 font-semibold">{t('agentInheritance.source')}</th>
            <th className="py-2 pr-3 font-semibold" />
          </tr>
        </thead>
        <tbody>
          {entries.map(([field, info]) => {
            const overridden = !!info?.overridden;
            const resetting = resettingField === field;
            return (
              <tr key={field} className="border-b border-gray-50 last:border-0">
                <td className="py-2 pr-3 font-mono text-xs text-gray-700">{field}</td>
                <td className="py-2 pr-3 text-gray-800 break-all max-w-xs">{formatValue(info?.value)}</td>
                <td className="py-2 pr-3">
                  <div className="flex items-center gap-1.5">
                    <SourceBadge source={info?.source} agentId={agentId} t={t} />
                    {overridden && (
                      <span className="text-[10px] uppercase font-semibold text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                        {t('agentInheritance.overridden')}
                      </span>
                    )}
                  </div>
                </td>
                <td className="py-2 pr-3 text-right">
                  {overridden && (
                    <button
                      type="button"
                      onClick={() => onReset(field)}
                      disabled={resetting}
                      className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 disabled:opacity-40"
                      title={t('agentInheritance.resetToInherited')}
                    >
                      {resetting ? <Loader className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
                      {t('agentInheritance.reset')}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Every inheritable LIST_FIELD: the parent's items kept, this agent's own
 * additions marked, and whatever parent items it removed struck through
 * (`list_deltas`, `effective_lists`, agents/inheritance.py).
 */
export function ListFieldsView({ agentId, effectiveLists, listDeltas, onReset, resettingField }) {
  const { t } = useI18n();
  const removedByField = effectiveLists?.removed || {};
  const fieldNames = Object.keys(effectiveLists || {}).filter((k) => k !== 'removed');
  if (fieldNames.length === 0) return null;
  return (
    <div className="space-y-4">
      {fieldNames.map((field) => {
        const items = effectiveLists[field] || [];
        const removed = removedByField[field] || [];
        const delta = listDeltas?.[field];
        const hasDelta = !!(delta && ((delta.add || []).length || (delta.remove || []).length));
        const resetting = resettingField === field;
        if (items.length === 0 && removed.length === 0) return null;
        return (
          <div key={field}>
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-mono text-xs text-gray-600">{field}</span>
              {hasDelta && (
                <button
                  type="button"
                  onClick={() => onReset(field)}
                  disabled={resetting}
                  className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 disabled:opacity-40"
                >
                  {resetting ? <Loader className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
                  {t('agentInheritance.reset')}
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {items.map((entry, i) => {
                const added = !!entry?.added;
                const fromSelf = !entry?.source || entry.source === agentId;
                return (
                  <span
                    key={`${field}-${entry?.value}-${i}`}
                    title={fromSelf ? t('agentInheritance.ownValue') : entry.source}
                    className={`text-xs px-2 py-0.5 rounded-full border ${
                      added
                        ? 'bg-green-50 text-green-700 border-green-200'
                        : 'bg-gray-50 text-gray-600 border-gray-200'
                    }`}
                  >
                    {entry?.value}
                  </span>
                );
              })}
              {removed.map((value, i) => (
                <span
                  key={`${field}-removed-${value}-${i}`}
                  title={t('agentInheritance.removedFromParent')}
                  className="text-xs px-2 py-0.5 rounded-full border border-red-200 bg-red-50 text-red-500 line-through"
                >
                  {value}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default ScalarFieldsTable;
