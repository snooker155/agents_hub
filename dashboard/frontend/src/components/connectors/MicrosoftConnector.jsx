import CredentialConnector from './CredentialConnector';

// Microsoft Graph (Outlook calendar). The Teams bot has its own tab; the two
// may share one app registration.
export default function MicrosoftConnector() {
  return <CredentialConnector name="microsoft" />;
}
