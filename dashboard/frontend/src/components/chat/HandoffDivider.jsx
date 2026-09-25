import { ArrowRightLeft } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * The line between the agent that handed the conversation over and the one
 * that took it (chat/handoff.py): who took over and why. Drawn above the
 * receiving agent's bubble, from the `handoff` that bubble carries.
 */
export default function HandoffDivider({ handoff, agentName }) {
  const { t } = useI18n();
  if (!handoff) return null;
  const agent = agentName || handoff.to_agent_name || handoff.to_agent_id || '';
  const reason = String(handoff.reason || '').trim();
  const label = reason
    ? t('handoffs.divider', { agent, reason })
    : t('handoffs.dividerNoReason', { agent });
  return (
    <div
      className="flex items-center gap-3 mb-6 mx-2"
      role="separator"
      aria-label={label}
      data-testid="handoff-divider"
    >
      <span className="flex-1 border-t border-gray-200" />
      <span className="flex items-center gap-1.5 text-xs text-gray-500 text-center max-w-[70%]">
        <ArrowRightLeft className="w-3.5 h-3.5 shrink-0" />
        <span>{label}</span>
      </span>
      <span className="flex-1 border-t border-gray-200" />
    </div>
  );
}
