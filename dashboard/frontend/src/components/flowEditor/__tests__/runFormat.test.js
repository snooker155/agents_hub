import { describe, expect, it } from 'vitest';
import { entityNodeText, reconstructRunMessages, rerunBubbleText, routeText } from '../runFormat';

describe('reconstructRunMessages', () => {
  it('keeps the files a node line names, linked', () => {
    const files = [{ op: 'add', path: 'out/report.md', url: '/artifacts?file=1' }];
    const [bubble] = reconstructRunMessages({ events: [
      { type: 'agent_finish', node_id: 'n1', agent_name: 'Writer', run_id: 'r1', output: 'done', files },
    ] });
    expect(bubble.files).toEqual(files);
    expect(bubble.agent_label).toBe('Writer');
  });

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
    expect(task).toEqual([{ id: 't1-u', role: 'user', content: '', rerun, turn_id: 't1' }]);
    const t = (key, vars) => `${key}:${vars.node}`;
    expect(rerunBubbleText(t, rerun)).toBe('flowFlowChat.rerunRest:Writer');
    expect(rerunBubbleText(t, { ...rerun, mode: 'single' })).toBe('flowFlowChat.rerunSingle:Writer');
  });
});

describe('switch bubbles', () => {
  const t = (key, vars = {}) => `${key}${vars.targets ? `:${vars.targets}` : ''}`;

  it('says which step a switch chose and on what value', () => {
    const route = { targets: [{ node_id: 'b', label: 'Writer' }], on: 'topic=news\nmore' };
    expect(routeText(t, route)).toBe('flowFlowChat.branchChosen:**Writer**\n\n`topic=news more`');
  });

  it('says what a switch tested and where it led, without quoting the value', () => {
    const tt = (key, vars = {}) => {
      if (key === 'flowFlowChat.switchOps.contains') return `contains «${vars.value}»`;
      if (key === 'flowFlowChat.switchDefault') return `${vars.key}: none (${vars.conds}) → ${vars.targets}`;
      if (key === 'flowFlowChat.switchMatched') return `${vars.key} ${vars.cond} → ${vars.targets}`;
      return key;
    };
    const checked = [{ op: 'contains', value: 'холодн', target: 'viz' }];
    const route = (detail, label) => ({ targets: [{ node_id: 'x', label }], on: 'post=Saved…', detail });
    expect(routeText(tt, route({ key: 'post', checked, matched: [], default: true }, 'Writer')))
      .toBe('post: none (contains «холодн») → **Writer**');
    expect(routeText(tt, route({ key: 'post', checked, matched: checked, default: false }, 'Visualizer')))
      .toBe('post contains «холодн» → **Visualizer**');
  });

  it('joins the conditions of the route that held with and or or, each with its key', () => {
    const tt = (key, vars = {}) => ({
      'flowFlowChat.switchOps.contains': `contains «${vars.value}»`,
      'flowFlowChat.switchOps.not_empty': 'is filled',
      'flowFlowChat.switchCond': `${vars.key} ${vars.cond}`,
      'flowFlowChat.switchAnd': 'and',
      'flowFlowChat.switchOr': 'or',
      'flowFlowChat.switchRouteMatched': `${vars.conds} → ${vars.targets}`,
      'flowFlowChat.switchRouteDefault': `none → ${vars.targets}`,
    }[key] || key);
    const conditions = [
      { key: 'post', op: 'contains', value: 'cold', held: true },
      { key: 'tone', op: 'not_empty', value: '', held: true },
    ];
    const route = (routes, isDefault, label) => ({
      targets: [{ node_id: 'x', label }], detail: { routes, default: isDefault },
    });
    expect(routeText(tt, route([{ target: 'x', match: 'all', conditions, held: true }], false, 'Viz')))
      .toBe('post contains «cold» and tone is filled → **Viz**');
    expect(routeText(tt, route([{ target: 'x', match: 'any', conditions, held: true }], false, 'Viz')))
      .toBe('post contains «cold» or tone is filled → **Viz**');
    expect(routeText(tt, route([{ target: 'v', match: 'all', conditions, held: false }], true, 'Writer')))
      .toBe('none → **Writer**');
  });

  it('says when no branch matched', () => {
    expect(routeText(t, { targets: [], on: '' })).toBe('flowFlowChat.noBranch');
  });

  it('falls back to the node summary for a node that chose nothing', () => {
    expect(entityNodeText(t, { summary: 'a=1' })).toBe('a=1');
  });

  it('keeps a saved switch line as a route, not its English summary', () => {
    const bubbles = reconstructRunMessages({ events: [
      { type: 'agent_finish', timestamp: 'x', node_id: 's', agent_name: 'Switch', entity: true,
        output: 'topic=news → b', route: { targets: [{ node_id: 'b', label: 'Writer' }], on: 'topic=news' } },
    ] });
    expect(bubbles[0]).toMatchObject({ role: 'agent', content: '', route: { on: 'topic=news' } });
  });
});

describe('reconstructRunMessages: a turn sent in the Summary view', () => {
  it('ends with its summary bubble', () => {
    const bubbles = reconstructRunMessages({ events: [
      { type: 'flow_start', timestamp: 't0' },
      { type: 'agent_start', timestamp: 't1', node_id: 'a', input: 'Latest user message: write it' },
      { type: 'agent_finish', timestamp: 't2', node_id: 'a', agent_name: 'Writer', output: 'draft' },
      { type: 'flow_summary', timestamp: 't3', content: 'Summary of the turn', output: 'Wrote the draft.' },
      { type: 'flow_finish', timestamp: 't4' },
    ] });
    expect(bubbles.map((b) => [b.role, b.content, Boolean(b.summary)])).toEqual([
      ['user', 'write it', false], ['agent', 'draft', false], ['agent', 'Wrote the draft.', true],
    ]);
  });
});

describe('reconstructRunMessages: a summary written after the fact', () => {
  it('goes under the turn it names, not at the end', () => {
    const bubbles = reconstructRunMessages({ events: [
      { type: 'flow_start', timestamp: 't0', turn_id: 'A' },
      { type: 'agent_start', timestamp: 't1', node_id: 'a', input: 'Latest user message: first' },
      { type: 'agent_finish', timestamp: 't2', node_id: 'a', output: 'one' },
      { type: 'flow_finish', timestamp: 't3' },
      { type: 'flow_start', timestamp: 't4' },
      { type: 'agent_start', timestamp: 't5', node_id: 'a', input: 'Latest user message: second' },
      { type: 'agent_finish', timestamp: 't6', node_id: 'a', output: 'two' },
      { type: 'flow_finish', timestamp: 't7' },
      { type: 'flow_summary', timestamp: 't8', turn_id: 'A', output: 'sum A' },
      // An older record's turn is named by its flow_start timestamp.
      { type: 'flow_summary', timestamp: 't9', turn_id: 't4', output: 'sum B' },
    ] });
    expect(bubbles.map((b) => b.content)).toEqual(['first', 'one', 'sum A', 'second', 'two', 'sum B']);
  });
});
