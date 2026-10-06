import { useI18n } from '../../i18n';

/**
 * A dot and a word after a provider's name in the model catalog: whether its
 * local server (Ollama, LM Studio, the hub runtime) answers right now. Nothing
 * for a provider that is not a local server, or before the first check.
 */
export default function ServerStatus({ status }) {
  const { t } = useI18n();
  if (!status) return null;
  const up = !!status.ok;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs shrink-0 ${up ? 'text-green-700' : 'text-gray-400'}`}
      title={up ? status.url : `${status.url}${status.error ? ` (${status.error})` : ''}`}
      data-testid="server-status"
    >
      <span className={`h-2 w-2 rounded-full ${up ? 'bg-green-500' : 'bg-gray-300'}`} />
      {up ? t('localModels.servers.running') : t('localModels.servers.notRunning')}
    </span>
  );
}
