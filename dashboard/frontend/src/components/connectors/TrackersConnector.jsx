import CredentialConnector from './CredentialConnector';

// Jira Cloud and Linear: credentials here, the project to tracker link on
// each project's page (routes/trackers.py).
export default function TrackersConnector() {
  return (
    <div className="space-y-5">
      <CredentialConnector name="jira" />
      <CredentialConnector name="linear" />
    </div>
  );
}
