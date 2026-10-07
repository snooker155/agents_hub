/**
 * The Assistant page's state that is worth testing without a browser: how a
 * streamed turn becomes the transcript, which state the live mark shows, what
 * the page remembers between visits, and whether the personal / service
 * switch is offered at all.
 */
import { stateForTurn } from '../liveMark/activity';

// ── the transcript ───────────────────────────────────────────────────────────

/** The stored thread as feed items: the rich trace when there is one. */
export function feedFromThread(data) {
  const trace = data?.trace;
  if (Array.isArray(trace) && trace.length) return trace;
  return (data?.messages || []).map((m) => ({ k: m.role, text: m.content }));
}

const closeLive = (items) => items.map((it) => (it.live ? { ...it, live: false } : it));

/**
 * The feed with one stream event applied. Kept in step with EntityChat and
 * with `trace_item_for` in chat/entity_chat.py, so a reloaded thread reads
 * the same as the live turn did.
 */
export function applyTurnEvent(feed, ev) {
  switch (ev?.type) {
    case 'token': {
      const last = feed[feed.length - 1];
      if (last?.k === 'assistant' && last.live) {
        return [...feed.slice(0, -1), { ...last, text: last.text + (ev.token || '') }];
      }
      return [...feed, { k: 'assistant', text: ev.token || '', live: true }];
    }
    case 'tool_start':
      return [...closeLive(feed), { k: 'tool', tool: ev.tool, input: ev.input, status: 'running' }];
    case 'tool_end':
    case 'tool_error': {
      const i = feed.map((it) => it.k === 'tool' && it.status === 'running').lastIndexOf(true);
      if (i === -1) return feed;
      const copy = [...feed];
      copy[i] = ev.type === 'tool_end'
        ? { ...copy[i], status: ev.status === 'error' ? 'error' : 'done', ...(ev.memory ? { memory: ev.memory } : {}) }
        : { ...copy[i], status: 'error', error: ev.error || '' };
      return copy;
    }
    case 'think':
      return ev.content ? [...closeLive(feed), { k: 'thinking', text: ev.content }] : feed;
    case 'message': {
      if (!ev.content) return closeLive(feed);
      const last = feed[feed.length - 1];
      if (last?.k === 'assistant' && last.live) {
        return [...feed.slice(0, -1), { k: 'assistant', text: ev.content }];
      }
      return [...closeLive(feed), { k: 'assistant', text: ev.content }];
    }
    case 'error':
      return [...closeLive(feed), { k: 'error', text: ev.error || 'error' }];
    default:
      return feed;
  }
}

/** The latest answer in the feed, for the line under the mark. */
export function lastAnswer(feed) {
  for (let i = feed.length - 1; i >= 0; i -= 1) {
    if (feed[i].k === 'assistant' && feed[i].text) return feed[i].text;
    if (feed[i].k === 'user') return '';
  }
  return '';
}

/** The first paragraph of a reply: what is read aloud and shown large. */
export function firstParagraph(text) {
  const t = String(text || '').replace(/^\s+/, '');
  const brk = t.search(/\n\s*\n/);
  return (brk === -1 ? t : t.slice(0, brk)).trim();
}

// ── the live mark ────────────────────────────────────────────────────────────

/**
 * What the mark shows: listening while the talk button is held, thinking
 * while a recording is turned into text, and during a turn whatever the turn
 * is doing (a card waiting outranks the rest; a running tool outranks the
 * voice, which then only says what that tool is doing, so the step's scene
 * stays on show; the voice outranks thinking and writing).
 */
export function markState({ recording, transcribing, speaking, busy, waiting, tool, input, thinking, text }) {
  if (recording) return 'listen';
  if (transcribing) return 'think';
  if (waiting) return 'wait';
  if (busy && tool) return stateForTurn({ tool, input });
  if (speaking) return 'speak';
  if (busy) return stateForTurn({ waiting: false, thinking, tool, text });
  return 'idle';
}

// ── hands-free listening ─────────────────────────────────────────────────────

/**
 * How the page listens: `hold` (push to talk), `conversation` (once started,
 * it listens after every answer until the conversation is ended) and `wake`
 * (it waits for its name, as on a phone or a smart speaker).
 */
export const LISTEN_MODES = ['hold', 'conversation', 'wake'];

/**
 * What the open microphone is for right now:
 *
 *  - `monitor` while the assistant works or speaks, in every mode: only a
 *    spoken stop (and, hands-free, a yes or no for a waiting card);
 *  - `command` in a conversation, or in wake mode once called: what is said
 *    is the next turn;
 *  - `wake` in wake mode otherwise: only the wake phrase is listened for;
 *  - `off`: nothing is listened for.
 */
export function earPhase({ busy, speaking, conversing, listen, awake }) {
  if (busy || speaking) return 'monitor';
  if (conversing) return 'command';
  if (listen === 'wake') return awake ? 'command' : 'wake';
  return 'off';
}

/**
 * The ear's settings for a phase (useHandsFree tune()). A stop is short, so
 * while the assistant works a long stretch is thrown away untranscribed, and
 * while it speaks the voice has to be louder than its own from the speakers.
 * A command gets a longer pause before it counts as finished: people stop to
 * think mid-sentence.
 */
export function earTuning(phase, { speaking = false, pending = false, maxSeconds = 60 } = {}) {
  if (phase === 'monitor') {
    return { silenceMs: 500, maxSeconds: pending ? 4 : 3, overflow: 'drop', sensitivity: speaking ? 2 : 1.3 };
  }
  if (phase === 'wake') return { silenceMs: 700, maxSeconds: 10, overflow: 'drop', sensitivity: 1 };
  return { silenceMs: 1100, maxSeconds: Math.min(60, maxSeconds || 60), overflow: 'cut', sensitivity: 1 };
}

// ── the switch ───────────────────────────────────────────────────────────────

/**
 * Personal / service: only for an administrator in multi mode, where the
 * server says a service thread exists beside the personal one. In single
 * mode the one thread is already the service thread, so there is nothing to
 * switch.
 */
export function showModeSwitch(meta) {
  return Boolean(meta?.service_available);
}

// ── what the page remembers ──────────────────────────────────────────────────

export const PREFS_KEY = 'agents_hub_assistant_prefs';
export const DEFAULT_PREFS = {
  muted: false, voiceOnly: false, voice: '', transcriptOpen: true,
  // hold | conversation | wake (LISTEN_MODES); the wake phrase ('' = "assistant"
  // in any interface language); a spoken stop while the assistant works.
  listen: 'hold', wakePhrase: '', voiceStop: true,
};

export function readPrefs(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const prefs = { ...DEFAULT_PREFS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
    if (!LISTEN_MODES.includes(prefs.listen)) prefs.listen = DEFAULT_PREFS.listen;
    return prefs;
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export function writePrefs(prefs, storage = globalThis.localStorage) {
  try { storage?.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* storage unavailable */ }
}

// ── links in an answer ───────────────────────────────────────────────────────

/** Whether a link points at a page of this app ("show on screen"). */
export function isScreenLink(href) {
  return typeof href === 'string' && href.startsWith('/') && !href.startsWith('//');
}

/** The agent a delegation step hands work to, from its tool input. */
export function delegateOf(input) {
  let data = input;
  if (typeof input === 'string') {
    try { data = JSON.parse(input); } catch { return ''; }
  }
  return (data && typeof data === 'object' && (data.agent_id || data.agent)) || '';
}
