import { describe, it, expect } from 'vitest';
import { liveReplies, reduceLiveTurns, withLiveTurns } from '../liveTurns';

const fold = (events, kind) => events.reduce((s, e) => reduceLiveTurns(s, e, kind), {});

describe('reduceLiveTurns', () => {
  it('names a node from its steps when its start went past, and keeps a switch’s choice', () => {
    const s = fold([
      { type: 'token', node_id: 'a', agent_id: 'writer', agent_label: 'Writer', token: 'Hi' },
      { type: 'node_start', node_id: 's', agent_label: 'Switch' },
      { type: 'node_done', node_id: 's', ok: true, response: 'no condition held → a',
        route: { targets: [{ node_id: 'a', label: 'Writer' }], on: '' } },
    ]);
    expect(s.a.meta.label).toBe('Writer');
    expect(s.s.turn.content).toBe('');
    expect(s.s.turn.route.targets[0].label).toBe('Writer');
  });

  it('keeps the turn of each flow node at work, with its text and steps', () => {
    const s = fold([
      { type: 'node_start', node_id: 'a', agent_label: 'Writer', agent_id: 'writer', input: 'brief' },
      { type: 'tool_start', node_id: 'a', step: 1, tool: 'web_search', input: '{}' },
      { type: 'token', node_id: 'a', token: 'Hello ' },
      { type: 'token', node_id: 'a', token: 'world' },
    ]);
    expect(s.a.meta).toEqual({ label: 'Writer', agent_id: 'writer', input: 'brief' });
    expect(s.a.turn.content).toBe('Hello world');
    expect(s.a.turn.timeline.some((e) => e.type === 'tool' && e.tool === 'web_search')).toBe(true);
  });

  it('keeps a node that ended with its answer, marked done, and ends the rest with the run', () => {
    const started = fold([
      { type: 'node_start', node_id: 'a', agent_label: 'A' },
      { type: 'tool_start', node_id: 'a', step: 1, tool: 'read_file', input: '{}' },
      { type: 'node_start', node_id: 'b', agent_label: 'B' },
    ]);
    const afterA = reduceLiveTurns(started, { type: 'node_done', node_id: 'a', ok: true, response: 'Final', run_id: 'r-a' });
    expect(afterA.a).toMatchObject({ done: true, ok: true });
    expect(afterA.a.turn).toMatchObject({ content: 'Final', run_id: 'r-a' });
    expect(afterA.a.turn.timeline.some((e) => e.running)).toBe(false);
    expect(afterA.b.done).toBeUndefined();
    // A late step of a finished node changes nothing.
    expect(reduceLiveTurns(afterA, { type: 'token', node_id: 'a', token: 'x' })).toBe(afterA);
    const over = reduceLiveTurns(afterA, { type: 'done', ok: true });
    expect(over.a).toBe(afterA.a);
    expect(over.b.done).toBe(true);
    // A skipped node is no turn.
    expect(reduceLiveTurns(started, { type: 'node_skip', node_id: 'b' }).b).toBeUndefined();
  });

  it('starts a turn from a step when the page opened after the node started', () => {
    const s = fold([{ type: 'token', node_id: 'a', token: 'mid-answer' }]);
    expect(s.a.turn.content).toBe('mid-answer');
  });

  it('keys a team member by its turn and ignores what is no turn', () => {
    const s = fold([
      { type: 'turn_start', run_id: 'r1', speaker: 'Writer', agent_id: 'writer' },
      { type: 'token', run_id: 'r1', token: 'Draft', member_event: true },
      { type: 'message', seq: 3, sender: 'Lead' },
    ], 'team');
    expect(s.r1.meta.label).toBe('Writer');
    expect(s.r1.turn.content).toBe('Draft');
    const done = reduceLiveTurns(s, { type: 'turn_done', run_id: 'r1' }, 'team');
    expect(done.r1).toMatchObject({ done: true });
    expect(done.r1.turn.content).toBe('Draft');
  });

  it('returns the same state for an event that changes nothing', () => {
    const s = fold([{ type: 'node_start', node_id: 'a' }]);
    expect(reduceLiveTurns(s, { type: 'flow_meta', nodes: [] })).toBe(s);
    expect(reduceLiveTurns(s, { type: 'node_done', node_id: 'zzz' })).toBe(s);
  });
});

describe('withLiveTurns', () => {
  it('lays a live turn over the open line of its node only', () => {
    const logs = [
      { type: 'agent_start', node_id: 'a' },
      { type: 'agent_finish', node_id: 'a' },
      { type: 'agent_start', node_id: 'b' },
    ];
    const turn = { content: 'x', timeline: [] };
    const out = withLiveTurns(logs, { b: { turn }, a: { turn } });
    expect(out[2].turn).toBe(turn);
    // a ended: its last line is the finish, which keeps its own record.
    expect(out[0].turn).toBeUndefined();
    expect(out[1].turn).toBeUndefined();
    expect(withLiveTurns(logs, {})).toBe(logs);
  });
});

describe('liveReplies', () => {
  it('shows a finished node until the stored messages have its run', () => {
    const live = fold([
      { type: 'node_start', node_id: 'a', agent_label: 'A' },
      { type: 'node_done', node_id: 'a', ok: true, response: 'Done', run_id: 'r-a' },
      { type: 'node_start', node_id: 'b', agent_label: 'B' },
    ]);
    const before = liveReplies(live, []);
    expect(before.map((m) => [m.agent_label, m.streaming])).toEqual([['A', false], ['B', true]]);
    const after = liveReplies(live, [{ id: 's1', role: 'agent', run_id: 'r-a' }]);
    expect(after.map((m) => m.agent_label)).toEqual(['B']);
  });
});
