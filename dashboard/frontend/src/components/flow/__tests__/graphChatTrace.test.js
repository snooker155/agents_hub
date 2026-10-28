import { describe, it, expect } from 'vitest';
import { graphChangeEvent, graphChatMessages, isGraphBuilderTool } from '../graphChatTrace';

describe('graphChatTrace', () => {
  it('turns stored node, edge and removal items into change entries', () => {
    const msgs = graphChatMessages({ trace: [
      { k: 'user', text: 'build it' },
      { k: 'tool', tool: 'read_file', status: 'running' },
      { k: 'node', label: 'Payments' },
      { k: 'node', label: '', edge: 'a → b' },
      { k: 'tool', tool: 'removed edge e1', status: 'done' },
      { k: 'tool', tool: 'removed n9', status: 'done' },
      { k: 'assistant', text: 'Done.' },
    ] });
    const reply = msgs[1];
    expect(reply.content).toBe('Done.');
    expect(reply.changes.map((c) => [c.action, c.kind, c.label])).toEqual([
      ['added', 'node', 'Payments'], ['added', 'edge', 'a → b'],
      ['removed', 'edge', 'e1'], ['removed', 'node', 'n9'],
    ]);
    expect(reply.timeline[0]).toMatchObject({ type: 'tool', tool: 'read_file', status: 'ok' });
  });

  it('maps live graph events and spots the builder tools', () => {
    expect(graphChangeEvent({ type: 'graph_node', node: { id: 'n1', data: { label: 'X' } } }))
      .toMatchObject({ action: 'added', kind: 'node', label: 'X' });
    expect(graphChangeEvent({ type: 'graph_edge', edge: { source: 'a', target: 'b' } }).label).toBe('a → b');
    expect(graphChangeEvent({ type: 'graph_layout' })).toBeNull();
    expect(isGraphBuilderTool('add_graph_node')).toBe(true);
    expect(isGraphBuilderTool('read_file')).toBe(false);
  });
});
