import { AlertTriangle } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * Shown on the tabs that change what a system agent *is* — its tools and its
 * instructions — rather than merely how it runs. Those two are what the rest of
 * the product is built against, and editing either also detaches the agent from
 * the shipped seed, so it stops receiving updates with new versions.
 */
function SystemAgentWarning({ scope }) {
  const { t } = useI18n();
  return (
    <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
      <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-amber-900">{t('agentDetails.systemAgentWarningTitle')}</p>
        <p className="text-xs text-amber-800 mt-1 leading-relaxed">
          {scope === 'config'
            ? t('agentDetails.systemAgentWarningConfig')
            : t('agentDetails.systemAgentWarningTools')}
        </p>
        <p className="text-xs text-amber-700 mt-1 leading-relaxed">
          {t('agentDetails.systemAgentWarningStopsTracking')}
        </p>
      </div>
    </div>
  );
}

export default SystemAgentWarning;
