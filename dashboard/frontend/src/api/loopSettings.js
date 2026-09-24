/**
 * The workspace's agent loop settings (agents/loop_ext/settings.py): the
 * ``settings.loop`` block the loop's own extensions read through
 * ``loop_setting`` (compaction, tool search, Anthropic native features,
 * strict tool schemas), plus what each key resolves to once the environment
 * and the built-in defaults are folded in.
 */
import api from './index';

const path = (workspace) => `/workspaces/${encodeURIComponent(workspace)}/loop-settings`;

// { settings, effective, defaults, env }: see routes/agent_loop_settings.py.
export const getWorkspaceLoopSettings = (workspace) => api.get(path(workspace));

// Merges `patch` into the stored block; a key set to null removes it (falls
// back to the environment, then the default). Answers like the GET.
export const updateWorkspaceLoopSettings = (workspace, patch) => api.put(path(workspace), patch);
