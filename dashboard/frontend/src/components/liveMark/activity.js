/*
 * What the live mark shows for a step of a run. A tool goes by its name:
 * the first rule that matches wins, so the narrow rules come first (a search
 * of errors before any search, making a view before any "create_"). Most
 * steps get a scene (scenes/): a kind of search, the thing being made, one
 * of the hub relay stories or one of the mark's rebuilds (a clock for
 * schedules, gears for sums, an antenna for messages, a graph that grows
 * and is pruned for planning); running code, plain reads and lookups get a
 * pose (poses.js). A tool no rule knows gets one of a few rebuilds that mean
 * nothing in particular, always the same one for the same tool, so steps of
 * different tools look different.
 *
 * The assistant's catalog tools (hub_lookup, service_lookup, hub_action)
 * name what they reach in their input, so they go by its `kind` instead:
 * a run is a search of the history, a cost a search of the records, a
 * model or a skill a search of the tools.
 */

// hub_lookup and service_lookup, by the kind of record (chat/lookup.py).
const LOOKUP_KINDS = [
  ['search-history', /^(run|message|session|job|audit|web_log|approval)$/],
  ['search-errors', /^(health|guardrail)$/],
  ['search-mail', /^notification$/],
  ['search-tools', /^(model|voice|speech|transcription|tool|skill|mcp|mcp_server|connection|registry|widget)$/],
  ['search-files', /^(view|project)$/],
];

// hub_action, by its verb and the kind it acts on (chat/actions.py).
function stateForAction(input) {
  const action = String(input.action || '').toLowerCase();
  const kind = String(input.kind || '').toLowerCase();
  if (/^(enable|disable|pause)$/.test(action)) return 'mark-tetra';
  if (!/^(start|restart|resume)$/.test(action)) return 'working';
  if (kind === 'loop') return 'mark-cycle';
  if (/^(scenario|flow)$/.test(kind)) return 'run-flow';
  if (kind === 'team') return 'run-team';
  if (/^(pulse|agent)$/.test(kind)) return 'delegate';
  if (action === 'restart') return 'relay-deploy';
  return 'working';
}

