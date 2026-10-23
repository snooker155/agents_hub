import { describe, expect, it } from 'vitest';
import { reconstructRunMessages, rerunBubbleText } from '../runFormat';

describe('reconstructRunMessages', () => {
  it('builds a user bubble from the first node input, then an agent bubble per terminal event', () => {
    const run = {
      events: [
        { type: 'flow_start', timestamp: '2026-01-01T00:00:00.000Z' },
        {
          type: 'agent_start', timestamp: '2026-01-01T00:00:01.000Z', node_id: 'n1',
          input: 'Some context\nLatest user message:\nHello there\n=== Attached files ===\nfile.txt',
        },
        {
          type: 'agent_finish', timestamp: '2026-01-01T00:00:02.000Z', node_id: 'n1',
          agent_name: 'Researcher', output: 'Hi! Here is what I found.',
        },
      ],
    };

    const bubbles = reconstructRunMessages(run);
    expect(bubbles).toHaveLength(2);
    expect(bubbles[0]).toMatchObject({ role: 'user', content: 'Hello there' });
    expect(bubbles[1]).toMatchObject({
      role: 'agent', agent_label: 'Researcher', content: 'Hi! Here is what I found.', error: false,
    });
  });

  it('only takes the first node-input as the user message within a turn', () => {
    const run = {
      events: [
        { type: 'flow_start', timestamp: 't0' },
        { type: 'agent_start', timestamp: 't1', node_id: 'n1', input: 'Latest user message:\nFirst node input' },
        { type: 'agent_start', timestamp: 't2', node_id: 'n2', input: 'Latest user message:\nSecond node input' },
        { type: 'agent_finish', timestamp: 't3', node_id: 'n2', agent_name: 'Writer', output: 'done' },
      ],
    };
    const bubbles = reconstructRunMessages(run);
    const userBubbles = bubbles.filter((b) => b.role === 'user');
    expect(userBubbles).toHaveLength(1);
    expect(userBubbles[0].content).toBe('First node input');
  });

  it('marks agent_error and agent_stopped bubbles as errors, agent_finish as not', () => {
    const run = {
      events: [
        { type: 'flow_start', timestamp: 't0' },
        { type: 'agent_start', timestamp: 't1', node_id: 'n1', input: '' },
        { type: 'agent_error', timestamp: 't2', node_id: 'n1', agent_name: 'A', error: 'boom' },
        { type: 'agent_stopped', timestamp: 't3', node_id: 'n2', agent_name: 'B' },
      ],
    };
    const bubbles = reconstructRunMessages(run);
    expect(bubbles.every((b) => b.role === 'agent')).toBe(true);
    expect(bubbles.every((b) => b.error === true)).toBe(true);
  });

  it('returns an empty array for a run with no events', () => {
    expect(reconstructRunMessages({ events: [] })).toEqual([]);
    expect(reconstructRunMessages(null)).toEqual([]);
  });

  it('shows a run again from a node as what it was, not as the node input', () => {
    const rerun = { node_id: 'n2', label: 'Writer', mode: 'rest' };
    const chat = reconstructRunMessages({ events: [
      { type: 'flow_start', timestamp: 't1', turn: { rerun } },
      { type: 'agent_start', node_id: 'n2', input: 'OUTPUT FROM PREVIOUS AGENTS', timestamp: 't2' },
      { type: 'agent_finish', node_id: 'n2', output: 'new draft', timestamp: 't3' },
    ] });
    expect(chat[0]).toMatchObject({ role: 'user', content: '', rerun });
    expect(chat).toHaveLength(2);
    // A task run says so on the flow_resume line before its flow_start.
    const task = reconstructRunMessages({ events: [
      { type: 'flow_resume', rerun, timestamp: 't0' },
      { type: 'flow_start', timestamp: 't1' },
      { type: 'agent_start', node_id: 'n2', input: 'x', timestamp: 't2' },
    ] });
    expect(task).toEqual([{ id: 't1-u', role: 'user', content: '', rerun }]);
    const t = (key, vars) => `${key}:${vars.node}`;
    expect(rerunBubbleText(t, rerun)).toBe('flowFlowChat.rerunRest:Writer');
    expect(rerunBubbleText(t, { ...rerun, mode: 'single' })).toBe('flowFlowChat.rerunSingle:Writer');
  });
});
