// Feature 7a: preview through the hub (dashboard/backend/routes/preview.py).
// mintPreviewTicket mints a short-lived, signed ticket for a running
// container ({kind: 'container', name}) or a project's frontend
// ({kind: 'project', project_id}); the iframe then loads the ticket's own
// URL directly, never through this authenticated client, since the ticket
// in the path is what authenticates it.
import api from './index';

export const mintPreviewTicket = (body) => api.post('/preview/tickets', body);

// A successor for a preview that is still open: `body` is {ticket} (the
// current one) or, once that has lapsed, the same target body as mint.
// Answers like mint: {url, expires_in, expires_at}.
export const renewPreviewTicket = (body) => api.post('/preview/tickets/renew', body);