const RULES = [
  // Thinking a plan through: a graph grows and is pruned.
  ['mark-graph', /^think$|prune|(^|_)plan(_|$)/],
  // Time: calendars, schedules, progress.
  ['mark-3dclock', /calendar/],
  ['mark-gclock', /schedul|cron|timezone|(^|_)(time|date)(_|$)/],
  ['mark-arcs', /_status(_tool)?$|progress|monitor/],
  // Searches, by where they look.
  ['search-errors', /^service_health$/],
  ['search-db', /^costs?_(summary|report)$/],
  ['search-errors', /search_errors|_logs$|^logs_|error/],
  ['search-history', /^list_runs$|^run_log$|^list_sessions$|routing_log|web_log|^(get|list)_\w*runs?(_tool)?$|^instance_timeline$|run_history/],
  ['search-tools', /search_tools|tool_search|list_tools|find_tool/],
  // Writing to memory drops into the pool; reading it is a search.
  ['mark-goodrop', /^write(_structured)?_memory$|^remember$|journal|save_extraction/],
  ['search-memory', /memory|^recall|^forget$/],
  ['search-mail', /mail|imap|inbox|outlook_mail/],
  ['search-db', /^db_|sql|database/],
  ['search-code', /grep|glob|search_code|code_search|ripgrep/],
  ['search-kb', /search_docs|knowledge|rag_|_rag$|notion_search|confluence_search|vector/],
  ['search-files', /search_text|search_files|file_search|drive_search|^list_(workspace_)?files$/],
  ['search-web', /web_search|^search$|search_web|browser_search/],
  // Reading a page or a file: the lens reads it line by line.
  ['search-doc', /^read_(file|doc|workspace_file)$|^fetch_url$|_read_page$|^browser_read$/],
  // Bringing things in or out, checking them against each other.
  ['mark-capture', /_import$|^import_|collect|gather|scrape|crawl|harvest/],
  ['mark-net', /export/],
  ['mark-venn', /validate|compare|_match$/],
  // Looking something up by name or id: a pose, not a search.
  ['read', /^(list|get|read)_|^view_(get|snapshot)$|_options$/],
  // Stopping or removing something is not making it (a flow's graph edits
  // are making the flow).
  ['working', /^(stop|cancel|delete|remove|clear)_(?!graph_)/],
  // The hub and its agents: asking, handing on, answering in a stream,
  // a pipeline, subtasks, a new lead, a build and launch.
  ['relay-ask', /^ask_|^propose_/],
  ['relay-stream', /^wait_for_agent|stream/],
  ['relay-handoff', /handoff|hand_off|reject_assignment/],
  ['relay-lead', /^assign_agent|set_lead/],
  ['relay-delegate', /run_eval|batch|parallel|fan_?out/],
  ['relay-team', /subtask/],
  ['relay-flow', /pipeline|transition|dependenc/],
  ['relay-deploy', /deploy|git_publish|^restart_|release/],
  ['mark-ring', /(^|_)sync(_|$)/],
  ['mark-recall', /^wake|(^|_)ping(_|$)|reconnect/],
  ['mark-replicate', /clone|fork|replica|(^|_)scale(_|$)/],
  ['delegate', /delegate|run_agent|start_agent|wait_for_agent|handoff/],
  // Speech is the voice's own pose; a message goes out on the antenna.
  ['speak', /synthesize_speech/],
  ['listen', /transcribe/],
  ['mark-antenna', /notify|channel_send|_comment$|broadcast|publish|post_message|send_(message|mail|email)/],
  // The mark rebuilt into what a step does.
  ['mark-invert', /revert|undo|rollback/],
  ['mark-gears', /calculat|compute|estimate/],
  ['mark-cycle', /loop/],
  ['mark-fission', /split|chunk|^extract_/],
  ['mark-fusion', /^(?!mesh_).*(merge|combine|summar)/],
  ['mark-goomerge', /consolidat|dedupe|compact|squash/],
  ['mark-goopour', /copy|transfer|migrat|convert|translat/],
  ['mark-load', /install|upload|download|^load_|_load$|(^|_)pull(_|$)/],
  ['mark-hgframe', /sleep|delay|timer|countdown|^block_|wait_until/],
  ['mark-hggrains', /queue|throttle|backoff|retry|prioriti/],
  ['mark-gplanet', /orchestrat|coordinat|supervis|dispatch/],
  ['mark-gchain', /chain|trigger|automat|_hook|hook_/],
  ['mark-strings', /audio|sound|music|tune/],
  ['mark-spiro', /generate_(image|video)|draw|sketch|pattern/],
  ['mark-shapes', /layout|diagram|mermaid|geometry|shape/],
  ['mark-flow', /^switch_|set_default|set_active/],
  ['mark-tetra', /toggle|flip|rotate/],
  ['mark-3dmorph', /world/],
  ['mark-3d', /(^|_)sim_|simulat/],
  // Making things.
  ['make-agent', /(create|modify)_agent/],
  ['run-team', /run_team/],
  ['make-team', /team|_workspace(_agent)?$/],
  ['run-flow', /run_(flow|scenario|sequence)/],
  ['make-flow', /flow|scenario|sequence|graph_(add|remove)|add_graph|delete_graph/],
  ['make-3d', /^mesh_|^scene_|3d|blender/],
  ['make-html', /html|view_serve|web_view/],
  ['make-view', /view|slides_|math_plot|chart/],
  ['mark-venn', /eval/],
  ['write-code', /patch|diff|str_replace|edit_code|write_code|aider|claude_code|codex|commit/],
  ['write-file', /^(write|create|save|edit|append)_(file|workspace_file|doc)|document_set|_append$|google_docs_create|create_page|create_issue|^(create|update)_task$/],
  // Poses.
  ['code', /run_code|run_shell|run_tests|run_diagnostics|terminal|python|bash|exec/],
  ['read', /^(read|fetch|get|list|load|open)_|(^|_)(list|get)_|_(read|import|get|status|stats|schema|list)$|^browser_/],
];

// What a tool no rule knows gets: rebuilds of the mark that stand for no
// step in particular.
export const UNKNOWN_TOOL_STATES = ['mark-3d', 'mark-tetra2', 'mark-goo'];

function hash(text) {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** A tool call's input as an object: streams send it parsed or as JSON. */
function inputOf(input) {
  if (input && typeof input === 'object') return input;
  if (typeof input !== 'string') return {};
  try {
    const data = JSON.parse(input);
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

export function stateForTool(name, input) {
  const tool = String(name || '').toLowerCase();
  if (!tool) return 'working';
  if (tool === 'hub_lookup' || tool === 'service_lookup') {
    const kind = String(inputOf(input).kind || '').toLowerCase();
    for (const [state, re] of LOOKUP_KINDS) if (re.test(kind)) return state;
    // agents, teams, instances, budgets and the rest are records
    return kind ? 'search-db' : 'read';
  }
  if (tool === 'hub_action') return stateForAction(inputOf(input));
  for (const [state, re] of RULES) if (re.test(tool)) return state;
  return UNKNOWN_TOOL_STATES[hash(tool) % UNKNOWN_TOOL_STATES.length];
}

/**
 * The state of a live chat turn: waiting for an approval, a tool, thinking,
 * writing the reply, or just working (nothing reported yet).
 */
export function stateForTurn({ waiting, thinking, tool, input, text }) {
  if (waiting) return 'wait';
  if (tool) return stateForTool(tool, input);
  if (thinking) return 'think';
  if (text) return 'speak';
  return 'working';
}
