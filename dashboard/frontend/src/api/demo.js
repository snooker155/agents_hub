/**
 * The demo workspace: a workspace named `demo` seeded with agents, a project,
 * a flow, a team, a scenario, views and recorded runs (see docs/demo.md).
 * Separate from api/index.js because this whole surface belongs to the demo
 * feature.
 */
import api from './index';

// { enabled, present, workspace, counts: { agents, flows, ... } }
export const getDemo = () => api.get('/demo');

// Seed (present: true) or remove (present: false) the demo workspace.
// Returns the same shape as getDemo.
export const setDemo = (present) => api.post('/demo', { present });
