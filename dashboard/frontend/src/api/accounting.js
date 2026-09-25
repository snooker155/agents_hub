/**
 * The spend report by key, user, project, workspace, agent or model
 * (dashboard/backend/routes/accounting.py, docs/costs.md "Report"), and
 * editing a personal key's own money quota. Separate from api/index.js like
 * the other one-page features: only the Costs page's report section and the
 * Account page's key form use these.
 */
import api from './index';

// { group_by, rows: [{key, label, runs, calls, inbound_tokens, cached_tokens,
//   outbound_tokens, total_tokens, cost}], totals: {...} }.
export const getAccountingReport = (params) => api.get('/accounting/report', { params });

// The CSV as a Blob, through the authenticated client (see
// api/files.js's getWorkspaceFileBlob): a bare link would carry no
// credential in token or multi mode. Pair with saveBlobAs.
export const getAccountingReportCsv = (params) =>
  api.get('/accounting/report', { params: { ...params, format: 'csv' }, responseType: 'blob' });

// Edits a live personal key's own limits, its money quota included
// (routes/account.py). Only the fields present in `data` change.
export const updateMyApiKeyLimits = (id, data) => api.put(`/auth/keys/${id}`, data);
