import { describe, expect, it } from 'vitest';
import { logText } from '../logText';

const t = (key, vars = {}) => `${key}${vars.node || vars.question ? `:${vars.node || vars.question}` : ''}`;

describe('logText: a run log line in the page language', () => {
  it('words the flow lines and node statuses from their type, not the saved English', () => {
    expect(logText(t, { type: 'flow_start', content: 'Starting flow: x' })).toMatchObject({ label: 'flowEditor.log.flow', content: 'flowEditor.log.started' });
    expect(logText(t, { type: 'agent_finish', agent_name: 'Writer', content: 'Completed Writer', output: 'draft' }))
      .toEqual({ label: 'Writer', content: 'flowEditor.log.completed', output: 'draft' });
    expect(logText(t, { type: 'agent_stopped', agent_name: 'W', output: 'Stopped by user' }).output).toBe('');
  });

  it('gives a switch output as the branch it chose, and a saved skip its node name', () => {
    const route = { targets: [{ node_id: 'b', label: 'Writer' }], on: 'topic=news' };
    const out = logText(t, { type: 'agent_finish', agent_name: 'Switch', route, output: 'topic=news → b' });
    expect(out.content).toBe('flowEditor.log.chose');
    expect(out.output).not.toContain('→');
    expect(logText(t, { type: 'node_skip', node_id: 'n3', reason: 'branch_not_selected' }, { n3: 'Editor' }))
      .toMatchObject({ label: 'Editor', content: 'flowEditor.skipReasons.branch_not_selected' });
  });
});
