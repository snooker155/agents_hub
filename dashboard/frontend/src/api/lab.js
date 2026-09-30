/**
 * The research lab's own endpoints: scenario templates, the reproducibility
 * eval, and the views a scenario run publishes. Separate from api/index.js
 * because this surface belongs to the lab feature; everything else about a
 * scenario (fetch, save, run) still goes through the shared helpers there.
 */
import api from './index';

// Ready made scenarios: [{id, name, description, environment, roles}].
export const getScenarioTemplates = () => api.get('/playground/scenarios/templates');

// Create a scenario from a template, cast by one agent (every role) or by a
// team (its members play it at run time). Returns the stored scenario.
export const createScenarioFromTemplate = ({ template, workspace, agentId, teamId, name }) =>
  api.post('/playground/scenarios/from-template', {
    template,
    workspace: workspace || undefined,
    agent_id: agentId || undefined,
    team_id: teamId || undefined,
    name: name || undefined,
  });

// Run the scenario `repeats` times as an eval. Returns {eval_id, eval_run_id}.
export const startReproducibilityEval = (scenarioId, { repeats, provider, model } = {}) =>
  api.post(`/playground/scenarios/${scenarioId}/repeat-eval`, {
    repeats, provider: provider || undefined, model: model || undefined,
  });

// Views owned by one scenario run (the lab's table, chart, formulas, report).
export const getScenarioRunViews = (simRunId) =>
  api.get('/views', { params: { owner_kind: 'scenario', owner_id: simRunId } });
