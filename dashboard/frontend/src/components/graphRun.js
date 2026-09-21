/**
 * Where a running turn is inside an imported agent's own graph.
 *
 * An agent that is a graph internally (a LangGraph flow, say) narrates its node
 * boundaries as it runs (`graph_node_start` / `graph_node_end`, translated from
 * the remote's frames by agents/remote_agent.py). This folds those events into
 * the little state the graph mirror needs: which node is lit, and which ones
 * this run has already been through.
 *
 * The open nodes are kept as a stack rather than a single name, because a
 * subgraph's nodes open and close inside a node that is still running. With one
 * name, the inner node finishing would leave the whole graph looking idle while
 * the outer node was still working; with a stack, the parent lights up again.
 *
 * Live only. The record of what happened is the timeline stored with the
 * message; this is thrown away when the next turn starts, because each turn
 * walks the graph again and carrying the last one's path over would show a
 * route the current run never took.
 */

export const EMPTY_GRAPH_RUN = { active: null, visited: [], open: [] };

export function reduceGraphRun(state = EMPTY_GRAPH_RUN, event) {
  if (!event || !event.node) return state;
  const open = state.open || [];

  if (event.type === 'graph_node_start') {
    return {
      active: event.node,
      // A loop revisits nodes; the trail is a set, so a node visited twice is
      // marked once rather than drawn twice.
      visited: state.visited.includes(event.node) ? state.visited : [...state.visited, event.node],
      open: [...open, event.node],
    };
  }

  if (event.type === 'graph_node_end') {
    // The *last* opening of this node closes: a loop enters the same node more
    // than once, and closing its first entry would strand the later ones open.
    const at = open.lastIndexOf(event.node);
    if (at === -1) return state;
    const remaining = open.slice(0, at);
    return {
      ...state,
      open: remaining,
      active: remaining.length ? remaining[remaining.length - 1] : null,
    };
  }

  return state;
}
