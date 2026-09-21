import { describe, it, expect } from 'vitest';
import { EMPTY_GRAPH_RUN, reduceGraphRun } from '../graphRun';

// Following a run through an imported agent's own graph. What the mirror needs
// is small: one lit node and a trail. What is easy to get wrong is a loop, a
// subgraph, and a node that never announced itself.

const walk = (...events) => events.reduce(reduceGraphRun, EMPTY_GRAPH_RUN);

describe('reduceGraphRun', () => {
  it('lights the node that started and remembers where the run has been', () => {
    const state = walk(
      { type: 'graph_node_start', node: 'triage' },
      { type: 'graph_node_end', node: 'triage' },
      { type: 'graph_node_start', node: 'pricing' },
    );

    expect(state.active).toBe('pricing');
    expect(state.visited).toEqual(['triage', 'pricing']);
  });

  it('unlights the graph when the last node finishes', () => {
    const state = walk(
      { type: 'graph_node_start', node: 'answer' },
      { type: 'graph_node_end', node: 'answer' },
    );

    expect(state.active).toBeNull();
    expect(state.visited).toEqual(['answer']);
  });

  it('marks a node visited once however often a loop returns to it', () => {
    const state = walk(
      { type: 'graph_node_start', node: 'work' },
      { type: 'graph_node_end', node: 'work' },
      { type: 'graph_node_start', node: 'critic' },
      { type: 'graph_node_end', node: 'critic' },
      { type: 'graph_node_start', node: 'work' },
    );

    expect(state.visited).toEqual(['work', 'critic']);
    expect(state.active).toBe('work');
  });

  it('lights the parent again when a node inside it finishes', () => {
    // A subgraph's nodes end before the node containing them does. Clearing the
    // highlight outright would leave a running graph looking idle.
    const state = walk(
      { type: 'graph_node_start', node: 'outer' },
      { type: 'graph_node_start', node: 'inner' },
      { type: 'graph_node_end', node: 'inner' },
    );

    expect(state.active).toBe('outer');
    expect(state.visited).toEqual(['outer', 'inner']);

    const done = reduceGraphRun(state, { type: 'graph_node_end', node: 'outer' });
    expect(done.active).toBeNull();
  });

  it('closes the latest entry of a node the run is inside twice', () => {
    // A recursive or re-entered node is open more than once; closing the first
    // entry would strand the rest and unlight a graph that is still working.
    const state = walk(
      { type: 'graph_node_start', node: 'step' },
      { type: 'graph_node_start', node: 'step' },
      { type: 'graph_node_end', node: 'step' },
    );

    expect(state.active).toBe('step');
    expect(state.open).toEqual(['step']);
  });

  it('ignores the end of a node that never started', () => {
    const state = { active: 'triage', visited: ['triage'], open: ['triage'] };
    expect(reduceGraphRun(state, { type: 'graph_node_end', node: 'ghost' })).toBe(state);
  });

  it('ignores an event with no node rather than lighting nothing', () => {
    expect(reduceGraphRun(EMPTY_GRAPH_RUN, { type: 'graph_node_start' })).toBe(EMPTY_GRAPH_RUN);
    expect(reduceGraphRun(EMPTY_GRAPH_RUN, null)).toBe(EMPTY_GRAPH_RUN);
  });

  it('ignores events that are not about the graph', () => {
    const state = { active: 'triage', visited: ['triage'], open: ['triage'] };
    expect(reduceGraphRun(state, { type: 'token', node: 'triage' })).toBe(state);
  });
});
