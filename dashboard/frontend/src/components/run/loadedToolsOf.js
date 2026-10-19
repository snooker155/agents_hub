/** The names in `run.loop.loaded_tools` (components/run/LoadedTools.jsx),
 * empty for a run without loop data. */
export function loadedToolsOf(run) {
  const list = run?.loop && typeof run.loop === 'object' ? run.loop.loaded_tools : null;
  return Array.isArray(list) ? list.filter(Boolean) : [];
}
