/**
 * The consent portal (routes/consent.py, docs/consent.md): which providers an
 * agent acts on as the end user of a widget or channel, with which access,
 * and the personal grants end users gave, which the operator can revoke.
 * Never carries a token: a grant is listed by account and access only.
 */
import api from './index';

const agentPath = (agentId) => `/consent/agents/${encodeURIComponent(agentId)}`;

// The catalog's `ready` flags depend on which connectors this workspace can
// reach (the default workspace's credentials work everywhere, another
// workspace's own work only there — connectors/channels/store.py), so every
// call below carries `workspace`, the default's when omitted.

// { providers: [{ id, label, access, default, ready }], redirect_uri }
export const getConsentCatalog = (workspace) =>
  api.get('/consent/catalog', { params: workspace ? { workspace } : {} });

// { agent_id, providers, scopes: { google: [...], microsoft: [...] },
//   catalog: { providers: [{ id, label, access, default, ready }], redirect_uri } }
export const getConsentSettings = (agentId, workspace) =>
  api.get(agentPath(agentId), { params: workspace ? { workspace } : {} });

export const updateConsentSettings = (agentId, payload, workspace) =>
  api.put(agentPath(agentId), payload, { params: workspace ? { workspace } : {} });

// { grants: [{ request_id, agent_id, agent_name, provider, access, principal,
//   principal_kind, account_email, granted_at }] }
export const listConsentGrants = (workspace, agentId) =>
  api.get('/consent/grants', { params: { workspace, ...(agentId ? { agent_id: agentId } : {}) } });

export const revokeConsentGrant = (requestId, workspace) =>
  api.post(`/consent/grants/${encodeURIComponent(requestId)}/revoke`, null, { params: { workspace } });
