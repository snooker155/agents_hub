/**
 * The embeddable chat widget's management API (widgets/,
 * dashboard/backend/routes/widget.py, docs/widget.md). Separate from
 * api/index.js like the other one-page features: the list, the form, the
 * snippet, the preview ticket and the conversations all belong to the
 * Widgets page. The visitor side (/api/widgets/public/...) is called by the
 * widget script itself, never from here.
 */
import api from './index';

const enc = encodeURIComponent;

export const getWidgetOptions = () => api.get('/widgets/options');

export const getWidgets = (workspace) => api.get('/widgets', { params: { workspace: workspace || 'default' } });

export const getWidget = (id) => api.get(`/widgets/${enc(id)}`);

export const createWidget = (payload) => api.post('/widgets', payload);

export const updateWidget = (id, payload) => api.patch(`/widgets/${enc(id)}`, payload);

export const deleteWidget = (id) => api.delete(`/widgets/${enc(id)}`);

export const rotateWidgetKey = (id) => api.post(`/widgets/${enc(id)}/rotate-key`);

// { snippet, script_url, hub }
export const getWidgetSnippet = (id) => api.get(`/widgets/${enc(id)}/snippet`);

// { ticket, expires_at, widget_id, public_key, script_path }: lets the live
// preview run the widget from the hub's own origin.
export const createWidgetPreview = (id) => api.post(`/widgets/${enc(id)}/preview`);

export const getWidgetThreads = (id) => api.get(`/widgets/${enc(id)}/threads`);

export const getWidgetThread = (id, threadId) => api.get(`/widgets/${enc(id)}/threads/${enc(threadId)}`);

export const deleteWidgetThread = (id, threadId) => api.delete(`/widgets/${enc(id)}/threads/${enc(threadId)}`);
