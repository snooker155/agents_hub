import { describe, expect, it } from 'vitest';
import { logRows } from '../logRows';

describe('logRows: one log row per agent node', () => {
  it('turns the running row into the outcome in place', () => {
    const rows = logRows([
      { type: 'flow_start', timestamp: 't0', run_group: 'g' },
      { type: 'agent_start', node_id: 'a', timestamp: 't1', run_group: 'g', input: 'do it', run_id: 'r1', content: 'Running A' },
      { type: 'flow_state', node_id: 'a', timestamp: 't1b', run_group: 'g' },
      { type: 'agent_start', node_id: 'b', timestamp: 't2', run_group: 'g', content: 'Running B' },
      { type: 'agent_finish', node_id: 'a', timestamp: 't3', run_group: 'g', output: 'done', content: 'Completed A' },
      { type: 'agent_error', node_id: 'b', timestamp: 't4', run_group: 'g', output: 'boom', content: 'B failed' },
      { type: 'flow_finish', timestamp: 't5', run_group: 'g' },
    ]);
    expect(rows.map((r) => r.type)).toEqual(['flow_start', 'agent_finish', 'agent_error', 'flow_finish']);
    expect(rows[1]).toMatchObject({ content: 'Completed A', timestamp: 't1', input: 'do it', run_id: 'r1', output: 'done' });
  });

  it('keeps a node that is still working as its running row', () => {
    const rows = logRows([{ type: 'agent_start', node_id: 'a', timestamp: 't1', content: 'Running A' }]);
    expect(rows).toEqual([{ type: 'agent_start', node_id: 'a', timestamp: 't1', content: 'Running A' }]);
  });

  it('gives each pass of a node its own row', () => {
    const pass = (t) => [
      { type: 'agent_start', node_id: 'a', timestamp: `${t}s`, run_group: 'g' },
      { type: 'agent_finish', node_id: 'a', timestamp: `${t}f`, run_group: 'g' },
    ];
    expect(logRows([...pass('1'), ...pass('2')]).map((r) => r.timestamp)).toEqual(['1s', '2s']);
  });
});

describe('logRows: a run that ends with a node still open', () => {
  it('closes the node as stopped', () => {
    const rows = logRows([
      { type: 'flow_start', timestamp: 't0' },
      { type: 'agent_start', node_id: 'a', timestamp: 't1', content: 'Running A',
        turn: { timeline: [{ type: 'tool', tool: 'x', running: true }] } },
      { type: 'flow_stopped', timestamp: 't2' },
    ]);
    expect(rows.map((r) => r.type)).toEqual(['flow_start', 'agent_stopped', 'flow_stopped']);
    expect(rows[1].turn.timeline[0].running).toBe(false);
  });
});
