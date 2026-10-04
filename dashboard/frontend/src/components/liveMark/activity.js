/*
 * What the live mark shows for a step of a run. A tool goes by its name:
 * the first rule that matches wins, so the narrow rules come first (a search
 * of errors before any search, making a view before any "create_"). Most
 * steps get a scene (scenes/): a kind of search, or the thing being made;
 * running code, plain reads and lookups get a pose (poses.js). A tool no
 * rule knows is plain work.
 */

const RULES = [
  // Searches, by where they look.
  ['search-errors', /search_errors|_logs$|^logs_|error/],
  ['search-history', /^list_runs$|^run_log$|^list_sessions$|routing_log|^(get|list)_\w*runs?(_tool)?$|^instance_timeline$|run_history/],
  ['search-tools', /search_tools|tool_search|list_tools|find_tool/],
  ['search-memory', /memory/],
  ['search-mail', /mail|imap|inbox|outlook_mail/],
  ['search-db', /^db_|sql|database/],
  ['search-code', /grep|glob|search_code|code_search|ripgrep/],
  ['search-kb', /search_docs|knowledge|rag_|_rag$|notion_search|confluence_search|vector/],
  ['search-files', /search_text|search_files|file_search|drive_search|^list_(workspace_)?files$/],
  ['search-web', /web_search|^search$|search_web|browser_search/],
  // Reading a page or a file: the lens reads it line by line.
  ['search-doc', /^read_(file|doc|workspace_file)$|^fetch_url$|_read_page$|^browser_read$/],
  // Looking something up by name or id: a pose, not a search.
  ['read', /^(list|get|read)_|_status$|^view_(get|snapshot)$/],
  // Stopping something is not making it.
  ['working', /^(stop|cancel|delete|remove)_\w*(run|agent|task|instance|container|deployment)/],
  // Making things.
  ['delegate', /delegate|run_agent|start_agent|wait_for_agent|wake_agent|handoff/],
  ['make-agent', /(create|modify|clone)_agent/],
  ['run-team', /run_team/],
  ['make-team', /team/],
  ['run-flow', /run_(flow|scenario|loop|sequence)/],
  ['make-flow', /flow|scenario|sequence|graph_(add|remove)|add_graph|delete_graph/],
  ['make-3d', /^mesh_|^scene_|3d|blender/],
  ['make-html', /html|view_serve|deploy_project|web_view/],
  ['make-view', /view|slides_|math_plot|chart/],
  ['write-code', /patch|diff|str_replace|edit_code|write_code|aider|claude_code|codex/],
  ['write-file', /^(write|create|save|edit|append)_(file|workspace_file|doc)|document_set|_append$|google_docs_create|notion_create_page|confluence_create_page/],
  // Poses.
  ['code', /run_code|run_shell|run_tests|run_diagnostics|terminal|calculator|python|bash|exec/],
  ['read', /^(read|fetch|get|list|load|open)_|_(read|import|get|status|stats|schema)$|^browser_/],
];

export function stateForTool(name) {
  const tool = String(name || '').toLowerCase();
  if (!tool) return 'working';
  for (const [state, re] of RULES) if (re.test(tool)) return state;
  return 'working';
}

/**
 * The state of a live chat turn: waiting for an approval, a tool, thinking,
 * writing the reply, or just working (nothing reported yet).
 */
export function stateForTurn({ waiting, thinking, tool, text }) {
  if (waiting) return 'wait';
  if (tool) return stateForTool(tool);
  if (thinking) return 'think';
  if (text) return 'speak';
  return 'working';
}
