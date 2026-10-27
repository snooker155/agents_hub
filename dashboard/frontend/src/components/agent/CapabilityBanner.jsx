import { AlertTriangle } from 'lucide-react';
import { capabilityLabel, localizeViolation } from '../../lib/capabilities';

/**
 * A flagged capability combination, red when the guard refuses it and amber
 * when it is allowed through (a warn-level rule, the per-agent override or
 * the guard's warn mode). Names each capability and exactly which tools or
 * delegation hops granted it, so the fix is obvious instead of a guessing game.
 */
export default function CapabilityBanner({ violation: raw, softened, overrideActive, t, footer }) {
  if (!raw) return null;
  const violation = localizeViolation(raw, t);
  const soft = !violation.blocking || softened;
  const tone = soft
    ? { box: 'border-amber-200 bg-amber-50', title: 'text-amber-800', text: 'text-amber-800', item: 'text-amber-900', foot: 'text-amber-700' }
    : { box: 'border-red-200 bg-red-50', title: 'text-red-800', text: 'text-red-700', item: 'text-red-800', foot: 'text-red-600' };
  return (
    <div className={`mb-4 rounded-lg border p-3 ${tone.box}`}>
      <div className={`text-sm font-bold flex items-center gap-2 ${tone.title}`}>
        <AlertTriangle className="w-4 h-4 shrink-0" />
        {violation.title}
        {overrideActive && violation.blocking && (
          <span className="px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 text-[10px] font-semibold uppercase tracking-wide">
            {t('agentDetails.overrideActive')}
          </span>
        )}
      </div>
      <div className={`text-xs mt-1.5 ${tone.text}`}>{violation.explanation}</div>
      <ul className="mt-2 space-y-1">
        {(violation.capabilities || []).map((cap) => (
          <li key={cap} className={`text-xs ${tone.item}`}>
            <span className="font-semibold">{capabilityLabel(cap, t)}</span>
            {': '}
            {((violation.sources || {})[cap] || []).join(', ') || '?'}
          </li>
        ))}
      </ul>
      {footer && <div className={`text-xs mt-2 ${tone.foot}`}>{footer}</div>}
    </div>
  );
}
