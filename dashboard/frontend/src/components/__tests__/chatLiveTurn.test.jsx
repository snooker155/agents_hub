import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api', () => ({ getChatLive: vi.fn() }));

// The stream is stood in for so a test can put an event on the channel; the
// real one is an EventSource onto the backend.
const handlers = new Map();
vi.mock('../stream', () => ({
  useStream: () => ({ clientId: 'this-tab' }),
  useChannel: (channel, handler) => { if (channel) handlers.set(channel, handler); },
}));

import { getChatLive } from '../../api';
import {
  LIVE_TURN_LINGER_MS, MAX_LIVE_TEXT, reduceLiveTurn, useLiveChatTurn,
} from '../chatLiveTurn';

/**
 * A turn someone else is running, mirrored here.
 *
 * The rules worth holding onto: the mirror shows what is being generated, it
 * never shows a tab its own echo, and it gets out of the way once the authored
 * transcript arrives — two tabs watching one answer must not end up writing two
 * versions of it.
 */

const emit = (event, channel = 'chat:c1') => act(() => { handlers.get(channel)?.(event); });

beforeEach(() => {
  vi.clearAllMocks();
  handlers.clear();
  getChatLive.mockResolvedValue({ data: { turn: null } });
});

describe('reduceLiveTurn', () => {
  it('opens a turn on turn_start and fills it as the answer streams', () => {
    let turn = reduceLiveTurn(null, { type: 'turn_start', message: 'hi', agent_id: 'assistant' });
    turn = reduceLiveTurn(turn, { type: 'meta', run_id: 'r1' });
    turn = reduceLiveTurn(turn, { type: 'token', token: 'he' });
    turn = reduceLiveTurn(turn, { type: 'token', token: 'llo' });

    expect(turn).toMatchObject({ user: 'hi', runId: 'r1', text: 'hello', status: 'running' });
  });

  it('starts a turn from whatever it first sees, for a tab that joined late', () => {
    const turn = reduceLiveTurn(null, { type: 'token', token: 'mid-sentence' });
    expect(turn.text).toBe('mid-sentence');
  });

  it('keeps thinking and tool steps as the answer is worked out', () => {
    let turn = reduceLiveTurn(null, { type: 'turn_start', message: 'go' });
    turn = reduceLiveTurn(turn, { type: 'think_delta', delta: 'weigh' });
    turn = reduceLiveTurn(turn, { type: 'think', step: 1, content: 'read the file' });
    turn = reduceLiveTurn(turn, { type: 'tool_start', step: 1, tool: 'shell', input: 'ls' });
    turn = reduceLiveTurn(turn, { type: 'tool_end', step: 1, output: 'a.txt' });

    expect(turn.thinking).toEqual([{ kind: 'think', step: 1, content: 'read the file' }]);
    // The completed thought replaces the ticker that was showing it arrive.
    expect(turn.thinkingLive).toBe('');
    expect(turn.tools).toEqual([{ step: 1, tool: 'shell', input: 'ls', output: 'a.txt', error: null }]);
  });

  it('prefers the final response over the streamed tokens', () => {
    // Streamed text accumulates every LLM turn, including re-statements; the
    // done event carries the de-duplicated answer.
    let turn = reduceLiveTurn(null, { type: 'token', token: 'draft draft answer' });
    turn = reduceLiveTurn(turn, { type: 'done', ok: true, response: 'answer', run_id: 'r1' });

    expect(turn).toMatchObject({ text: 'answer', status: 'finished', runId: 'r1' });
  });

  it('marks a failed turn without losing what was said', () => {
    let turn = reduceLiveTurn(null, { type: 'token', token: 'half' });
    turn = reduceLiveTurn(turn, { type: 'done', ok: false, error: 'model died' });

    expect(turn).toMatchObject({ text: 'half', status: 'failed', error: 'model died' });
  });

  it('ends a turn on the stream sentinel when no terminal event arrived', () => {
    let turn = reduceLiveTurn(null, { type: 'token', token: 'x' });
    turn = reduceLiveTurn(turn, { type: 'chat_stream_end' });
    expect(turn.status).toBe('finished');
  });

  it('bounds the text it keeps', () => {
    const turn = reduceLiveTurn(null, { type: 'token', token: 'x'.repeat(MAX_LIVE_TEXT + 100) });
    expect(turn.text).toHaveLength(MAX_LIVE_TEXT);
  });

  it('leaves saves and heartbeats alone', () => {
    const turn = reduceLiveTurn(null, { type: 'chat_saved' });
    expect(turn).toBeNull();
  });
});

