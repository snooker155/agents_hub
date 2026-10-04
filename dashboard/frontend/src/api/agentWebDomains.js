/**
 * An agent's own domain lists for web_search, fetch_url and the browser
 * (routes/agent_web_domains.py, tools/web.py). `allowed_domains` narrows the
 * agent to those hosts and their subdomains; `blocked_domains` adds to the
 * workspace's deny list.
 */
import api from './index';

const path = (agentId) => `/agents/${encodeURIComponent(agentId)}/web-domains`;

// { allowed_domains, blocked_domains, effective?: { blocked, allowed, agent_allowed } }
export const getAgentWebDomains = (agentId, workspace) =>
  api.get(path(agentId), { params: workspace ? { workspace } : undefined });

// Only the keys present in `payload` change.
export const updateAgentWebDomains = (agentId, payload) => api.put(path(agentId), payload);
