import { describe, it, expect } from 'vitest';
import { summaryViewMessages, turnsWithoutSummary } from '../summaryView';

const user = (id) => ({ id, role: 'user', content: id });
const node = (id, content, extra = {}) => ({ id, role: 'agent', agent_label: id, content, ...extra });

describe('summaryViewMessages', () => {
  it('shows a turn as its summary, with every node file and link', () => {
    const out = summaryViewMessages([
      user('u1'),
      node('a', 'research', { files: [{ path: 'a.md', op: 'created' }], entities: [{ kind: 'task', id: 't1' }] }),
      node('b', 'draft', { files: [{ path: 'a.md', op: 'edited' }] }),
      { id: 's', role: 'agent', summary: true, content: 'Done: draft written' },
    ]);
    expect(out.map((m) => m.id)).toEqual(['u1', 's']);
    expect(out[1].files).toEqual([{ path: 'a.md', op: 'edited' }]);
    expect(out[1].entities).toEqual([{ kind: 'task', id: 't1' }]);
  });

  it('falls back to the last node answer when the turn has no summary', () => {
    const out = summaryViewMessages([user('u1'), node('a', 'one'), node('b', 'two'), node('c', '')]);
    expect(out.map((m) => m.id)).toEqual(['u1', 'b']);
  });

  it('shows the node at work while the turn is open, and nothing between nodes', () => {
    const msgs = [user('u1'), node('a', 'one'), node('b', 'partial')];
    const live = summaryViewMessages(msgs, { streamingId: 'b', loading: true });
    expect(live.map((m) => m.id)).toEqual(['u1', 'b']);
    expect(live[1].progress).toBe(true);
    expect(summaryViewMessages(msgs, { streamingId: null, loading: true }).map((m) => m.id)).toEqual(['u1']);
  });
});

describe('turnsWithoutSummary', () => {
  const turn = (id, ...agents) => [{ id: `u${id}`, role: 'user', content: `ask ${id}`, turn_id: id }, ...agents];
  it('lists finished turns with two answers or a failure, and no summary', () => {
    const msgs = [
      ...turn('A', node('a1', 'x'), node('a2', 'y')),
      ...turn('B', node('b1', 'only')),
      ...turn('C', node('c1', 'x'), node('c2', 'y'), { id: 's', role: 'agent', summary: true, turn_id: 'C', content: 's' }),
      ...turn('D', node('d1', 'boom', { error: true })),
      ...turn('E', node('e1', 'x'), node('e2', 'y')),
    ];
    const out = turnsWithoutSummary(msgs, { loading: true });
    expect(out.map((x) => x.turn_id)).toEqual(['A', 'D']);
    expect(out[0]).toMatchObject({ user_message: 'ask A', responses: [{ response: 'x' }, { response: 'y' }] });
    expect(out[1].responses[0].error).toBe('boom');
  });

  it('shows a turn being summed up as a reply in progress', () => {
    const msgs = turn('A', node('a1', 'x'), node('a2', 'y'));
    const out = summaryViewMessages(msgs, { writing: new Set(['A']), writingLabel: 'Writing' });
    expect(out[1]).toMatchObject({ streaming: true, working_label: 'Writing', summary: true });
  });
});
