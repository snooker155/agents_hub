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

// No credential: the API serves one operator on their own machine and asks
// for none. The helpers below keep their names because the streaming calls
// and the SSE connection already call them.
export const getAuthToken = () => '';

// `url` unchanged: there is no credential to put in a query string.
export const withAuthTicket = async (url) => url;

// Leave for `url` (kept for the links that call it).
export const navigateWithAuthTicket = async (url) => {
  window.location.assign(url);
};

// Headers a fetch() call outside the `api` instance would need: none.
export const authFetchHeaders = () => ({});

// Identical GETs already on the wire share one request. StrictMode mounts
// every effect twice in development, and several components load the same
// list at the same moment, so without this each page load fires its reads
// in pairs. A call with an AbortSignal is left
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
      url, config.params ?? null, config.headers ?? null, config.responseType ?? null,
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

// Agent edits carry the definition they were made against (If-Match), and a
// conflict with somebody else's edit asks the user what to do (agentRevision.js).
installAgentRevision(api);
// Endpoint groups live in their own modules; everything is re-exported here so
// `from '../api'` keeps resolving every name.
export * from './agentImport';
export * from './agents';
export * from './chat';
export * from './notify';
export * from './containers';
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
export * from './secrets';
export * from './skills';
export * from './tasks';
export * from './views';
export * from './workspaces';

export default api;
