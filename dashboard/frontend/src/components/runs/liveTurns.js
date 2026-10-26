/**
 * The live work of a flow, loop or team run, for any page that shows it.
 *
 * A run started in the background (by an agent in a chat, a loop pass, a
 * schedule) streams what its agents do on channels (common/run_stream.py): a
 * flow run on `run:<flow_run_id>`, a team on `team:<team_run_id>`, a loop on
 * `loop:<loop_run_id>` with each pass on its flow run's channel. The events
 * are a chat turn's own (`token`, `think`, `tool_start`...) stamped with what
 * they belong to: a flow node's `node_id`, a team member's turn `run_id`.
 *
 * What is kept here is the turn of each node or member, folded with the
 * reducer every chat shares (chat/turnEvents.js). A turn that ends stays,
 * marked `done` with its final answer, so its reply does not vanish between
 * the stream's end and the stored record's arrival: a page drops it once the
 * stored record has that run (or shows it as finished, the chat).
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { getStreamReplay } from '../../api';
import { useStream } from '../stream';
import { agentMessage, applyTurnEvent, isDelegationEvent, settleTurn } from '../chat/turnEvents';

const NONE = {};

/** The events that are an agent's own steps (its thoughts, tool calls, text, files). */
export const STEP_EVENTS = new Set([
  'think_delta', 'think', 'plan', 'artifact', 'graph_node_start', 'graph_node_end',
  'tool_start', 'tool_end', 'tool_error', 'token',
]);

/** How each run kind's events say a turn starts and ends, and whose it is. */
export const RUN_KINDS = {
  // A flow node: `node_start` … `node_done`, keyed by the node.
  flow: {
    key: (e) => e.node_id || null,
    start: new Set(['node_start']),
    end: new Set(['node_done']),
    drop: new Set(['node_skip']),
    clear: new Set(['done']),
    // A switch's choice is worded where it is shown (routeText), not its text.
    answer: (e) => (e.ok === false || isRoute(e.route) ? '' : e.response || ''),
    meta: (e) => ({ label: e.agent_label || e.agent_id || e.node_id, agent_id: e.agent_id, input: e.input || '' }),
  },
  // A team member's turn: `turn_start` … `turn_done`, keyed by its run.
  team: {
    key: (e) => e.run_id || null,
    start: new Set(['turn_start']),
    end: new Set(['turn_done']),
    drop: new Set(),
    clear: new Set(['team_done']),
    answer: () => '',
    meta: (e) => ({ label: e.speaker || e.agent_id, agent_id: e.agent_id }),
  },
};

const isRoute = (route) => Boolean(route && typeof route === 'object' && !Array.isArray(route));

// A turn over: settled (nothing left running) with its final answer, and the
// branch it chose when it is a switch.
function finish(entry, { content = '', ok = true, runId = null, route = null } = {}) {
  if (entry.done) return entry;
  return {
    ...entry,
    done: true,
    ok,
    turn: settleTurn({
      ...entry.turn, ...(runId ? { run_id: runId } : {}), ...(isRoute(route) ? { route } : {}),
    }, { content, error: !ok }),
  };
}

/**
 * Fold one event into `{ [key]: { meta, turn, done?, ok? } }`. Pure: an event
 * that changes nothing returns the same object.
 */
export function reduceLiveTurns(state, event, kind = 'flow') {
  const spec = RUN_KINDS[kind] || RUN_KINDS.flow;
  const type = event?.type;
  if (!type) return state;
  if (spec.clear.has(type)) {
    // The run is over: whatever was still open ends with it.
    const open = Object.entries(state).filter(([, e]) => !e.done);
    if (!open.length) return state;
    const next = { ...state };
    for (const [k, e] of open) next[k] = finish(e, { ok: event.ok !== false });
    return next;
  }
  const key = spec.key(event);
  if (!key) return state;
  if (spec.start.has(type)) {
    return { ...state, [key]: { meta: spec.meta(event), turn: agentMessage({ run_id: event.run_id || null }) } };
  }
  if (spec.drop.has(type)) {
    if (!(key in state)) return state;
    const { [key]: _gone, ...rest } = state;
    return rest;
  }
  if (spec.end.has(type)) {
    if (!(key in state)) return state;
    return { ...state, [key]: finish(state[key], {
      content: spec.answer(event), ok: event.ok !== false && !event.error, runId: event.run_id || null,
      route: event.route,
    }) };
  }
  if (STEP_EVENTS.has(type) || isDelegationEvent(event)) {
    // A step of a turn that started before the page was open: its turn
    // begins here, without what went past.
    const entry = state[key] || { meta: spec.meta(event), turn: agentMessage({ run_id: event.run_id || null }) };
    if (entry.done) return state;
    const turn = applyTurnEvent(entry.turn, event);
    if (turn === entry.turn && state[key]) return state;
    return { ...state, [key]: { ...entry, turn } };
  }
  return state;
}

/**
 * A stored run log with the turns of its nodes still at work laid over their
 * open lines: the log has their start, the stream has what they do since.
 */
