/**
 * The demo's request resolution, kept free of MSW so it can be tested as plain
 * functions (see __tests__/handlers.test.js). handlers.js wires these into MSW
 * request handlers; nothing here touches the network or the DOM.
 *
 * Fixtures come from `src/demo/fixtures/fixtures.json` (recorded by the demo
 * seeding script against a real backend) and are keyed `METHOD /path?query`
 * with the query parameters sorted, so the same request made with its
 * parameters in another order still finds its recording.
 */

/** Sorted, re-encoded query string of a URL, without the leading `?`. */
export function sortedQuery(url) {
  const params = [...url.searchParams.entries()]
    // The operator token and the stream's bookkeeping parameters are never
    // part of what a recording was made for.
    .filter(([k]) => k !== 'token' && k !== '_')
    .sort(([a, av], [b, bv]) => (a === b ? (av < bv ? -1 : av > bv ? 1 : 0) : a < b ? -1 : 1));
  return new URLSearchParams(params).toString();
}

/** The fixture keys a request can match, most specific first. */
export function fixtureKeys(method, rawUrl) {
  const url = typeof rawUrl === 'string' ? new URL(rawUrl, 'http://demo.local') : rawUrl;
  const m = method.toUpperCase();
  const q = sortedQuery(url);
  const keys = [];
  if (q) keys.push(`${m} ${url.pathname}?${q}`);
  keys.push(`${m} ${url.pathname}`);
  return keys;
}

// Response shapes the pages destructure, for a GET the recording does not
// cover. A list page given `{}` would crash on `.map`, and one given `[]` where
// it reads `data.items` shows an empty state, so the shape matters more than
// the content. First match wins; anything unlisted gets an empty array.
const FALLBACKS = [
  [/^\/api\/auth\/mode$/, () => ({ mode: 'single', bootstrap_required: false, features: {}, oidc: null })],
  [/^\/api\/auth\/preferences$/, () => ({})],
  [/^\/api\/health$/, () => ({ status: 'ok', demo: true, checks: {} })],
  [/^\/api\/demo$/, () => ({ enabled: true, present: true, workspace: 'demo', counts: {} })],
  // The guided setup never runs in the demo: a model is already there, and
  // nobody touring it should be offered a welcome window.
  [/^\/api\/setup-guide$/, () => ({
    active: false, started_at: null, finished_at: null, dismissed_at: null,
    mode: '', admin: true, multi: false, needs_model: false,
    steps: [], done: 0, total: 0, next: null, complete: false, work: null,
  })],
  [/^\/api\/stats$/, () => ({
    active_runs: 0, available_slots: 0, total_capacity: 0, total_tasks: 0,
    completed_tasks: 0, completion_rate: 0, recent_runs: [],
  })],
  [/^\/api\/connections$/, () => ({ connections: [] })],
  [/^\/api\/views$/, () => ({ views: [], total: 0 })],
  [/^\/api\/teams$/, () => ({ teams: [] })],
  [/^\/api\/run-groups$/, () => ({ groups: [] })],
  [/^\/api\/chat\/sessions$/, () => ({ sessions: [] })],
  [/^\/api\/(sessions|messages|instances|web-logs|audit)$/, () => ({ items: [], total: 0 })],
  // The flat run list (/api/runs) is an array; these nested ones wrap theirs.
  [/^\/api\/(teams\/[^/]+|connections\/[^/]+|playground)\/runs$/, () => ({ runs: [] })],
  [/^\/api\/settings(\/.*)?$/, () => ({})],
  [/^\/api\/features$/, () => ({})],
];

export function fallbackFor(pathname) {
  for (const [re, make] of FALLBACKS) if (re.test(pathname)) return make();
  return [];
}

/**
 * Resolve a GET: the exact recording (with its query), then the recording of
 * the bare path, then a shaped empty value. The auth probe is always answered
 * as single user with no login, whatever was recorded, so the demo never
 * shows a login form.
 */
export function resolveGet(fixtures, rawUrl) {
  const url = typeof rawUrl === 'string' ? new URL(rawUrl, 'http://demo.local') : rawUrl;
  if (url.pathname === '/api/auth/mode') {
    return { source: 'forced', body: fallbackFor(url.pathname) };
  }
  const responses = fixtures?.responses || {};
  for (const key of fixtureKeys('GET', url)) {
    if (Object.prototype.hasOwnProperty.call(responses, key)) {
      return { source: key, body: responses[key] };
    }
  }
  return { source: 'fallback', body: fallbackFor(url.pathname) };
}

let counter = 0;

/** A write is acknowledged and echoed: the demo is read mostly. */
export function resolveWrite(method, body) {
  counter += 1;
  const base = body && typeof body === 'object' && !Array.isArray(body) ? body : {};
  return { ...base, id: base.id || `demo-${method.toLowerCase()}-${Date.now().toString(36)}-${counter}`, ok: true, demo: true };
}

/**
 * Requests answered with a replayed `text/event-stream` instead of JSON: the
 * chat turn, the entity and page chats, generation streams and anything whose
 * path ends in /stream. The shared `/api/stream` connection is separate (see
 * `sharedStreamFrames`), and `/chat/stream-sse` answers JSON like a write.
 */
