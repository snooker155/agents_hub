import axios from 'axios';
import { installAgentRevision } from './agentRevision';

// Empty means same-origin: `/api/...` is served by whatever host the app was
// loaded from. In dev that is the Vite server, which proxies /api to the
// backend (see vite.config.js), so the port the backend is published on is not
// baked in here. Set VITE_API_ORIGIN to point a build at a different host.
export const API_ORIGIN = import.meta.env.VITE_API_ORIGIN ?? '';

const api = axios.create({
  baseURL: `${API_ORIGIN}/api`,
});

// Optional operator token (see common/auth.py). Off by default: an unconfigured
// backend accepts every request and this stays a no-op. Settings → System →
// API access sets it through setApiToken below, or it can be set directly with
// `localStorage.setItem('agents_hub_api_token', '<token>')`, or baked into the
// build with VITE_API_TOKEN when the same token should ship with every build.
// localStorage wins so a token can be set (or rotated) without a rebuild.
export const getApiToken = () => {
  try {
    const stored = window.localStorage.getItem('agents_hub_api_token');
    if (stored) return stored;
  } catch {
    // Privacy mode or no localStorage: fall through to the build-time value.
  }
  return import.meta.env.VITE_API_TOKEN ?? '';
};

// Sets or clears this browser's token. An empty value removes the localStorage
// key rather than storing a blank one, so getApiToken then falls back to
// VITE_API_TOKEN (if any) instead of an empty override.
export const setApiToken = (token) => {
  try {
    if (token) window.localStorage.setItem('agents_hub_api_token', token);
    else window.localStorage.removeItem('agents_hub_api_token');
  } catch {
    // Privacy mode or no localStorage: nothing to persist.
  }
};

// The session token of a logged-in user (AUTH_MODE=multi, see
// docs/identity.md). A separate key from the operator token above because they
// are separate things: one is a shared credential the operator pastes in, the
// other is issued by the backend to this browser and revoked on logout. When a
// session exists it wins, so a browser that once held an operator token does
// not keep presenting it after somebody logs in.
const SESSION_KEY = 'agents_hub_session_token';

export const getSessionToken = () => {
  try {
    return window.localStorage.getItem(SESSION_KEY) || '';
  } catch {
    return '';
  }
};

export const setSessionToken = (token) => {
  try {
    if (token) window.localStorage.setItem(SESSION_KEY, token);
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // Privacy mode or no localStorage: nothing to persist.
  }
};

/**
 * Whichever credential this browser currently has, session first.
 *
 * Exported because the SSE stream cannot ride the axios instance: EventSource
 * opens its own connection and cannot set headers, so `StreamContext` has to
 * put this in the query string itself.
 */
export const getAuthToken = () => getSessionToken() || getApiToken();
const activeToken = getAuthToken;

/**
 * A one-time, minute-long ticket (POST /api/auth/ticket) for a connection
 * that cannot set a header: the SSE stream, the browser frame WebSocket, a
 * full page navigation. Under AUTH_MODE=multi it is the only credential the
 * backend accepts in a query string, so the session token never lands in a
 * URL (docs/identity.md, "Tickets for streams"). Resolves to the ticket
 * string; rejects when the call fails.
 */
export const mintAuthTicket = async () => {
  const { data } = await api.post('/auth/ticket');
  return data.ticket;
};

/**
 * `url` with a credential in its query: `ticket=` when the backend can mint
 * one, else (a backend older than tickets answers 404, or the call fails
 * outright) the previous `token=` form. No credential at all when this
 * browser holds none, which is the single-operator case.
 */
export const withAuthTicket = async (url) => {
  const token = getAuthToken();
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  try {
    const ticket = await mintAuthTicket();
    return `${url}${sep}ticket=${encodeURIComponent(ticket)}`;
  } catch {
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  }
};

// Mint a ticket, then leave for `url`: for links that must authenticate a
// full navigation (the GitHub connect flow, an audit export download).
export const navigateWithAuthTicket = async (url) => {
  window.location.assign(await withAuthTicket(url));
};

// Headers a fetch() call outside the `api` instance needs to authenticate.
// Every streaming endpoint below opens its own fetch (a long-lived response
// body axios cannot hand back incrementally), so each has to attach this
// itself rather than riding the interceptor below.
export const authFetchHeaders = () => {
  const token = activeToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

// Identical GETs already on the wire share one request. StrictMode mounts
// every effect twice in development, and several components load the same
// list at the same moment, so without this each page load fires its reads
// in pairs. The key includes the credential, so a login mid-flight never
// hands one person's answer to another. A call with an AbortSignal is left
// alone: aborting a shared request would cancel it for everybody. Each
// joiner gets its own copy of `data`, so a caller that sorts or edits its
// result in place cannot change what another caller sees.
const inflightGets = new Map();
const sendGet = api.get.bind(api);

const copyResponse = (response) => {
  try {
    return { ...response, data: structuredClone(response.data) };
  } catch {
    return response;
  }
};

api.get = (url, config = {}) => {
  if (config.signal) return sendGet(url, config);
  let key;
  try {
    key = JSON.stringify([
      url, config.params ?? null, config.headers ?? null, config.responseType ?? null, activeToken(),
    ]);
  } catch {
    return sendGet(url, config);
  }
  const shared = inflightGets.get(key);
  if (shared) return shared.then(copyResponse);
  const request = sendGet(url, config).finally(() => inflightGets.delete(key));
  inflightGets.set(key, request);
  return request;
};

api.interceptors.request.use((config) => {
  const token = activeToken();
  if (token) {
    config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
  }
  return config;
});

// A 401 means the session this browser holds is gone: expired, logged out
// elsewhere, or revoked with a password reset. Drop it and send the app back
// to the login screen, rather than leaving every page showing its own error.
// Only sessions are handled here: an operator token that stops working is a
// configuration problem the Settings page reports, not a login to redo, and
// the auth routes themselves answer 401 as part of their normal contract.
const LOGIN_PATH = '/login';
const isAuthRoute = (url = '') => String(url).includes('/auth/');

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    if (status === 401 && getSessionToken() && !isAuthRoute(error?.config?.url)) {
      setSessionToken('');
      if (window.location.pathname !== LOGIN_PATH) {
        window.location.assign(LOGIN_PATH);
      }
    }
    return Promise.reject(error);
  },
);

// Agent edits carry the definition they were made against (If-Match), and a
// conflict with somebody else's edit asks the user what to do (agentRevision.js).
installAgentRevision(api);
// Endpoint groups live in their own modules; everything is re-exported here so
// `from '../api'` keeps resolving every name.
export * from './agentImport';
export * from './agents';
export * from './auth';
export * from './chat';
export * from './connections';
export * from './containers';
export * from './environments';
export * from './evals';
export * from './flows';
export * from './hubInfo';
export * from './instances';
export * from './integrations';
export * from './loopsTeams';
export * from './memory';
export * from './models';
export * from './plan';
export * from './projects';
export * from './runs';
export * from './services';
export * from './simulation';
export * from './skills';
export * from './tasks';
export * from './views';
export * from './workspaces';

export default api;
