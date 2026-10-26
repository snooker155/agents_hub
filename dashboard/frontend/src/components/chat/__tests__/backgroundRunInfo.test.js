import { describe, it, expect } from 'vitest';
import { activityText, footerText, progressText, runPagePath, workChannels } from '../backgroundRunInfo';

const t = (key, vars = {}) => `${key}${Object.keys(vars).length ? ` ${JSON.stringify(vars)}` : ''}`;

describe('background run info', () => {
  it('words each kind of progress', () => {
    expect(progressText(t, { kind: 'flow', progress: { done: 2, total: 5 } }))
      .toBe('chat.background.flowProgress {"done":2,"total":5}');
    expect(progressText(t, { kind: 'loop', progress: { iteration: 2, max_iterations: 5, score: 61.4, pass: { done: 1, total: 3 } } }))
      .toBe('chat.background.loopIteration {"n":2,"max":5} · chat.background.loopScore {"score":61} · chat.background.flowProgress {"done":1,"total":3}');
    expect(progressText(t, { kind: 'team', progress: { round: 2, max_rounds: 6 } }))
      .toBe('chat.background.teamRound {"n":2,"max":6}');
    expect(progressText(t, { kind: 'flow', progress: null })).toBe('');
  });

  it('says in the footer what was started and how far it got, in short', () => {
    expect(footerText(t, { kind: 'flow', name: 'Digest', progress: { done: 0, total: 4 } }))
      .toBe('chat.background.footer.flowProgress {"name":"Digest","done":0,"total":4}');
    expect(footerText(t, { kind: 'flow', name: 'Digest', progress: null }))
      .toBe('chat.background.footer.flow {"name":"Digest"}');
    expect(footerText(t, { kind: 'loop', name: 'L', progress: { iteration: 2 } }))
      .toBe('chat.background.footer.loopStep {"name":"L","done":2}');
    expect(footerText(t, { kind: 'team', name: 'T', progress: { round: 1, max_rounds: 3 } }))
      .toBe('chat.background.footer.teamProgress {"name":"T","done":1,"total":3}');
  });

  it('opens each kind on its own page', () => {
    expect(runPagePath({ kind: 'flow', entity_id: 'f', run_id: 'r' })).toBe('/flows/f/runs/r');
    expect(runPagePath({ kind: 'loop', entity_id: 'l', run_id: 'r' })).toBe('/loops/l/runs/r');
    expect(runPagePath({ kind: 'team', entity_id: 't', run_id: 'r' })).toBe('/teams/t?run=r');
  });

  it('follows a loop through its pass, not its own channel', () => {
    expect(workChannels({ kind: 'loop', channels: ['loop:1', 'run:p'] })).toEqual(['run:p']);
    expect(workChannels({ kind: 'team', channels: ['team:1'] })).toEqual(['team:1']);
  });

  it('says what a turn does now', () => {
    const tool = { timeline: [{ type: 'tool', tool: 'read_file', input: '{"path": "a.md"}', running: true }] };
    expect(activityText(t, tool)).not.toBe('chat.background.working');
    expect(activityText(t, { content: '', timeline: [{ type: 'text', text: 'Hi' }] })).toBe('chat.background.writing');
    expect(activityText(t, { timeline: [] })).toBe('chat.background.working');
  });
});
