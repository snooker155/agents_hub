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
        ? { ...copy[i], status: 'done' }
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
 * is doing (a card waiting outranks the voice, the voice outranks the work).
 */
export function markState({ recording, transcribing, speaking, busy, waiting, tool, thinking, text }) {
  if (recording) return 'listen';
  if (transcribing) return 'think';
  if (waiting) return 'wait';
  if (speaking) return 'speak';
  if (busy) return stateForTurn({ waiting: false, thinking, tool, text });
  return 'idle';
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
export const DEFAULT_PREFS = { muted: false, voiceOnly: false, voice: '', transcriptOpen: true };

export function readPrefs(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return { ...DEFAULT_PREFS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
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
