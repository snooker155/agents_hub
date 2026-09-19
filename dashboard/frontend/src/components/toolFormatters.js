// Per-tool inline representation of a tool call's data. Each tool decides how
// its arguments should be summarized on the collapsed node (a read-file tool
// shows just the file name; a shell tool shows the command). Unlisted tools
// fall back to the first scalar argument. Add an entry here to give a new tool
// its own inline form.

import { preview } from './processUtils';

// Parse a tool's raw input into its args. The backend normalizes tool args to
// compact JSON, but stay tolerant of Python-literal reprs (single-quoted dicts,
// True/False/None) and plain scalar strings.
export function parseToolInput(input) {
  if (input == null) return null;
  if (typeof input === 'object') return input;
  const s = String(input).trim();
  if (!s) return null;
  const structured =
    (s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'));
  if (!structured) return s;
  try {
    return JSON.parse(s);
  } catch { /* fall through to a tolerant Python-literal parse */ }
  try {
    const norm = s
      .replace(/\bNone\b/g, 'null')
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false');
    // Only swap quotes when there are no double quotes that would be clobbered.
    return JSON.parse(norm.includes('"') ? norm : norm.replace(/'/g, '"'));
  } catch {
    return s;
  }
}

// Last path segment, e.g. "/a/b/read_me.py" -> "read_me.py".
function basename(v) {
  const s = String(v ?? '');
  if (!/[\\/]/.test(s)) return s;
  const parts = s.split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : s;
}

// Value of the first argument, without its key (file paths as their basename).
function firstScalar(args) {
  if (args == null) return '';
  if (typeof args !== 'object') return String(args);
  const v = Array.isArray(args) ? args[0] : Object.values(args)[0];
  if (v == null) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return /[\\/]/.test(String(v)) ? basename(v) : String(v);
}

function diffFileCount(diffText) {
  const hits = String(diffText ?? '').match(/^\+\+\+ /gm);
  const n = hits ? hits.length : 0;
  return n ? `${n} file${n > 1 ? 's' : ''}` : 'diff';
}

const arrow = (a, b) => (a && b ? `${a} → ${b}` : (a || b || ''));

// Which parts of a record an edit touches, by argument name: a surgical world
// edit is "add_locations, remove_actions", and naming them says more than the
// id of the world the page is already showing.
function sections(args, skip = []) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return '';
  const names = Object.entries(args)
    .filter(([k, v]) => !skip.includes(k) && v != null && v !== '')
    .map(([k]) => k);
  return names.join(', ');
}

// tool name -> (parsedArgs) => keyless inline string.
const FORMATTERS = {
  // Filesystem — show the file name.
  read_file: (a) => basename(a.path ?? a.file_path),
  write_file: (a) => basename(a.path ?? a.file_path),
  create_file: (a) => basename(a.path ?? a.file_path),
  delete_file: (a) => basename(a.path ?? a.file_path),
  list_files: (a) => a.glob ?? a.pattern ?? '**/*',
  search_text: (a) =>
    a.pattern != null ? (a.file_glob ? `/${a.pattern}/ in ${a.file_glob}` : `/${a.pattern}/`) : '',
  apply_unified_diff: (a) => diffFileCount(a.diff_text),

  // Execution / reasoning.
  run_shell: (a) => a.command ?? '',
  calculator: (a) => a.expression ?? '',
  think: (a) => a.thought ?? '',
  plan: (a) => a.plan ?? '',

  // Interaction / skills.
  ask_user: (a) => a.question ?? a.prompt ?? '',
  get_skill: (a) => a.skill_id ?? a.id ?? a.name ?? '',
  list_skills: (a) => a.query ?? a.tags ?? '',
  create_skill: (a) => a.name ?? a.title ?? '',

  // Task management — identify by title or task id.
  create_task: (a) => a.title ?? '',
  add_subtask: (a) => a.title ?? '',
  get_task: (a) => a.id ?? '',
  update_task: (a) => a.id ?? '',
  stop_task: (a) => a.id ?? '',
  block_task: (a) => (a.reason ? `${a.id} (${a.reason})` : (a.id ?? '')),
  set_task_dependencies: (a) => a.id ?? '',
  create_sequence: (a) => (Array.isArray(a.task_ids) ? a.task_ids.join(', ') : ''),
  get_task_result: (a) => a.task_id ?? '',

  // Agent orchestration.
  assign_agent_tool: (a) => arrow(a.agent_id, a.task_id),
  run_agent_tool: (a) => a.agent_id ?? '',
  start_agent_tool: (a) => a.task_id ?? '',
  reject_assignment_tool: (a) => a.task_id ?? '',
  stop_agent_tool: (a) => a.task_id ?? '',
  wait_for_agent_tool: (a) => a.task_id ?? '',
  get_agent_status_tool: (a) => a.task_id ?? '',
  create_agent_tool: (a) => a.agent_id ?? a.name ?? '',
  get_agent_tool: (a) => a.agent_id ?? '',
  modify_agent_tool: (a) => a.agent_id ?? '',
  delete_agent_tool: (a) => a.agent_id ?? '',

  // Flow management.
  create_flow_tool: (a) => a.name ?? '',
  get_flow_tool: (a) => a.flow_id ?? '',
  modify_flow_tool: (a) => a.flow_id ?? '',
  delete_flow_tool: (a) => a.flow_id ?? '',
  validate_flow_tool: (a) => a.flow_id ?? '',
  run_flow_tool: (a) => a.flow_id ?? '',

  // Scheduling.
  schedule_notification: (a) => a.title ?? '',
  schedule_task: (a) => a.title ?? '',
  notify_user: (a) => a.title ?? '',
  cancel_scheduled: (a) => a.id ?? '',
  update_scheduled: (a) => a.id ?? '',

  // Memory.
  read_memory: (a) => a.note_title ?? a.kv_key ?? a.memory_id ?? '',
  write_memory: (a) => a.note_title ?? a.kv_key ?? a.memory_id ?? '',
  search_memory_semantic: (a) => a.query ?? '',
  read_structured_memory: (a) => a.slot ?? a.memory_id ?? '',
  write_structured_memory: (a) => a.slot ?? '',
  recall: (a) => a.query ?? '',
  remember: (a) => a.slot ?? a.note_title ?? '',
  record_episode: (a) => a.summary ?? a.kind ?? '',
  recall_episodes: (a) => a.query ?? a.kind ?? '',

  // Playground worlds and scenarios — a build chat is mostly these, so what
  // they show is the difference between a readable trace and eight identical
  // lines. Show what the call is *about*: the thing being made, or the section
  // of an edit rather than the id the user is already looking at.
  create_world_tool: (a) => a.name ?? '',
  get_world_tool: (a) => a.world_id ?? '',
  modify_world_tool: (a) => sections(a, ['world_id']),
  validate_world_tool: (a) => a.world_id ?? '',
  delete_world_tool: (a) => a.world_id ?? '',
  list_worlds_tool: (a) => a.workspace ?? '',
  create_scenario_tool: (a) => a.name ?? '',
  get_scenario_tool: (a) => a.scenario_id ?? '',
  modify_scenario_tool: (a) => sections(a, ['scenario_id']),
  delete_scenario_tool: (a) => a.scenario_id ?? '',
  validate_scenario_tool: (a) => a.scenario_id ?? '',
  list_scenarios_tool: (a) => a.workspace ?? '',

  // Views / diagrams.
  create_view: (a) => a.title ?? a.view_kind ?? '',
  view_add_asset: (a) => basename(a.path),
  graph_add_node: (a) => a.label ?? a.node_id ?? '',
  graph_add_edge: (a) => arrow(a.source, a.target),
  add_graph_node: (a) => a.label ?? a.id ?? '',
  add_graph_edge: (a) => arrow(a.source, a.target),
  get_project_graph: (a) => a.view ?? '',
};

// Inline, keyless summary of a tool call's data for the collapsed node.
export function toolInline(tool, input, max = 120) {
  const args = parseToolInput(input);
  const fmt = FORMATTERS[tool];
  let out = '';
  try {
    if (fmt) out = fmt(args && typeof args === 'object' ? args : {}) || '';
  } catch {
    out = '';
  }
  if (!out) out = firstScalar(args);
  return preview(out, max);
}
