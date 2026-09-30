/**
 * Who a turn is addressed to. One answer for all three target modes, so a new
 * mode cannot be added to one control and forgotten in the next.
 */

function targetId(mode, { selectedAgent, selectedFlow, selectedTeam }) {
  if (mode === 'flow') return selectedFlow;
  if (mode === 'team') return selectedTeam;
  return selectedAgent;
}

export { targetId };
