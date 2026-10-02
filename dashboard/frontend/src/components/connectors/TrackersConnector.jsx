import { useI18n } from '../../i18n';
import CredentialConnector from './CredentialConnector';

// Jira Cloud and Linear: credentials here, the project to tracker link on
// each project's page (routes/trackers.py).
export default function TrackersConnector() {
  const { t } = useI18n();
  return (
    <div className="space-y-5">
      <p className="text-sm text-gray-600">{t('connectors.trackers.intro')}</p>
      <CredentialConnector name="jira" />
      <CredentialConnector name="linear" />
    </div>
  );
}
