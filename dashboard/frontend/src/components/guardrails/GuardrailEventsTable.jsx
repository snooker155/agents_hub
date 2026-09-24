/**
 * Recent guardrail findings (GET /api/guardrails/events): a block or a warn,
 * never a pass. Loaded once and refreshed by the caller (pages/Guardrails.jsx
 * polls it alongside the guardrail list).
 */
import { Link } from 'react-router-dom';
import { useI18n } from '../../i18n';
import { ActionBadge, StageBadge } from './badges';

function fmtAt(at) {
  if (!at) return '';
  try {
    return new Date(at).toLocaleString();
  } catch {
    return at;
  }
}

export default function GuardrailEventsTable({ events }) {
  const { t } = useI18n();
  if (!events || events.length === 0) {
    return <p className="text-sm text-gray-400 py-4 text-center">{t('guardrails.events.empty')}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.at')}</th>
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.guardrail')}</th>
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.stageCol')}</th>
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.actionCol')}</th>
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.reason')}</th>
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.excerpt')}</th>
            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.events.run')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50">
          {events.map((e) => (
            <tr key={e.id} className="hover:bg-gray-50">
              <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{fmtAt(e.at)}</td>
              <td className="px-3 py-2 text-gray-800 font-medium">{e.guardrail_name}</td>
              <td className="px-3 py-2"><StageBadge stage={e.stage} t={t} /></td>
              <td className="px-3 py-2"><ActionBadge action={e.action} t={t} /></td>
              <td className="px-3 py-2 text-gray-600 max-w-xs truncate" title={e.reason}>{e.reason}</td>
              <td className="px-3 py-2 text-gray-400 font-mono text-xs max-w-xs truncate" title={e.excerpt}>{e.excerpt}</td>
              <td className="px-3 py-2 text-xs">
                {e.run_id ? (
                  <Link to={`/messages/${e.run_id}`} className="text-indigo-600 hover:underline">
                    {e.run_id.slice(0, 8)}…
                  </Link>
                ) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
