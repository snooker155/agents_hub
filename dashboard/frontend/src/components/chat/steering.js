/**
 * Talking to a turn that is still running (docs/steering.md).
 *
 * While an agent works, the composer stays open and a message can go three
 * ways: `inject` ("Steer") is posted to the run and read by the agent before
 * its next model step; `interrupt` stops the turn and sends the message as
 * the next one; `queue` waits and sends it when the turn ends. The last
 * choice is remembered: in the signed-in person's preferences (`steer_mode`
 * on /api/auth/preferences) when that endpoint exists, and in this browser's
 * localStorage always, the rule lib/modelView.js follows.
 *
 * A steered message is a user bubble carrying `steer: {msg_id, mode, state,
 * after_step}`, placed just above the bubble the agent is still writing.
 * `state` is `pending` until the backend says the model saw it
 * (`steer_delivered`), then `delivered`; a message the turn ended without
 * taking comes back in `done.undelivered` and turns `queued`, to be sent as
 * the next turn with whatever else was queued.
 *
 * Pure helpers only, so the rules can be checked without a page.
 */
import { getMyPreferences, putMyPreferences } from '../../api/palette';
import { genId } from './turnState';

export const STEER_MODE_KEY = 'agents_hub_steer_mode';
export const STEER_MODES = ['inject', 'interrupt', 'queue'];
export const DEFAULT_STEER_MODE = 'inject';

const valid = (v) => (STEER_MODES.includes(v) ? v : null);

export function readLocalSteerMode() {
  try {
    return valid(window.localStorage.getItem(STEER_MODE_KEY)) || DEFAULT_STEER_MODE;
  } catch {
    return DEFAULT_STEER_MODE;
  }
}

/** The account's choice when there is one, else this browser's. */
export async function loadSteerMode() {
  try {
    const { data } = await getMyPreferences();
    const fromAccount = valid(data?.steer_mode);
    if (fromAccount) return fromAccount;
  } catch {
    // 404 outside multi mode, 401 signed out, or offline: the local copy.
  }
  return readLocalSteerMode();
}

/** Write locally at once; tell the account in the background, ignoring a 404. */
export function saveSteerMode(mode) {
  const v = valid(mode);
  if (!v) return;
  try { window.localStorage.setItem(STEER_MODE_KEY, v); } catch { /* storage blocked */ }
  try {
    Promise.resolve(putMyPreferences({ steer_mode: v })).catch(() => {});
  } catch { /* no api in this context */ }
}

/**
 * The run a message can steer right now: the run id of the bubble the agent
 * is still writing, which the stream's `meta` event put on it. Null before
 * that event, when no turn is running, and for flows and teams, which have
 * no single run to talk to.
 */
export function inFlightRunId(messages, { loading, targetMode }) {
  if (!loading || targetMode !== 'agent') return null;
  const list = messages || [];
  const last = list[list.length - 1];
  if (!last || last.role !== 'agent') return null;
  return last.run_id || null;
}

/** The modes offered right now: all three with a run to talk to, else only
 * the queue while a turn is running, else none. */
export function availableModes({ loading, runId }) {
  if (!loading) return [];
  return runId ? STEER_MODES : ['queue'];
}

/** The mode a send uses: the chosen one when it is on offer, else the queue. */
export function effectiveMode(chosen, modes) {
  if (!modes.length) return null;
  return modes.includes(chosen) ? chosen : 'queue';
}

export function buildSteerBubble({ text, msgId, mode = 'inject', state = 'pending', afterStep = null }) {
  return {
    id: genId(),
    role: 'user',
    content: text,
    steer: { msg_id: msgId, mode, state, after_step: afterStep },
    createdAt: new Date().toISOString(),
  };
}

// Above the bubble still being written (the last message, from the agent),
// or at the end when there is none.
function insertIndex(messages, assistantId) {
  if (assistantId) {
    const idx = messages.findIndex((m) => m.id === assistantId);
    if (idx !== -1) return idx;
  }
  const last = messages[messages.length - 1];
  return last && last.role === 'agent' ? messages.length - 1 : messages.length;
}

export function insertSteerBubble(messages, bubble, assistantId = null) {
  const list = [...(messages || [])];
  list.splice(insertIndex(list, assistantId), 0, bubble);
  return list;
}

export function markSteerDelivered(messages, msgId, afterStep) {
  return (messages || []).map((m) => (
    m.steer && m.steer.msg_id === msgId
      ? { ...m, steer: { ...m.steer, state: 'delivered', after_step: afterStep ?? null } }
      : m
  ));
}

/**
 * Fold `done.undelivered` into the transcript: each message the turn never
 * took turns `queued`. One that has no bubble here (steered from the run
 * page, or from another tab) gets one, so it is sent with the rest.
 */
export function applyUndelivered(messages, undelivered, assistantId = null) {
  let list = [...(messages || [])];
  for (const item of undelivered || []) {
    if (!item || !item.msg_id) continue;
    const idx = list.findIndex((m) => m.steer && m.steer.msg_id === item.msg_id);
    if (idx === -1) {
      list = insertSteerBubble(list, buildSteerBubble({
        text: String(item.body || ''), msgId: item.msg_id, state: 'queued',
      }), assistantId);
    } else {
      list[idx] = { ...list[idx], steer: { ...list[idx].steer, state: 'queued' } };
    }
  }
  return list;
}

/**
 * Split the steer bubbles the next turn should carry off: what stays, and the
 * texts to send. Once a turn has ended that is every queued one and every one
 * still pending (the turn closed without saying it took them, a Stop for
 * instance), since a pending message can no longer reach that turn.
 */
export function takeQueuedSteers(messages, states = ['queued', 'pending']) {
  const texts = [];
  const rest = [];
  for (const m of messages || []) {
    if (m.steer && states.includes(m.steer.state)) texts.push(String(m.content || ''));
    else rest.push(m);
  }
  return { messages: rest, texts };
}

/** Whether a message belongs in the history sent with the next turn: a
 * steered message only once the model has read it. */
export function inHistory(message) {
  return !message.steer || message.steer.state === 'delivered';
}

/**
 * What to send once a turn has ended: interrupts first (the person asked for
 * those to go now), then the messages the turn did not take, then the queue,
 * each as a paragraph of one message.
 */
export function nextTurnText({ queue = [], steerTexts = [] }) {
  const interrupts = queue.filter((q) => q.interrupt).map((q) => q.text);
  const later = queue.filter((q) => !q.interrupt).map((q) => q.text);
  return [...interrupts, ...steerTexts, ...later]
    .map((s) => String(s || '').trim())
    .filter(Boolean)
    .join('\n\n');
}

/** The caption under a steered bubble, as an i18n key and its values. */
export function steerCaption(steer) {
  if (!steer) return null;
  if (steer.state === 'delivered') {
    return steer.after_step
      ? { key: 'steering.state.delivered', values: { step: steer.after_step } }
      : { key: 'steering.state.deliveredStart', values: {} };
  }
  const known = ['pending', 'queued', 'expired', 'interrupted', 'failed'];
  return known.includes(steer.state) ? { key: `steering.state.${steer.state}`, values: {} } : null;
}
