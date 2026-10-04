import api, { API_ORIGIN } from '../../api';

/*
 * The terminal's connection to the hub (dashboard/backend/routes/terminal.py,
 * docs/terminal.md), kept apart from xterm so it can be tested on its own.
 *
 * Every socket needs a fresh one-time ticket from POST .../ticket: the
 * session token never rides the URL, and a spent ticket is refused. The hub
 * keeps the shell alive for a grace period when the socket drops, so a
 * dropped socket is not the end: this reconnects with the session id, the
 * hub replays its recent output, and the shell carries on. It stops trying
 * once the grace period has passed, when the shell exited, or when another
 * window took the session over.
 */

export const mintTerminalTicket = async (kind, id, sessionId) => {
  const { data } = await api.post(
    `/terminal/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/ticket`,
    sessionId ? { session_id: sessionId } : {},
  );
  return data;
};

export const terminalSocketUrl = (kind, id, ticket, { cols, rows } = {}) => {
  const origin = API_ORIGIN || (typeof window !== 'undefined' ? window.location.origin : '');
  const url = new URL(`${origin}/api/terminal/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/ws`);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('ticket', ticket);
  if (cols) url.searchParams.set('cols', String(cols));
  if (rows) url.searchParams.set('rows', String(rows));
  return url.toString();
};

/** Where a resumable session id is remembered per target, so hiding the
 * panel or reloading the page comes back to the same shell. */
export const storageKey = (kind, id) => `agents_hub_terminal:${kind}:${id}`;

const remember = (kind, id, sessionId) => {
  try {
    if (sessionId) window.sessionStorage.setItem(storageKey(kind, id), sessionId);
    else window.sessionStorage.removeItem(storageKey(kind, id));
  } catch {
    // Privacy mode: the session just cannot be resumed after a reload.
  }
};

export const rememberedSession = (kind, id) => {
  try {
    return window.sessionStorage.getItem(storageKey(kind, id)) || null;
  } catch {
    return null;
  }
};

const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];

const errorDetail = (err) => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return err?.message || 'error';
};

/**
 * Status, as handed to `onStatus(status, info)`:
 * - `connecting`: minting a ticket and opening the socket;
 * - `open`: attached; `info` is the hub's session message;
 * - `reconnecting`: the socket dropped, trying again within the grace period;
 * - `ended`: the shell exited or the session ended (`info.why`: exited,
 *   closed, grace, idle, shutdown, or expired when reconnecting ran out);
 * - `taken`: another window attached to the same session;
 * - `error`: the hub refused (`info.detail`, `info.status`).
 */
export function createTerminalConnection({
  kind, id, onOutput, onStatus = () => {}, onNotice = () => {},
  getSize = () => ({}), mintTicket = mintTerminalTicket,
  WebSocketImpl = typeof WebSocket !== 'undefined' ? WebSocket : null,
  now = () => Date.now(),
}) {
  let ws = null;
  let sessionId = rememberedSession(kind, id);
  let graceMs = 60_000;
  let droppedAt = null;
  let attempt = 0;
  let timer = null;
  let stopped = false;
  let final = false;
  let status = 'idle';

  const setStatus = (next, info = {}) => {
    status = next;
    onStatus(next, info);
  };

  const finish = (next, info) => {
    final = true;
    sessionId = null;
    remember(kind, id, null);
    setStatus(next, info);
  };

  const scheduleReconnect = () => {
    if (stopped || final) return;
    if (droppedAt == null) droppedAt = now();
    if (!sessionId || now() - droppedAt > graceMs) {
      finish('ended', { why: 'expired' });
      return;
    }
    setStatus('reconnecting', { attempt: attempt + 1 });
    const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    timer = setTimeout(() => { timer = null; open(); }, delay);
  };

  async function open() {
    if (stopped) return;
    final = false;
    if (status !== 'reconnecting') setStatus('connecting');
    let ticket;
    try {
      ticket = (await mintTicket(kind, id, sessionId)).ticket;
    } catch (err) {
      if (stopped) return;
      const code = err?.response?.status;
      if (sessionId && code === 410) {
        // The session is gone (grace passed, idle, closed): start afresh.
        sessionId = null;
        remember(kind, id, null);
        onNotice('expired');
        droppedAt = null;
        attempt = 0;
        open();
        return;
      }
      if (!code && sessionId) {
        // The hub itself is unreachable: keep trying within the grace period.
        scheduleReconnect();
        return;
      }
      finish('error', { detail: errorDetail(err), status: code });
      return;
    }
    if (stopped || !WebSocketImpl) return;
    const socket = new WebSocketImpl(terminalSocketUrl(kind, id, ticket, getSize()));
    socket.binaryType = 'arraybuffer';
    ws = socket;

    socket.onmessage = (event) => {
      if (socket !== ws) return;
      if (typeof event.data !== 'string') {
        onOutput(new Uint8Array(event.data));
        return;
      }
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (!msg || typeof msg !== 'object') return;
      if (msg.type === 'session') {
        if (msg.resumed) onNotice('resumed');
        sessionId = msg.session_id;
        remember(kind, id, sessionId);
        graceMs = (Number(msg.grace_seconds) || 60) * 1000;
        droppedAt = null;
        attempt = 0;
        setStatus('open', msg);
      } else if (msg.type === 'exit') {
        finish('ended', { why: msg.why || 'exited', reason: msg.reason, code: msg.code });
      } else if (msg.type === 'taken_over') {
        final = true;
        setStatus('taken');
      } else if (msg.type === 'error') {
        if (msg.status === 410 && sessionId) {
          sessionId = null;
          remember(kind, id, null);
          onNotice('expired');
          return;
        }
        finish('error', { detail: msg.detail, status: msg.status });
      }
    };
    socket.onerror = () => {};
    socket.onclose = () => {
      if (socket !== ws) return;
      ws = null;
      if (stopped || final) return;
      if (status === 'open' || status === 'reconnecting' || status === 'connecting') {
        if (!sessionId && status !== 'open') {
          // Never got a session (an expired resume answered over the socket):
          // open a new one.
          droppedAt = null;
          attempt = 0;
          open();
          return;
        }
        scheduleReconnect();
      }
    };
  }

  const sendJson = (payload) => {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(payload));
  };

  return {
    connect: () => { stopped = false; open(); },
    /** Start again after an end, an error or a take-over: resumes the
     * remembered session if there still is one, else opens a new shell. */
    reconnect: () => {
      if (timer) { clearTimeout(timer); timer = null; }
      if (ws) { const old = ws; ws = null; try { old.close(); } catch { /* already closed */ } }
      stopped = false;
      final = false;
      droppedAt = null;
      attempt = 0;
      status = 'idle';
      sessionId = rememberedSession(kind, id);
      open();
    },
    send: (data) => sendJson({ type: 'input', data }),
    resize: (cols, rows) => sendJson({ type: 'resize', cols, rows }),
    /** Drop the socket. With `end`, also end the shell now; without, the hub
     * keeps it for its grace period and the next panel resumes it. */
    close: ({ end = false } = {}) => {
      stopped = true;
      if (timer) { clearTimeout(timer); timer = null; }
      if (end) {
        sendJson({ type: 'close' });
        remember(kind, id, null);
        sessionId = null;
      }
      if (ws) { const old = ws; ws = null; try { old.close(); } catch { /* already closed */ } }
    },
    get sessionId() { return sessionId; },
    get status() { return status; },
  };
}

export default createTerminalConnection;
