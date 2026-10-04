import CredentialConnector from './CredentialConnector';

// Notion and Confluence: credentials only; the tools do the rest.
export default function KnowledgeConnector() {
  return (
    <div className="space-y-5">
      <CredentialConnector name="notion" />
      <CredentialConnector name="confluence" />
    </div>
  );
}
