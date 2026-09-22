/**
 * Folding a delegated sub-agent's run into the timeline of the run that asked
 * for it, so a delegation reads as one nested card rather than as a flat
 * sequence of somebody else's tool calls.
 */

// ---------------------------------------------------------------------------
// Delegation (run_agent_tool) live-nesting
// ---------------------------------------------------------------------------
// A delegated child run streams its tool/thought events tagged with the child
// run_id + nesting depth. The event handler folds them into a `delegation`
// timeline entry: { type:'delegation', run_id, agent_id, agent_name, input,
// running, ok, timeline:[ ...nested entries... ] }. Nested entries use the same
// shapes as top-level ones (reasoning / tool / delegation), so a delegation can
// itself contain deeper delegations — rendered recursively by DelegationCard.

// Apply `fn` to the delegation entry whose run_id matches, searching nested
// delegation timelines. Returns a new timeline array (immutable), or the same
// reference when nothing matched (so React can skip untouched branches).
function mapDelegation(timeline, runId, fn) {
  if (!Array.isArray(timeline)) return timeline;
  let changed = false;
  const next = timeline.map((e) => {
    if (e && e.type === 'delegation') {
      if (e.run_id === runId) { changed = true; return fn(e); }
      const inner = mapDelegation(e.timeline || [], runId, fn);
      if (inner !== (e.timeline || [])) { changed = true; return { ...e, timeline: inner }; }
    }
    return e;
  });
  return changed ? next : timeline;
}

// Append `entry` to the delegation(parentRunId)'s nested timeline, or to the
// top-level timeline when parentRunId is absent or is the message's own run.
function appendUnderDelegation(timeline, parentRunId, messageRunId, entry) {
  if (!parentRunId || parentRunId === messageRunId) {
    return [...(timeline || []), entry];
  }
  return mapDelegation(timeline, parentRunId, (d) => ({
    ...d, timeline: [...(d.timeline || []), entry],
  }));
}

// Append an inner entry (reasoning / tool) into delegation(runId)'s own timeline.
function appendIntoDelegation(timeline, runId, entry) {
  return mapDelegation(timeline, runId, (d) => ({
    ...d, timeline: [...(d.timeline || []), entry],
  }));
}

// Resolve the last still-running tool inside delegation(runId) with `patch`.
function resolveDelegationTool(timeline, runId, patch) {
  return mapDelegation(timeline, runId, (d) => {
    const tl = [...(d.timeline || [])];
    for (let i = tl.length - 1; i >= 0; i -= 1) {
      if (tl[i].type === 'tool' && tl[i].running) { tl[i] = { ...tl[i], ...patch, running: false }; break; }
    }
    return { ...d, timeline: tl };
  });
}

export { mapDelegation, appendUnderDelegation, appendIntoDelegation, resolveDelegationTool };
