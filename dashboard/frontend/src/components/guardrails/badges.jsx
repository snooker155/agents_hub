/**
 * Small badges shared by the Guardrails page and the agent's guardrails card:
 * stage, kind and action, each colored the same way everywhere they show up.
 */
import { Ban, AlertTriangle } from 'lucide-react';

const BASE = 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border';

export function StageBadge({ stage, t }) {
  const cls = stage === 'output'
    ? 'bg-blue-50 text-blue-700 border-blue-200'
    : stage === 'both'
      ? 'bg-purple-50 text-purple-700 border-purple-200'
      : 'bg-gray-50 text-gray-600 border-gray-200';
  return <span className={`${BASE} ${cls}`}>{t(`guardrails.stage.${stage}`)}</span>;
}

export function KindBadge({ kind, t }) {
  return (
    <span className={`${BASE} bg-gray-100 text-gray-700 border-gray-200`}>
      {t(`guardrails.kind.${kind}`)}
    </span>
  );
}

export function ActionBadge({ action, t }) {
  if (action === 'warn') {
    return (
      <span className={`${BASE} bg-amber-50 text-amber-700 border-amber-200`}>
        <AlertTriangle className="w-3 h-3" /> {t('guardrails.action.warn').split(':')[0]}
      </span>
    );
  }
  return (
    <span className={`${BASE} bg-red-50 text-red-700 border-red-200`}>
      <Ban className="w-3 h-3" /> {t('guardrails.action.block').split(':')[0]}
    </span>
  );
}
