/** The owning record's own page, opened on this run: a flow's run has its
 * own address under the flow, the other entity pages take the run in their
 * ?run= parameter (the loops page also names the loop with ?loop=). */
export function entityLink(group) {
  switch (group.kind) {
    case 'flow':
      return group.parent_id ? `/flows/${group.parent_id}/runs/${encodeURIComponent(group.id)}` : null;
    case 'team':
      return group.parent_id ? `/teams/${group.parent_id}?run=${group.id}` : null;
    case 'loop':
      return group.parent_id ? `/loops/${encodeURIComponent(group.parent_id)}/runs/${encodeURIComponent(group.id)}` : '/loops';
    case 'container':
      return `/tasks/${group.id}`;
    case 'scenario':
      // The scenario page reads its run from ?run=, same as the run picker
      // on that page itself (pages/PlaygroundScenario.jsx).
      return group.parent_id ? `/playground/${group.parent_id}?run=${group.id}` : null;
    default:
      return null;
  }
}

/** A run's own page from a row of the cluster map: an agent run opens its
 * messages, a flow, loop, team or scenario run opens on its owner's page. */
export function runLink(run) {
  if (!run?.run_id) return null;
  if (!run.kind) return `/messages/${encodeURIComponent(run.run_id)}`;
  return entityLink({ kind: run.kind, id: run.run_id, parent_id: run.entity_id });
}