export function withLiveTurns(logs, live) {
  const keys = Object.keys(live).filter((k) => !live[k].done);
  if (!keys.length) return logs;
  const out = [...logs];
  for (const nodeId of keys) {
    for (let i = out.length - 1; i >= 0; i -= 1) {
      const l = out[i];
      if (l.node_id !== nodeId) continue;
      if (l.type === 'agent_start') out[i] = { ...l, turn: live[nodeId].turn };
      break;
    }
  }
  return out;
}

/**
 * The live turns as replies for a page that also shows the stored record:
 * a turn at work as a reply being written, a finished one until the stored
 * messages have its run (so it never blinks out in between).
 */
export function liveReplies(live, stored = [], idPrefix = 'live') {
  const storedRuns = new Set((stored || []).map((m) => m.run_id).filter(Boolean));
  return Object.entries(live)
    .filter(([, e]) => !e.done || !e.turn.run_id || !storedRuns.has(e.turn.run_id))
    .map(([key, { meta, turn, done }]) => ({
      ...turn, id: `${idPrefix}-${key}`, role: 'agent', streaming: !done, agent_label: meta?.label || key,
    }));
}

/** Follow `channels` while mounted (a falsy one is skipped); `handler` gets every event. */
export function useRunChannels(channels, handler) {
  const { acquireChannel, on } = useStream();
  const hRef = useRef(handler);
  useLayoutEffect(() => { hRef.current = handler; });
  const key = (channels || []).filter(Boolean).join('|');
  useEffect(() => {
    if (!key) return undefined;
    const offs = key.split('|').flatMap((ch) => [
      acquireChannel(ch),
      on(ch, (ev) => hRef.current?.(ev, ch)),
    ]);
    return () => offs.forEach((off) => off());
  }, [key, acquireChannel, on]);
}

/**
 * The turns still being worked on in a run, live: `{ [key]: { meta, turn } }`.
 * `kind` is `flow` (a flow run or a loop pass, keyed by node) or `team` (keyed
 * by the member's turn). Starts empty and again when the channels change.
 */
export function useLiveTurns(channels, kind = 'flow') {
  // Kept with the channels it was built from: other channels start empty.
  const key = (channels || []).filter(Boolean).join('|');
  const [state, setState] = useState({ key: null, turns: NONE });
  // Per channel, the number of the last event folded in; live events up to
  // it are already in (the replay covered them). `pending` holds what came
  // live before the replay did.
  const sync = useRef({ key: null, seq: {}, ready: false, pending: [] });
  // Bumped when a live event shows a gap (one went past between the replay
  // and the subscription): the replay is read again.
  const [resync, setResync] = useState(0);
  const { channelReady } = useStream();

  const fold = useCallback((turns, event) => {
    // A team channel also carries the board and the rounds; only a member's
    // own events and its turn's bounds are turns.
    if (kind === 'team' && STEP_EVENTS.has(event.type) && !event.member_event) return turns;
    return reduceLiveTurns(turns, event, kind);
  }, [kind]);

  useRunChannels(channels, (event, ch) => {
    const s = sync.current;
    if (s.key !== key) return;
    if (!s.ready) {
      s.pending.push([event, ch]);
      return;
    }
    if (event._seq) {
      const last = s.seq[ch] || 0;
      if (event._seq <= last) return;
      if (event._seq > last + 1) {
        s.ready = false;
        s.pending.push([event, ch]);
        setResync((n) => n + 1);
        return;
      }
      s.seq[ch] = event._seq;
    }
    setState((prev) => {
      const base = prev.key === key ? prev.turns : NONE;
      const turns = fold(base, event);
      return turns === base && prev.key === key ? prev : { key, turns };
    });
  });
  // What the channels have streamed so far (common/run_replay.py), then
  // the live events that came in while it loaded.
  useEffect(() => {
    sync.current = { key, seq: {}, ready: false, pending: [] };
    if (!key) return undefined;
    let alive = true;
    const chans = key.split('|');
    // Read once the channels are being sent here (subscribed above), so no
    // event falls between the replay and the live stream.
    Promise.all(chans.map((ch) => (channelReady ? channelReady(ch) : undefined)))
      .then(() => Promise.all(chans.map((ch) => getStreamReplay(ch).then((r) => r.data).catch(() => null))))
      .then((replays) => {
        if (!alive || sync.current.key !== key) return;
        let turns = NONE;
        const seq = {};
        replays.forEach((r, i) => {
          seq[chans[i]] = r?.seq || 0;
          for (const ev of r?.events || []) turns = fold(turns, ev);
        });
        for (const [ev, ch] of sync.current.pending) {
          if (ev._seq && ev._seq <= (seq[ch] || 0)) continue;
          turns = fold(turns, ev);
          if (ev._seq) seq[ch] = ev._seq;
        }
        sync.current = { key, seq, ready: true, pending: [] };
        setState({ key, turns });
      });
    return () => { alive = false; };
  }, [key, fold, resync, channelReady]);

  return state.key === key ? state.turns : NONE;
}
