import { describe, it, expect, vi, beforeEach } from 'vitest';

const getMyPreferences = vi.fn();
const putMyPreferences = vi.fn(() => Promise.resolve({}));
vi.mock('../../../api/palette', () => ({
  getMyPreferences: (...a) => getMyPreferences(...a),
  putMyPreferences: (...a) => putMyPreferences(...a),
}));

import {
  STEER_MODE_KEY, applyUndelivered, availableModes, buildSteerBubble, effectiveMode,
  inFlightRunId, inHistory, insertSteerBubble, loadSteerMode, markSteerDelivered,
  nextTurnText, readLocalSteerMode, saveSteerMode, steerCaption, takeQueuedSteers,
} from '../steering';
import { buildHistoryPayload } from '../send/buildRequest';
import { handleAgentEvent } from '../send/handleAgentResponse';

const user = (id, content) => ({ id, role: 'user', content });
const agent = (id, extra = {}) => ({ id, role: 'agent', content: '', ...extra });

describe('steer mode choice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('defaults to Steer and remembers the last choice locally and on the account', () => {
    expect(readLocalSteerMode()).toBe('inject');
    saveSteerMode('queue');
    expect(window.localStorage.getItem(STEER_MODE_KEY)).toBe('queue');
    expect(putMyPreferences).toHaveBeenCalledWith({ steer_mode: 'queue' });
    expect(readLocalSteerMode()).toBe('queue');
    saveSteerMode('shout');
    expect(readLocalSteerMode()).toBe('queue');
  });

  it("prefers the account's choice and falls back to this browser's", async () => {
    getMyPreferences.mockResolvedValueOnce({ data: { steer_mode: 'interrupt' } });
    expect(await loadSteerMode()).toBe('interrupt');
    window.localStorage.setItem(STEER_MODE_KEY, 'queue');
    getMyPreferences.mockRejectedValueOnce({ response: { status: 404 } });
    expect(await loadSteerMode()).toBe('queue');
  });
});

describe('turn state', () => {
  it('knows the run of the bubble being written, only for an agent turn', () => {
    const messages = [user('u1', 'hi'), agent('a1', { run_id: 'run-1' })];
    expect(inFlightRunId(messages, { loading: true, targetMode: 'agent' })).toBe('run-1');
    expect(inFlightRunId(messages, { loading: false, targetMode: 'agent' })).toBeNull();
    expect(inFlightRunId(messages, { loading: true, targetMode: 'flow' })).toBeNull();
    // Before the stream's meta event there is no run to talk to yet.
    expect(inFlightRunId([user('u1', 'hi'), agent('a1')], { loading: true, targetMode: 'agent' })).toBeNull();
  });

  it('offers all three modes with a run, only the queue without one, none when idle', () => {
    expect(availableModes({ loading: true, runId: 'r' })).toEqual(['inject', 'interrupt', 'queue']);
    expect(availableModes({ loading: true, runId: null })).toEqual(['queue']);
    expect(availableModes({ loading: false, runId: 'r' })).toEqual([]);
    expect(effectiveMode('interrupt', ['queue'])).toBe('queue');
    expect(effectiveMode('interrupt', ['inject', 'interrupt', 'queue'])).toBe('interrupt');
    expect(effectiveMode('inject', [])).toBeNull();
  });
});

describe('steered bubbles in the transcript', () => {
  it('goes above the bubble the agent is still writing', () => {
    const messages = [user('u1', 'hi'), agent('a1', { run_id: 'r' })];
    const out = insertSteerBubble(messages, buildSteerBubble({ text: 'also this', msgId: 'm1' }));
    expect(out.map((m) => m.id === 'a1' ? 'agent' : m.content)).toEqual(['hi', 'also this', 'agent']);
    expect(out[1].steer).toMatchObject({ msg_id: 'm1', state: 'pending', mode: 'inject' });
  });

  it('marks a delivered message with its step', () => {
    const messages = [buildSteerBubble({ text: 'x', msgId: 'm1' }), agent('a1')];
    const out = markSteerDelivered(messages, 'm1', 2);
    expect(out[0].steer).toMatchObject({ state: 'delivered', after_step: 2 });
    expect(steerCaption(out[0].steer)).toEqual({ key: 'steering.state.delivered', values: { step: 2 } });
    expect(steerCaption({ state: 'delivered', after_step: 0 }).key).toBe('steering.state.deliveredStart');
    expect(steerCaption({ state: 'pending' }).key).toBe('steering.state.pending');
  });

  it('turns undelivered messages into the queue, adding the ones it has no bubble for', () => {
    const messages = [user('u1', 'hi'), buildSteerBubble({ text: 'mine', msgId: 'm1' }), agent('a1')];
    const out = applyUndelivered(messages, [
      { msg_id: 'm1', body: 'mine' },
      { msg_id: 'm2', body: 'from the run page' },
    ], 'a1');
    expect(out.filter((m) => m.steer).map((m) => [m.content, m.steer.state])).toEqual([
      ['mine', 'queued'], ['from the run page', 'queued'],
    ]);
    expect(out[out.length - 1].id).toBe('a1');
    const { messages: rest, texts } = takeQueuedSteers(out);
    expect(texts).toEqual(['mine', 'from the run page']);
    expect(rest.some((m) => m.steer)).toBe(false);
  });

  it('keeps a message out of the history until the model has read it', () => {
    const pending = buildSteerBubble({ text: 'not yet', msgId: 'm1' });
    const read = { ...buildSteerBubble({ text: 'read', msgId: 'm2' }), steer: { msg_id: 'm2', state: 'delivered' } };
    expect(inHistory(pending)).toBe(false);
    expect(inHistory(read)).toBe(true);
    const history = buildHistoryPayload([user('u1', 'hi'), read, pending, { ...agent('a1'), content: 'answer' }]);
    expect(history.map((m) => m.content)).toEqual(['hi', 'read', 'answer']);
  });

  it('sends interrupts first, then what the turn missed, then the queue', () => {
    const text = nextTurnText({
      queue: [{ text: 'later' }, { text: 'now!', interrupt: true }],
      steerTexts: ['missed'],
    });
    expect(text).toBe('now!\n\nmissed\n\nlater');
    expect(nextTurnText({})).toBe('');
  });
});

describe('stream events', () => {
  const run = (event, conversations) => {
    let state = conversations;
    const ctx = {
      convId: 'c1', assistantId: 'a1', isMultiAgent: false, processOpen: false,
      setConversations: (fn) => { state = fn(state); },
      setActiveRunId: () => {}, setSessionId: () => {}, setGraphRun: () => {},
      setProcessInsights: () => {}, mergeArtifact: () => {}, t: (k) => k,
      state: { runId: 'run-1', nodeMsgIds: {} },
    };
    handleAgentEvent(event, ctx);
    return state;
  };

  it('steer_delivered updates the bubble; done.undelivered queues it', () => {
    const start = [{ id: 'c1', messages: [
      user('u1', 'hi'), buildSteerBubble({ text: 'a', msgId: 'm1' }),
      buildSteerBubble({ text: 'b', msgId: 'm2' }), agent('a1', { run_id: 'run-1' }),
    ] }];
    const delivered = run({ type: 'steer_delivered', msg_id: 'm1', after_step: 1 }, start);
    expect(delivered[0].messages[1].steer).toMatchObject({ state: 'delivered', after_step: 1 });
    const done = run({ type: 'done', ok: true, response: 'ok', undelivered: [{ msg_id: 'm2', body: 'b' }] }, delivered);
    expect(done[0].messages[2].steer.state).toBe('queued');
    expect(done[0].messages[3].content).toBe('ok');
  });
});
