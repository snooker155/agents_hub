import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useContextUsage } from '../contextUsage';

/**
 * Context fill, as the build chats learn it from their own event stream.
 *
 * The rules that matter to the user: the meter shows the biggest prompt (not a
 * sum of calls, which would cross the ceiling long before the conversation
 * does), it stays hidden when the model's window is unknown, and the overflow
 * warning both appears on the failure that means it and goes away once a turn
 * is accepted again.
 */
function usageEvent(prompt_tokens, context_window) {
  return { type: 'usage', prompt_tokens, context_window };
}

describe('useContextUsage', () => {
  it('starts empty, so nothing is claimed before a turn has run', () => {
    const { result } = renderHook(() => useContextUsage());
    expect(result.current.usage).toEqual({ used: 0, window: 0, overflow: false });
  });

  it('reports the largest prompt, not the sum of the calls', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe(usageEvent(30000, 200000));
      result.current.observe(usageEvent(90000, 200000));
      result.current.observe(usageEvent(70000, 200000));
    });
    expect(result.current.usage).toEqual({ used: 90000, window: 200000, overflow: false });
  });

  it('takes the turn summary when the streamed events were missed', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe({
        type: 'done',
        usage: { context_used: 120000, context_window: 200000 },
      });
    });
    expect(result.current.usage.used).toBe(120000);
    expect(result.current.usage.window).toBe(200000);
  });

  it('raises the overflow flag on the error that means it', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe({ type: 'error', code: 'context_overflow', error: 'prompt is too long' });
    });
    expect(result.current.usage.overflow).toBe(true);
  });

  it('reads the same verdict off a terminal done event', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe({ type: 'done', ok: false, error_code: 'context_overflow' });
    });
    expect(result.current.usage.overflow).toBe(true);
  });

  it('leaves ordinary failures alone', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe({ type: 'error', error: 'tool failed' });
    });
    expect(result.current.usage.overflow).toBe(false);
  });

  it('drops the warning once a prompt is accepted again', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe({ type: 'error', code: 'context_overflow' });
      result.current.observe(usageEvent(50000, 1000000));
    });
    expect(result.current.usage).toEqual({ used: 50000, window: 1000000, overflow: false });
  });

  it('empties on reset, which is what clearing the chat does', () => {
    const { result } = renderHook(() => useContextUsage());
    act(() => {
      result.current.observe(usageEvent(90000, 200000));
      result.current.observe({ type: 'error', code: 'context_overflow' });
    });
    act(() => result.current.reset());
    expect(result.current.usage).toEqual({ used: 0, window: 0, overflow: false });
  });
});