describe('useLiveChatTurn', () => {
  it('mirrors a turn started somewhere else', async () => {
    const { result } = renderHook(() => useLiveChatTurn('c1'));
    await waitFor(() => expect(handlers.has('chat:c1')).toBe(true));

    await emit({ type: 'turn_start', message: 'from my phone', origin_client: 'other-tab' });
    await emit({ type: 'token', token: 'working on it', origin_client: 'other-tab' });

    expect(result.current).toMatchObject({ user: 'from my phone', text: 'working on it' });
  });

  it('ignores this tab\'s own echo', async () => {
    // The sending tab renders its own stream; without this it would draw every
    // token twice.
    const { result } = renderHook(() => useLiveChatTurn('c1'));
    await waitFor(() => expect(handlers.has('chat:c1')).toBe(true));

    await emit({ type: 'turn_start', message: 'mine', origin_client: 'this-tab' });
    await emit({ type: 'token', token: 'mine too', origin_client: 'this-tab' });

    expect(result.current).toBeNull();
  });

  it('shows nothing, and asks for nothing, while this tab is sending', async () => {
    const { result } = renderHook(() => useLiveChatTurn('c1', { muted: true }));

    expect(result.current).toBeNull();
    expect(getChatLive).not.toHaveBeenCalled();
    expect(handlers.has('chat:c1')).toBe(false);
  });

  it('catches up on a turn that started before this tab opened the chat', async () => {
    getChatLive.mockResolvedValue({
      data: {
        turn: {
          run_id: 'r7', user_message: 'earlier question', text: 'half an ',
          thinking: [{ kind: 'think', content: 'looking' }], tools: [], status: 'running',
        },
      },
    });

    const { result } = renderHook(() => useLiveChatTurn('c1'));

    await waitFor(() => expect(result.current?.text).toBe('half an '));
    await emit({ type: 'token', token: 'answer', origin_client: 'other-tab' });
    expect(result.current.text).toBe('half an answer');
  });

  it('does not let a late snapshot roll back events already received', async () => {
    let release;
    getChatLive.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const { result } = renderHook(() => useLiveChatTurn('c1'));
    await waitFor(() => expect(handlers.has('chat:c1')).toBe(true));

    await emit({ type: 'token', token: 'live text', origin_client: 'other-tab' });
    await act(async () => { release({ data: { turn: { run_id: 'r1', text: 'stale' } } }); });

    expect(result.current.text).toBe('live text');
  });

  it('steps aside once the stored transcript holds the run', async () => {
    const resolved = new Set();
    const { result, rerender } = renderHook(
      ({ ids }) => useLiveChatTurn('c1', { resolvedRunIds: ids }),
      { initialProps: { ids: resolved } },
    );
    await waitFor(() => expect(handlers.has('chat:c1')).toBe(true));

    await emit({ type: 'turn_start', message: 'q', origin_client: 'other-tab' });
    await emit({ type: 'done', ok: true, response: 'a', run_id: 'r1', origin_client: 'other-tab' });
    expect(result.current.text).toBe('a');

    rerender({ ids: new Set(['r1']) });
    expect(result.current).toBeNull();
  });

  it('drops a finished turn the transcript never took over', async () => {
    // The tab that ran it went away before saving; the mirror is not a record
    // and must not pretend to be one.
    vi.useFakeTimers();
    const { result } = renderHook(() => useLiveChatTurn('c1'));
    await act(async () => {});

    act(() => { handlers.get('chat:c1')?.({ type: 'token', token: 'orphan', origin_client: 'other' }); });
    act(() => { handlers.get('chat:c1')?.({ type: 'chat_stream_end', origin_client: 'other' }); });
    expect(result.current.text).toBe('orphan');

    act(() => { vi.advanceTimersByTime(LIVE_TURN_LINGER_MS + 100); });
    expect(result.current).toBeNull();
    vi.useRealTimers();
  });

  it('drops the mirror when the conversation changes', async () => {
    const { result, rerender } = renderHook(({ id }) => useLiveChatTurn(id),
      { initialProps: { id: 'c1' } });
    await waitFor(() => expect(handlers.has('chat:c1')).toBe(true));
    await emit({ type: 'token', token: 'in c1', origin_client: 'other' });

    rerender({ id: 'c2' });
    expect(result.current).toBeNull();
  });
});
