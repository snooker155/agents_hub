/**
 * The support bundle and the SLO card on the Health page
 * (dashboard/backend/routes/support.py, common/support_bundle.py,
 * common/slo.py).
 */
import api from './index';

// { status, checked_at, window_seconds, objectives: { start_p95, error_rate } }
export const getSlo = () => api.get('/support/slo');

// The bundle as a Blob, through the authenticated client (same reasoning as
// api/files.js's getWorkspaceFileBlob: a credential in a bare download URL
// would work in single-operator mode only). Admin only; a 403 outside that
// role is expected and left for the caller to show.
export const getSupportBundle = () => api.get('/support/bundle', { responseType: 'blob' });
