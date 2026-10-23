import { useI18n } from '../../i18n';

/**
 * The version an agent runs on (routes/agents: `pinned_version`,
 * `current_version`), as just "vN": yellow when the agent is pinned to it,
 * plain when it follows its live state. Renders nothing while the live state
 * is not a stored version yet (edited since the last snapshot) and nothing
 * is pinned.
 */
export default function AgentVersionBadge({ agent, className = '' }) {
  const { t } = useI18n();
  const pinned = agent?.pinned_version ?? null;
  const current = agent?.current_version ?? null;
  const version = pinned ?? current;
  if (version == null) return null;
  return (
    <span
      className={`inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded whitespace-nowrap ${
        pinned != null ? 'text-amber-800 bg-amber-100 border border-amber-200' : 'text-gray-500 bg-gray-100'
      } ${className}`}
      title={pinned != null ? t('agentVersionPin.pinned', { version }) : t('agentVersionPin.live', { version })}
    >
      {t('agentVersionPin.versionOption', { version })}
    </span>
  );
}
