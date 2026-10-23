import { act, render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// With `timeline` a run's thoughts, tool calls and text show one after
// another in the order they happened (the decomposer in the new task form),
// not as a "Thinking (N)" group, a tool list and the text at the end.

let emit = null;
vi.mock('../stream', () => ({ useChannel: (_channel, fn) => { emit = fn; } }));
vi.mock('../../api/steering', () => ({
  steerRun: vi.fn(),
  listRunSteering: () => Promise.resolve({ data: { messages: [] } }),
}));

import LiveRunStream from '../LiveRunStream';

const show = (props) => render(
  <I18nProvider><LiveRunStream sessionId="decompose:1" {...props} /></I18nProvider>,
);

const play = () => act(() => {
  emit({ type: 'meta', run_id: 'r1', agent_id: 'decomposer' });
  emit({ type: 'thinking', run_id: 'r1', message: 'first thought' });
  emit({ type: 'tool_start', run_id: 'r1', tool: 'add_subtask', input: '{}' });
  emit({ type: 'tool_end', run_id: 'r1', output: 'ok' });
  emit({ type: 'thinking', run_id: 'r1', message: 'second thought' });
  emit({ type: 'tool_start', run_id: 'r1', tool: 'get_task', input: '{}' });
  emit({ type: 'token', run_id: 'r1', token: 'Plan ' });
  emit({ type: 'token', run_id: 'r1', token: 'ready' });
});

describe('LiveRunStream timeline', () => {
  beforeEach(() => { Element.prototype.scrollIntoView = () => {}; });

  it('lists thoughts, tool calls and text in the order they happened', () => {
    const { container } = show({ timeline: true });
    play();
    const text = container.textContent;
    const order = ['first thought', 'add_subtask', 'second thought', 'get_task', 'Plan ready']
      .map((s) => text.indexOf(s));
    expect(order.every((at) => at >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(text).not.toMatch(/Thinking \(2\)/);
  });

  it('keeps the grouped view without it', () => {
    const { container } = show({});
    play();
    expect(container.textContent).toMatch(/Thinking \(2\)/);
    expect(container.textContent).not.toMatch(/first thought/);
  });

  it('bare drops the panel frame', () => {
    const { container } = show({ timeline: true, bare: true });
    play();
    expect(container.firstChild.className).not.toMatch(/border|shadow/);
  });
});
