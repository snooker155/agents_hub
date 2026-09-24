// Feature 7a: preview through the hub (dashboard/backend/routes/preview.py).
// mintPreviewTicket mints a short-lived, signed ticket for a running
// container ({kind: 'container', name}) or a project's frontend
// ({kind: 'project', project_id}); the iframe then loads the ticket's own
// URL directly, never through this authenticated client, since the ticket
// in the path is what authenticates it.
import api from './index';

export const mintPreviewTicket = (body) => api.post('/preview/tickets', body);
