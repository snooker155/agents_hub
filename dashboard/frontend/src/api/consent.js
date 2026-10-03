/**
 * The consent portal (routes/consent.py, docs/consent.md): which providers an
 * agent acts on as the end user of a widget or channel, with which access,
 * and the personal grants end users gave, which the operator can revoke.
 * Never carries a token: a grant is listed by account and access only.
 */
import api from './index';

const agentPath = (agentId) => `/consent/agents/${encodeURIComponent(agentId)}`;

// { agent_id, providers, scopes: { google: [...], microsoft: [...] },
//   catalog: { providers: [{ id, label, access, default, ready }], redirect_uri } }
export const getConsentSettings = (agentId) => api.get(agentPath(agentId));

export const updateConsentSettings = (agentId, payload) => api.put(agentPath(agentId), payload);

// { grants: [{ request_id, agent_id, agent_name, provider, access, principal,
//   principal_kind, account_email, granted_at }] }
export const listConsentGrants = (workspace, agentId) =>
  api.get('/consent/grants', { params: { workspace, ...(agentId ? { agent_id: agentId } : {}) } });

export const revokeConsentGrant = (requestId, workspace) =>
  api.post(`/consent/grants/${encodeURIComponent(requestId)}/revoke`, null, { params: { workspace } });
