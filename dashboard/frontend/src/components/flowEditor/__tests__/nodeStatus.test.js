import { describe, expect, it } from 'vitest';
import { nodeStatusesFor } from '../nodeStatus';

const nodes = ['a', 'b', 'c'].map((id) => ({ id, data: { label: id.toUpperCase() } }));
const statuses = (logs, flag) => nodeStatusesFor(nodes, logs, flag).map((s) => s.status);

describe('nodeStatusesFor', () => {
  it('reads a pass in progress', () => {
    expect(statuses([
      { type: 'flow_start' },
      { type: 'agent_start', node_id: 'a' }, { type: 'agent_finish', node_id: 'a' },
      { type: 'agent_start', node_id: 'b' },
    ])).toEqual(['done', 'running', 'pending']);
  });

  it('leaves nothing running or pending once the run was stopped', () => {
    expect(statuses([
      { type: 'flow_start' },
      { type: 'agent_start', node_id: 'a' }, { type: 'agent_finish', node_id: 'a' },
      { type: 'agent_start', node_id: 'b' },
      { type: 'flow_stopped' },
    ], true)).toEqual(['done', 'stopped', 'ready']);
  });

  it('shows a node the server stopped as stopped, not done', () => {
    expect(statuses([
      { type: 'flow_start' }, { type: 'agent_start', node_id: 'a' }, { type: 'agent_stopped', node_id: 'a' },
    ])[0]).toBe('stopped');
  });

  it('counts only the latest pass', () => {
    expect(statuses([
      { type: 'flow_start' }, { type: 'agent_start', node_id: 'a' }, { type: 'agent_error', node_id: 'a' },
      { type: 'flow_finish' },
      { type: 'flow_start' },
    ])).toEqual(['pending', 'pending', 'pending']);
  });
});
