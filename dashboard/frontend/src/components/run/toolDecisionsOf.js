/** The entries of `run.loop.tool_decisions` (components/run/ToolDecisions.jsx),
 * empty for a run without loop data. */
export function toolDecisionsOf(run) {
  const list = run?.loop && typeof run.loop === 'object' ? run.loop.tool_decisions : null;
  return Array.isArray(list) ? list.filter((d) => d && typeof d === 'object') : [];
}
