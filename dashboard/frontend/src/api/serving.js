/**
 * Feature 5, the "Serving" card on the Local tab: the hub's own OpenAI
 * compatible /v1 endpoint, so agents outside the hub (or a stray curl) can
 * call back into whatever provider and model this hub already has configured.
 */
import api from './index';

// { base_url, models: n, auth: 'api_key' | 'token' | 'none' }.
export const getServingInfo = () => api.get('/models/serving/info');

// { rows: [...], totals: {...}, recent: [...] }. `params` may carry since/until.
export const getServingUsage = (params) => api.get('/models/serving/usage', { params });
