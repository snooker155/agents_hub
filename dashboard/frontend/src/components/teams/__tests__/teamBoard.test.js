import { describe, it, expect } from 'vitest';
import { boardChatMessages, memberActivity, quotesToMarkdown, turnRounds } from '../teamBoard';

const t = (key, vars = {}) => `${key}${Object.keys(vars).length ? ` ${JSON.stringify(vars)}` : ''}`;

const board = [
  { seq: 1, round: 0, sender: '(request)', kind: 'goal', content: 'Autumn drink', recipients: ['*'] },
  { seq: 2, round: 1, sender: 'Lead', kind: 'instruction', content: 'Write it', recipients: ['Writer'], run_id: 'lead1' },
  { seq: 3, round: 1, sender: 'Lead', kind: 'instruction', content: 'Check it', recipients: ['Verifier'], run_id: 'lead1' },
  { seq: 4, round: 1, sender: 'Writer', kind: 'message', content: 'Done', recipients: ['*'], run_id: 'w1', cost: 0.5 },
  { seq: 5, round: 1, sender: 'Verifier', kind: 'error', content: '(no contribution: boom)', error: 'boom', recipients: ['*'], run_id: 'v1' },
];

describe('boardChatMessages', () => {
  it('reads the board as a chat: request, round line, one instruction message per lead turn, replies', () => {
    const out = boardChatMessages({ run: { attached: [] }, messages: board, t });
    expect(out.map((m) => m.role)).toEqual(['user', 'system', 'agent', 'agent', 'agent']);
    expect(out[0].content).toBe('Autumn drink');
    expect(out[1].content).toBe('teamDetails.roundN {"n":1}');
    expect(out[2]).toMatchObject({ kind: 'instruction', sender: 'Lead', run_id: 'lead1' });
    expect(out[2].content).toBe('**→ Writer:** Write it\n\n**→ Verifier:** Check it');
    expect(out[3]).toMatchObject({ sender: 'Writer', run_id: 'w1', content: 'Done' });
    expect(out[4]).toMatchObject({ error: true, content: 'teamDetails.noContribution {"error":"boom"}' });
  });

  it('adds a reply being written for each member still working', () => {
    const out = boardChatMessages({ run: {}, messages: board.slice(0, 1), working: [{ run_id: 'x', speaker: 'Writer' }], t });
    expect(out[1]).toMatchObject({ id: 'live:x', sender: 'Writer', streaming: true, kind: 'working' });
    expect(out[1].run_id).toBeUndefined();
  });

  it('gives a working member its steps so far, the ones the run log shows', () => {
    const timeline = [{ type: 'reasoning', content: 'plan' }, { type: 'tool', tool: 'read_file', running: true }];
    const files = [{ path: 'a.md' }];
    const out = boardChatMessages({
      run: {}, messages: board.slice(0, 1), working: [{ run_id: 7, speaker: 'Writer' }],
      trails: { 7: { timeline, files } }, t,
    });
    expect(out[1]).toMatchObject({ id: 'live:7', timeline, files });
  });

  it('gives a streamed member its text being written and its thought', () => {
    const turn = { content: 'Half a draft', thinking_live: 'hmm', timeline: [] };
    const out = boardChatMessages({
      run: {}, messages: board.slice(0, 1), working: [{ run_id: 'r', speaker: 'Writer' }],
      trails: { r: { timeline: [], files: [], turn } }, t,
    });
    expect(out[1]).toMatchObject({ content: 'Half a draft', thinking_live: 'hmm', streaming: true });
  });
});

describe('quotesToMarkdown', () => {
  it('turns quote markers into a Markdown quote', () => {
    const md = quotesToMarkdown('Look:\n<<<QUOTE web>>>\nline one\nline two\n<<<END_QUOTE>>>\nok', t);
    expect(md).toContain('> *teamDetails.quoteFrom {"source":"web"}*');
    expect(md).toContain('> line one\n> line two');
    expect(md).not.toContain('<<<');
  });
});

describe('memberActivity and turnRounds', () => {
  it('says who is working, how each last turn ended and what it cost', () => {
    const members = [{ display_name: 'Writer' }, { display_name: 'Verifier' }, { display_name: 'Idle' }];
    const turns = [
      { run_id: 'w1', speaker: 'Writer', status: 'completed' },
      { run_id: 'v1', speaker: 'Verifier', status: 'failed', error: 'boom' },
      { run_id: 'w2', speaker: 'Writer', status: 'running' },
    ];
    const a = memberActivity(members, turns, board);
    expect(a.Writer).toMatchObject({ status: 'running', turns: 2, cost: 0.5 });
    expect(a.Verifier).toMatchObject({ status: 'failed', error: 'boom' });
    expect(a.Idle.status).toBe('idle');
    expect(turnRounds(board).w1).toEqual({ round: 1, kind: 'message' });
  });
});
