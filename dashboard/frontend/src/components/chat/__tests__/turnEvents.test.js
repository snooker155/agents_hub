import { describe, expect, it } from 'vitest';
import { agentMessage, applyTurnEvent, settleTurn } from '../turnEvents';
import { messagesFromTrace } from '../traceMessages';

const fold = (events, msg = agentMessage({ id: 'a' })) => events.reduce(applyTurnEvent, msg);

describe('applyTurnEvent: one reading of a turn for every chat', () => {
  it('builds the timeline in execution order', () => {
    const msg = fold([
      { type: 'think', content: 'look at the loop' },
      { type: 'token', token: 'Checking ' },
      { type: 'token', token: 'it.' },
      { type: 'tool_start', tool: 'read_file', input: 'a.py' },
      { type: 'tool_end', output: 'print(1)', status: 'ok' },
      { type: 'artifact', op: 'modify', path: 'a.py', additions: 2, deletions: 1 },
      { type: 'entity_changed', action: 'added', kind: 'step', label: 'Fetch' },
    ]);
    expect(msg.timeline.map((e) => e.type)).toEqual(['reasoning', 'text', 'tool', 'artifact', 'change']);
    expect(msg.timeline[1].text).toBe('Checking it.');
    expect(msg.timeline[2]).toMatchObject({ tool: 'read_file', output: 'print(1)', running: false, status: 'ok' });
    expect(msg.files).toEqual([{ op: 'modify', path: 'a.py', additions: 2, deletions: 1 }]);
    expect(msg.changes).toEqual([{ type: 'change', action: 'added', kind: 'step', label: 'Fetch' }]);
  });

  it('marks a call that raised as failed', () => {
    const msg = fold([{ type: 'tool_start', tool: 'x' }, { type: 'tool_error', error: 'boom' }]);
    expect(msg.timeline[0]).toMatchObject({ output: 'ERROR: boom', error: true, running: false });
  });

  it('keeps the live thought until the finished one lands', () => {
    let msg = fold([{ type: 'think_delta', delta: 'hm' }]);
    expect(msg.thinking_live).toBe('hm');
    msg = applyTurnEvent(msg, { type: 'think', content: 'hmm, done' });
    expect(msg.thinking_live).toBe('');
  });

  it('nests a delegated run inside its card', () => {
    const msg = fold([
      { type: 'delegation_start', run_id: 'c1', agent_id: 'w', input: 'go' },
      { type: 'tool_start', tool: 'grep', delegation: true, run_id: 'c1' },
      { type: 'delegation_end', run_id: 'c1', ok: true, output: 'done' },
    ]);
    expect(msg.timeline).toHaveLength(1);
    expect(msg.timeline[0]).toMatchObject({ type: 'delegation', running: false, ok: true });
    expect(msg.timeline[0].timeline[0]).toMatchObject({ type: 'tool', tool: 'grep' });
  });

  it('returns the same message for an event that is not its business', () => {
    const msg = agentMessage({ id: 'a' });
    expect(applyTurnEvent(msg, { type: 'meta', run_id: 'r' })).toBe(msg);
  });

  it('settles a turn: nothing left running, the final reply as its text', () => {
    const msg = settleTurn(fold([{ type: 'tool_start', tool: 'x' }]), { content: 'Final' });
    expect(msg.timeline[0].running).toBe(false);
    expect(msg.content).toBe('Final');
  });
});

describe('messagesFromTrace: a reloaded build chat reads like the live one', () => {
  it('folds the stored feed into user and agent messages', () => {
    const msgs = messagesFromTrace([
      { k: 'user', text: 'add a step' },
      { k: 'thinking', text: 'which step' },
      { k: 'tool', tool: 'modify_loop_tool', input: 'lp_1', output: '{"ok": true}', status: 'done' },
      { k: 'text', text: 'Adding it.' },
      { k: 'artifact', op: 'add', path: 'x.md', additions: 3, deletions: 0 },
      { k: 'entity', action: 'added', kind: 'step', label: 'Fetch' },
      { k: 'assistant', text: 'Done.', run_id: 'r1' },
      { k: 'user', text: 'again' },
      { k: 'tool', tool: 'stopped by you', status: 'done' },
      { k: 'error', text: 'boom' },
    ]);
    expect(msgs.map((m) => m.role)).toEqual(['user', 'agent', 'user', 'agent']);
    expect(msgs[1]).toMatchObject({ content: 'Done.', run_id: 'r1' });
    expect(msgs[1].timeline.map((e) => e.type)).toEqual(['reasoning', 'tool', 'text', 'artifact', 'change']);
    expect(msgs[1].timeline[1].status).toBe('ok');
    expect(msgs[1].files).toHaveLength(1);
    expect(msgs[1].changes).toHaveLength(1);
    // The stop marker is not a tool the agent called.
    expect(msgs[3].timeline).toEqual([]);
    expect(msgs[3]).toMatchObject({ error: true, content: 'boom' });
  });

  it('reads an older trace whose tool steps carry no output', () => {
    const msgs = messagesFromTrace([
      { k: 'user', text: 'hi' },
      { k: 'tool', tool: 't', status: 'error', error: 'nope' },
      { k: 'assistant', text: 'ok' },
    ]);
    expect(msgs[1].timeline[0]).toMatchObject({ output: 'ERROR: nope', status: 'error' });
  });
});
