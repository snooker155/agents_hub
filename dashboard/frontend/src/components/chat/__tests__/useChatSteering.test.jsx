import { act, renderHook, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const steerRun = vi.fn();
vi.mock('../../../api/steering', () => ({ steerRun: (...a) => steerRun(...a) }));
vi.mock('../../../api/palette', () => ({
  getMyPreferences: () => Promise.reject({ response: { status: 404 } }),
  putMyPreferences: () => Promise.resolve({}),
}));

import { useChatSteering } from '../useChatSteering';

const TURN = [
  { id: 'u1', role: 'user', content: 'write the report' },
  { id: 'a1', role: 'agent', content: '', run_id: 'run-1' },
];

function useHarness(sendMessage) {
  const [conversations, setConversations] = useState([{ id: 'c1', messages: TURN }]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const steering = useChatSteering({
    conversations, setConversations, currentConvId: 'c1', input, setInput, loading,
    sendMessage, stopGeneration: () => setLoading(false), targetMode: 'agent',
  });
  return { steering, conversations, input, setInput, setLoading, setConversations };
}

describe('useChatSteering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('steers the running turn and shows the message above the answer', async () => {
    steerRun.mockResolvedValue({ data: { message: { msg_id: 'm1' }, next: 'wait' } });
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage));
    expect(result.current.steering.modes).toEqual(['inject', 'interrupt', 'queue', 'system']);
    expect(result.current.steering.activeMode).toBe('inject');
    act(() => result.current.setInput('use last year as the baseline'));
    await act(async () => { await result.current.steering.steer(); });
    expect(steerRun).toHaveBeenCalledWith('run-1', 'use last year as the baseline', 'inject');
    const messages = result.current.conversations[0].messages;
    expect(messages.map((m) => m.id === 'a1' ? 'agent' : m.content))
      .toEqual(['write the report', 'use last year as the baseline', 'agent']);
    expect(messages[1].steer).toMatchObject({ msg_id: 'm1', state: 'pending' });
    expect(result.current.input).toBe('');
  });

  it('sends an instruction as a system message and never queues it', async () => {
    steerRun.mockResolvedValue({ data: { message: { msg_id: 's1' }, next: 'wait' } });
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage));
    act(() => result.current.steering.setMode('system'));
    await act(async () => { await result.current.steering.steer('answer in German from now on'); });
    expect(steerRun).toHaveBeenCalledWith('run-1', 'answer in German from now on', 'system');
    const bubble = result.current.conversations[0].messages.find((m) => m.steer);
    expect(bubble.steer).toMatchObject({ msg_id: 's1', mode: 'system', state: 'pending' });
    act(() => result.current.setLoading(false));
    await waitFor(() => expect(result.current.steering.busy).toBe(false));
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it('queues a message and sends it when the turn ends', async () => {
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage));
    act(() => result.current.steering.setMode('queue'));
    expect(window.localStorage.getItem('agents_hub_steer_mode')).toBe('queue');
    await act(async () => { await result.current.steering.steer('then summarise it'); });
    expect(steerRun).not.toHaveBeenCalled();
    expect(result.current.steering.queued.map((q) => q.text)).toEqual(['then summarise it']);
    act(() => result.current.setLoading(false));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith('then summarise it'));
    expect(result.current.steering.queued).toEqual([]);
  });

  it('sends what the turn did not take as the next turn', async () => {
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage));
    act(() => result.current.setConversations([{ id: 'c1', messages: [
      TURN[0],
      { id: 's1', role: 'user', content: 'missed it', steer: { msg_id: 'm1', state: 'queued' } },
      { id: 's2', role: 'user', content: 'read in time', steer: { msg_id: 'm2', state: 'delivered', after_step: 1 } },
      { id: 's3', role: 'user', content: 'never confirmed', steer: { msg_id: 'm3', state: 'pending' } },
      { ...TURN[1], content: 'done' },
    ] }]));
    act(() => result.current.setLoading(false));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith('missed it\n\nnever confirmed'));
    // The delivered one stays where it is: the model already read it.
    expect(result.current.conversations[0].messages.map((m) => m.content)).toContain('read in time');
    expect(result.current.conversations[0].messages.filter((m) => m.steer).map((m) => m.steer.state))
      .toEqual(['delivered']);
  });

  it('interrupts the run and sends the message once it has stopped', async () => {
    steerRun.mockResolvedValue({ data: { next: 'send' } });
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage));
    act(() => result.current.steering.setMode('interrupt'));
    await act(async () => { await result.current.steering.steer('stop, wrong file'); });
    expect(steerRun).toHaveBeenCalledWith('run-1', 'stop, wrong file', 'interrupt');
    act(() => result.current.setLoading(false));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith('stop, wrong file'));
  });

  it('falls back to the queue when the turn ended before the message landed', async () => {
    steerRun.mockRejectedValue({ response: { status: 409, data: { detail: { status: 'completed' } } } });
    const { result } = renderHook(() => useHarness(vi.fn()));
    await act(async () => { await result.current.steering.steer('late words'); });
    expect(result.current.steering.queued.map((q) => q.text)).toEqual(['late words']);
  });

  it('puts the queue back in the box when the person presses Stop', async () => {
    const sendMessage = vi.fn();
    const { result } = renderHook(() => useHarness(sendMessage));
    act(() => result.current.steering.setMode('queue'));
    await act(async () => { await result.current.steering.steer('after that, email it'); });
    act(() => result.current.steering.stop());
    await waitFor(() => expect(result.current.input).toBe('after that, email it'));
    expect(sendMessage).not.toHaveBeenCalled();
    expect(result.current.steering.notice).toBe('restored');
  });

  it('offers only the queue before the run id is known', () => {
    const { result } = renderHook(() => {
      const [conversations, setConversations] = useState([{ id: 'c1', messages: [TURN[0], { id: 'a1', role: 'agent', content: '' }] }]);
      return useChatSteering({
        conversations, setConversations, currentConvId: 'c1', input: '', setInput: () => {},
        loading: true, sendMessage: vi.fn(), stopGeneration: () => {}, targetMode: 'agent',
      });
    });
    expect(result.current.modes).toEqual(['queue']);
    expect(result.current.activeMode).toBe('queue');
  });
});
