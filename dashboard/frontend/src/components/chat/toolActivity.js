/**
 * What an agent is doing when it calls a tool, in words: "Searching the web:
 * flights to Lisbon" rather than "web_search". Known tools have their own
 * phrase (toolActivity namespace); any other tool, an MCP or connector tool
 * among them, is read from its name: the verb in it picks a phrase ("create"
 * → Creating) and the rest of the name says what it acts on. The subject after
 * the colon is the call's main argument, as the process graph shows it
 * (toolFormatters.js).
 */
import { toolInline } from '../toolFormatters';

// tool name → phrase key in the toolActivity namespace.
const PHRASES = {
  web_search: 'webSearch',
  fetch_url: 'fetchUrl',
  browser: 'browse',
  read_file: 'readFile',
  read_workspace_file: 'readFile',
  read_doc: 'readDoc',
  write_file: 'writeFile',
  create_file: 'writeFile',
  save_workspace_file: 'writeFile',
  delete_file: 'deleteFile',
  list_files: 'listFiles',
  list_workspace_files: 'listFiles',
  search_text: 'searchText',
  apply_unified_diff: 'editFiles',
  run_shell: 'runShell',
  run_code: 'runCode',
  calculator: 'calculate',
  search_docs: 'searchDocs',
  document_set: 'searchDocs',
  search_memory: 'searchMemory',
  recall: 'searchMemory',
  recall_episodes: 'searchMemory',
  read_memory: 'readMemory',
  read_structured_memory: 'readMemory',
  write_memory: 'writeMemory',
  write_structured_memory: 'writeMemory',
  remember: 'writeMemory',
  record_episode: 'writeMemory',
  get_skill: 'loadSkill',
  list_skills: 'findSkill',
  create_skill: 'createSkill',
  create_task: 'createTask',
  add_subtask: 'createTask',
  update_task: 'updateTask',
  block_task: 'updateTask',
  stop_task: 'updateTask',
  set_task_dependencies: 'updateTask',
  create_sequence: 'updateTask',
  get_task: 'checkTasks',
  list_tasks: 'checkTasks',
  get_task_result: 'checkTasks',
  run_agent_tool: 'delegate',
  delegate_task_tool: 'delegate',
  start_agent_tool: 'delegate',
  assign_agent_tool: 'delegate',
  wait_for_agent_tool: 'waitAgent',
  get_agent_status_tool: 'waitAgent',
  create_agent_tool: 'setUpAgent',
  modify_agent_tool: 'setUpAgent',
  list_agents_tool: 'lookAgents',
  get_agent_tool: 'lookAgents',
  run_flow_tool: 'runFlow',
  run_new_flow_tool: 'runFlow',
  create_flow_tool: 'buildFlow',
  modify_flow_tool: 'buildFlow',
  run_team_tool: 'runTeam',
  run_loop_tool: 'runLoop',
  create_view: 'buildView',
  suggest_view: 'buildView',
  math_plot: 'buildView',
  slides_add: 'makeSlides',
  slides_style: 'makeSlides',
  slides_export: 'exportSlides',
  notify_user: 'notify',
  schedule_task: 'schedule',
  schedule_notification: 'schedule',
  tracker_create_issue: 'createIssue',
  tracker_comment: 'commentIssue',
  tracker_get_issue: 'lookIssues',
  tracker_list_issues: 'lookIssues',
  tracker_transition: 'moveIssue',
  git_publish: 'publishGit',
  deploy_project: 'deploy',
  ask_user: 'askUser',
  switch_model: 'switchModel',
};

// Prefixes of tool families that share one phrase.
const PREFIXES = [
  ['view_', 'buildView'],
  ['graph_', 'buildView'],
  ['scene_', 'buildView'],
  ['slides_', 'makeSlides'],
];

// A verb in a tool's name → the generic phrase for it.
const VERBS = [
  [['search', 'find', 'query', 'lookup', 'grep'], 'search'],
  [['create', 'add', 'new', 'insert', 'make', 'generate', 'post', 'open'], 'create'],
  [['delete', 'remove', 'drop', 'clear', 'close', 'cancel', 'archive'], 'remove'],
  [['update', 'edit', 'modify', 'patch', 'set', 'write', 'save', 'upload', 'put',
    'move', 'rename', 'assign', 'merge', 'transition'], 'update'],
  [['send', 'notify', 'email', 'reply', 'comment', 'message', 'publish', 'share'], 'send'],
  [['run', 'exec', 'execute', 'start', 'invoke', 'call', 'trigger', 'deploy'], 'run'],
  [['get', 'read', 'fetch', 'list', 'show', 'describe', 'view', 'load', 'download',
    'check', 'status', 'inspect'], 'read'],
];

// The words of a tool name, without an MCP server prefix and a `_tool` tail:
// "mcp__github__create_issue" → ["github", "create", "issue"].
function nameWords(tool) {
  return String(tool || '')
    .replace(/^mcp__/, '')
    .replace(/_tool$/, '')
    .split(/__|[_\-.\s]+|(?<=[a-z])(?=[A-Z])/)
    .map((w) => w.toLowerCase())
    .filter(Boolean);
}

function phraseKey(tool) {
  if (PHRASES[tool]) return { key: PHRASES[tool] };
  const prefix = PREFIXES.find(([p]) => String(tool || '').startsWith(p));
  if (prefix) return { key: prefix[1] };
  const words = nameWords(tool);
  for (const [verbs, key] of VERBS) {
    const at = words.findIndex((w) => verbs.includes(w));
    if (at !== -1) {
      const object = words.filter((_, i) => i !== at).join(' ');
      return { key: `generic.${key}`, object };
    }
  }
  return { key: 'generic.use', object: words.join(' ') || 'tool' };
}

/**
 * What the agent is doing with this call, as one line.
 *
 * @param {function} t the i18n lookup.
 * @param {string} tool the tool's name.
 * @param {*} [input] the call's raw arguments (JSON, a Python repr or text).
 * @param {number} [max] longest subject shown after the colon.
 */
export function toolActivity(t, tool, input, max = 80) {
  const { key, object } = phraseKey(tool);
  const phrase = t(`toolActivity.${key}`, { object: object || '' }).replace(/\s+/g, ' ').trim();
  let subject;
  try {
    subject = input == null || input === '' ? '' : toolInline(tool, input, max);
  } catch {
    subject = '';
  }
  subject = String(subject || '').replace(/\s+/g, ' ').trim();
  return subject ? `${phrase}: ${subject}` : phrase;
}