export function isStreamRequest(method, pathname) {
  if (pathname === '/api/stream' || /^\/api\/stream\//.test(pathname)) return false;
  if (/\/stream-sse$/.test(pathname)) return false;
  if (/\/stream$/.test(pathname)) return true;
  if (method.toUpperCase() !== 'POST') return false;
  return /\/(chat|page-chat|generate)$/.test(pathname);
}

/**
 * One recorded frame in the shape the frontend's consumers read: a flat
 * object with `type`. The recording stores `{event, data}`; frames already in
 * the flat shape pass through. Token text is normalised onto `token`, which is
 * the field every consumer (handleAgentResponse, LiveRunStream, EntityChat)
 * reads.
 */
export function normalizeFrame(frame, runId) {
  if (!frame || typeof frame !== 'object') return null;
  let ev;
  if (frame.type) ev = { ...frame };
  else if (frame.event) ev = { ...(frame.data || {}), type: frame.event };
  else return null;
  if (ev.type === 'token' && ev.token == null) {
    // Recordings taken from a run log carry one line per frame in `text`;
    // the line break is what keeps them apart once the tokens are joined.
    const line = String(ev.text ?? ev.content ?? ev.chunk ?? '');
    ev.token = ev.text != null && !/\s$/.test(line) ? `${line}\n` : line;
  }
  if ((ev.type === 'tool_start' || ev.type === 'tool_end') && !ev.tool) {
    ev.tool = ev.name || 'recorded_step';
  }
  if (runId && ev.run_id == null) ev.run_id = runId;
  return ev;
}

/** The two frame stream a run with no recording gets. */
export const DEFAULT_FRAMES = [
  { event: 'token', data: { token: 'This is a recorded demo.' } },
  { event: 'done', data: { ok: true, response: 'This is a recorded demo.' } },
];

/**
 * The frames to replay for a run: its recording when there is one, else the
 * default two frames. A `meta` frame carrying the run id goes first when the
 * recording has none (the chat reads the run id from it), and `done` always
 * carries `ok` and a `response`, built from the streamed tokens when the
 * recording left it out.
 */
export function framesForRun(streams, runId) {
  const recorded = (runId && streams && Array.isArray(streams[runId])) ? streams[runId] : null;
  const id = recorded ? runId : (runId || 'demo-run');
  const frames = (recorded || DEFAULT_FRAMES).map((f) => normalizeFrame(f, id)).filter(Boolean);
  if (!frames.some((f) => f.type === 'meta')) frames.unshift({ type: 'meta', run_id: id });
  // A turn only ends on `done`; a recording cut before it would leave the
  // chat spinning.
  if (!frames.some((f) => f.type === 'done')) frames.push({ type: 'done', run_id: id });
  const text = frames.filter((f) => f.type === 'token').map((f) => f.token).join('').trim();
  for (const f of frames) {
    if (f.type === 'done') {
      if (f.ok == null) f.ok = true;
      if (f.response == null) f.response = text;
    }
  }
  return frames;
}

/**
 * Which recording a stream request replays. An explicit run id (in the path,
 * the query or the body) wins; otherwise the first recording whose frames
 * name the requested agent; otherwise the first recording at all, so a
 * visitor typing into Chat sees a real recorded turn.
 */
export function pickRunId(streams, { pathname = '', search = '', body = null } = {}) {
  const ids = Object.keys(streams || {});
  const inPath = pathname.match(/\/runs\/([^/]+)\/stream$/);
  if (inPath) return inPath[1];
  const q = new URLSearchParams(search);
  const explicit = q.get('run_id') || body?.run_id;
  if (explicit) return explicit;
  const agent = body?.agent_id || body?.agent;
  if (agent) {
    const hit = ids.find((id) => (streams[id] || []).some((f) => (f.data?.agent_id ?? f.agent_id) === agent));
    if (hit) return hit;
  }
  // A chat turn replays a recorded chat run when there is one.
  if (/\/chat(\/stream)?$/.test(pathname)) {
    const chat = ids.find((id) => /chat/i.test(id));
    if (chat) return chat;
  }
  return ids[0] || null;
}

/** Encode one event as an SSE frame the way the backend does. */
export const sseFrame = (ev) => `data: ${JSON.stringify(ev)}\n\n`;

/** A delay between 60 and 150 ms, the pace of a real token stream. */
export const frameDelay = () => 60 + Math.floor(Math.random() * 91);

/**
 * A ReadableStream replaying `frames` as SSE, `delay()` ms apart; a frame
 * with `demo_pause` waits that long instead, so a scripted step can last as
 * long as a real one would. `wait` is injectable so a test can run the
 * replay without real timers.
 */
export function replayStream(frames, { delay = frameDelay, wait = (ms) => new Promise((r) => setTimeout(r, ms)), keepOpen = false } = {}) {
  const encoder = new TextEncoder();
  let cancelled = false;
  return new ReadableStream({
    async start(controller) {
      for (const { demo_pause: pause, ...ev } of frames) {
        if (cancelled) return;
        await wait(pause ?? delay());
        if (cancelled) return;
        controller.enqueue(encoder.encode(sseFrame(ev)));
      }
      if (keepOpen) {
        // The shared stream stays open like the real one; a closed
        // EventSource would reconnect every three seconds.
        while (!cancelled) {
          await wait(15000);
          if (cancelled) return;
          controller.enqueue(encoder.encode(sseFrame({ channel: '_meta', type: 'heartbeat' })));
        }
        return;
      }
      controller.close();
    },
    cancel() { cancelled = true; },
  });
}

/** The first frames of the shared `/api/stream` connection. */
export const sharedStreamFrames = () => [
  { channel: '_meta', type: 'ready', client_id: 'demo-client', resumed: false },
];
