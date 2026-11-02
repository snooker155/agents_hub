/**
 * Saved tool outputs: a tool result past the workspace's spill size is
 * written to `tool-outputs/<run_id>/` under the agent's folder
 * (agents/tool_spill.py). They are the agent's scratch, not what it made, so
 * the chat's Artifacts panel and a task's files leave them out; the run page
 * still opens each by its file id.
 */
export const TOOL_OUTPUTS_DIR = 'tool-outputs';

/** True when `path` lies under a `tool-outputs` folder at any depth. */
export const isToolOutputPath = (path) => String(path || '').replace(/\\/g, '/').split('/').includes(TOOL_OUTPUTS_DIR);
