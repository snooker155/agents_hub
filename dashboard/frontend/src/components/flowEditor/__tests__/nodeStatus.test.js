import { describe, expect, it } from 'vitest';
import { nodeStatusesFor, nodeStatusText } from '../nodeStatus';

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

describe('skipped nodes', () => {
  it('reads a node the run went past as skipped, with why, and one taken from before as done', () => {
    const rows = nodeStatusesFor(nodes, [
      { type: 'flow_start' },
      { type: 'node_skip', node_id: 'a', reason: 'already_done' },
      { type: 'node_skip', node_id: 'b', reason: 'branch_not_selected' },
      { type: 'node_skip', node_id: 'c' },
      { type: 'flow_finish' },
    ]);
    expect(rows.map((r) => r.status)).toEqual(['done', 'skipped', 'skipped']);
    expect(rows[1].reason).toBe('branch_not_selected');
  });

  it('says why on the badge and in full on hover', () => {
    const t = (key, vars = {}) => (key.startsWith('flowEditor.skipShort.missing') ? vars.defaultValue : key);
    expect(nodeStatusText(t, { status: 'skipped', reason: 'branch_not_selected' }))
      .toEqual({ badge: 'flowEditor.skipShort.branch_not_selected', title: 'flowEditor.skipReasons.branch_not_selected' });
    expect(nodeStatusText(t, { status: 'done' }).badge).toBe('flowEditor.status.done');
  });
});
