// Per-agent loop settings (agents/agent_loop.py, fourth-cycle stage 2):
// fallback models, the output JSON Schema, and the tri-state loop toggles.
export default {
  title: 'Loop settings',
  intro: 'Fallback models tried when this agent\'s own model refuses or fails, a schema its final answer must match, and two toggles for the loop\'s own policies.',
  save: 'Save',
  saved: 'Loop settings saved.',
  saveFailed: 'Could not save the loop settings.',
  loadFailed: 'Could not load the loop settings.',
  schemaEmpty: 'Enter a JSON Schema, or switch to free text.',
  schemaInvalidJson: 'Not valid JSON: {{message}}',
  schemaNotObject: 'A JSON Schema must be a single JSON object.',
  advisor: {
    title: 'Advisor',
    intro: 'A second model this agent may ask for advice on a hard step, with consult_advisor. It sees only the question and the context the agent writes into the call, never the run itself. Its cost counts toward the run and its budget.',
    none: 'No advisor',
    notInCatalog: '{{model}} (not enabled on the Models page)',
    hint: 'Models come from the Models page. The workspace\'s agent loop settings limit the calls per run and the answer length.',
  },
  concurrency: {
    title: 'Delegate concurrency',
    intro: 'How many of this agent\'s delegated subtasks (delegate_task_tool) may run at once. A launch past the limit is refused with a clear message.',
    label: 'Max concurrent delegates',
    hint: 'From 1 to 32. Default 6. A run\'s own overrides can set a tighter or looser limit for that run alone.',
  },
  fallback: {
    title: 'Fallback models',
    intro: 'Tried in order when this agent\'s own model refuses, is rate limited, or fails with a server error. The model that actually answered is recorded on the run.',
    empty: 'No fallback models. The run stops on the first failure of this agent\'s own model.',
    addLabel: 'Add a fallback model',
    addPlaceholder: 'Choose a model to add…',
    add: 'Add',
    noCatalog: 'No enabled models in the catalog yet. Enable some on the Models page first.',
    moveUp: 'Move {{model}} up',
    moveDown: 'Move {{model}} down',
    remove: 'Remove {{model}}',
  },
  schema: {
    title: 'Output schema',
    intro: 'When set, the agent\'s final answer must be a single JSON value matching this schema. An answer that fails validation gets up to two automatic repair attempts before the run ends in an error.',
    freeText: 'Free text',
    jsonSchema: 'JSON Schema',
    placeholder: '{\n  "type": "object",\n  "properties": {\n    "answer": {"type": "string"}\n  },\n  "required": ["answer"]\n}',
  },
  triState: {
    automatic: 'Automatic',
    on: 'On',
    off: 'Off',
  },
  toolSearch: {
    title: 'Tool search',
    hint: 'Automatic turns it on once the agent has more tools than the workspace threshold.',
  },
  compaction: {
    title: 'Compaction',
    hint: 'Automatic follows the workspace\'s compaction setting.',
  },
};
