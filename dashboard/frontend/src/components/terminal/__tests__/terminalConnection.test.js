import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// The connection mints a one-time ticket before every socket, streams the
// shell's bytes, and when the socket drops comes back to the same session
// within the hub's grace period. These tests drive it with a fake socket and
// a fake ticket call, no xterm involved.

vi.mock('../../../api', () => ({ default: { post: vi.fn() }, API_ORIGIN: 'http://hub.test' }));

import api from '../../../api';
import {
  createTerminalConnection, mintTerminalTicket, rememberedSession, storageKey, terminalSocketUrl,
} from '../terminalConnection';

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    FakeSocket.instances.push(this);
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
  message(data) { this.onmessage?.({ data: typeof data === 'string' ? data : data }); }
  json(obj) { this.message(JSON.stringify(obj)); }
  drop() { this.readyState = 3; this.onclose?.({ code: 1006 }); }
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const last = () => FakeSocket.instances[FakeSocket.instances.length - 1];

const make = (overrides = {}) => {
  const out = [];
  const statuses = [];
  const notices = [];
  let n = 0;
  const mintTicket = vi.fn(async (_kind, _id, sessionId) => ({ ticket: `T${++n}`, sessionId }));
  const conn = createTerminalConnection({
    kind: 'run', id: 'r1',
    onOutput: (bytes) => out.push(new TextDecoder().decode(bytes)),
    onStatus: (s, info) => statuses.push([s, info]),
    onNotice: (what) => notices.push(what),
    getSize: () => ({ cols: 100, rows: 30 }),
    mintTicket, WebSocketImpl: FakeSocket,
    ...overrides,
  });
  return { conn, out, statuses, notices, mintTicket };
};

beforeEach(() => {
  FakeSocket.instances = [];
  window.sessionStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => vi.useRealTimers());

describe('terminalSocketUrl and the ticket call', () => {
  it('builds a ws URL with the ticket and the size', () => {
    const url = new URL(terminalSocketUrl('replica', 'inst_1', 'abc', { cols: 90, rows: 20 }));
    expect(url.protocol).toBe('ws:');
    expect(url.pathname).toBe('/api/terminal/replica/inst_1/ws');
    expect(url.searchParams.get('ticket')).toBe('abc');
    expect(url.searchParams.get('cols')).toBe('90');
  });

  it('posts the session id only when resuming', async () => {
    api.post.mockResolvedValue({ data: { ticket: 'x' } });
    await mintTerminalTicket('run', 'r1');
    expect(api.post).toHaveBeenLastCalledWith('/terminal/run/r1/ticket', {});
    await mintTerminalTicket('run', 'r1', 's9');
    expect(api.post).toHaveBeenLastCalledWith('/terminal/run/r1/ticket', { session_id: 's9' });
  });
});

describe('createTerminalConnection', () => {
  it('opens with a ticket, streams bytes, sends input and resize', async () => {
    const { conn, out, statuses, mintTicket } = make();
    conn.connect();
    await flush();
    expect(mintTicket).toHaveBeenCalledWith('run', 'r1', null);
    const ws = last();
    expect(ws.url).toContain('ticket=T1');
    expect(ws.url).toContain('cols=100');
    ws.json({ type: 'session', session_id: 'S1', resumed: false, grace_seconds: 60, container: 'c1' });
    expect(statuses.at(-1)[0]).toBe('open');
    expect(window.sessionStorage.getItem(storageKey('run', 'r1'))).toBe('S1');
    ws.message(new TextEncoder().encode('hello').buffer);
    expect(out).toEqual(['hello']);
    conn.send('ls\r');
    conn.resize(120, 40);
    expect(ws.sent).toEqual([{ type: 'input', data: 'ls\r' }, { type: 'resize', cols: 120, rows: 40 }]);
  });

  it('reconnects to the same session after a drop, with a fresh ticket', async () => {
    const { conn, notices, mintTicket, statuses } = make();
    conn.connect();
    await flush();
    last().json({ type: 'session', session_id: 'S1', resumed: false, grace_seconds: 60 });
    last().drop();
    expect(statuses.at(-1)[0]).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(600);
    expect(mintTicket).toHaveBeenLastCalledWith('run', 'r1', 'S1');
    expect(FakeSocket.instances).toHaveLength(2);
    expect(last().url).toContain('ticket=T2');
    last().json({ type: 'session', session_id: 'S1', resumed: true, grace_seconds: 60 });
    expect(notices).toContain('resumed');
    expect(statuses.at(-1)[0]).toBe('open');
  });

  it('gives up once the grace period has passed', async () => {
    let clock = 1_000;
    const { conn, statuses } = make({ now: () => clock });
    conn.connect();
    await flush();
    last().json({ type: 'session', session_id: 'S1', grace_seconds: 5 });
    last().drop();
    clock += 6_000;
    await vi.advanceTimersByTimeAsync(600);
    last().drop();
    expect(statuses.at(-1)).toEqual(['ended', { why: 'expired' }]);
    expect(rememberedSession('run', 'r1')).toBeNull();
  });

  it('starts a new shell when the remembered session is gone', async () => {
    window.sessionStorage.setItem(storageKey('run', 'r1'), 'OLD');
    let calls = 0;
    const mintTicket = vi.fn(async (_k, _i, sessionId) => {
      calls += 1;
      if (sessionId) {
        const err = new Error('gone');
        err.response = { status: 410, data: { detail: 'ended' } };
        throw err;
      }
      return { ticket: `T${calls}` };
    });
    const { conn, notices } = make({ mintTicket });
    conn.connect();
    await flush();
    await flush();
    expect(mintTicket.mock.calls.map((c) => c[2])).toEqual(['OLD', null]);
    expect(notices).toEqual(['expired']);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('shows a refusal and does not retry', async () => {
    const err = new Error('no');
    err.response = { status: 409, data: { detail: 'This run executes in the hub\'s own process (local mode)' } };
    const { conn, statuses } = make({ mintTicket: vi.fn().mockRejectedValue(err) });
    conn.connect();
    await flush();
    expect(statuses.at(-1)[0]).toBe('error');
    expect(statuses.at(-1)[1].detail).toMatch(/local mode/);
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it('reports the exit and a take-over without reconnecting', async () => {
    const { conn, statuses } = make();
    conn.connect();
    await flush();
    last().json({ type: 'session', session_id: 'S1' });
    last().json({ type: 'exit', code: 0, why: 'exited', reason: 'the shell exited' });
    last().drop();
    expect(statuses.at(-1)).toEqual(['ended', { why: 'exited', reason: 'the shell exited', code: 0 }]);
    await vi.advanceTimersByTimeAsync(2000);
    expect(FakeSocket.instances).toHaveLength(1);

    const second = make();
    second.conn.connect();
    await flush();
    last().json({ type: 'session', session_id: 'S2' });
    last().json({ type: 'taken_over' });
    last().drop();
    expect(second.statuses.at(-1)[0]).toBe('taken');
  });

  it('ending the session tells the hub and forgets it', async () => {
    const { conn } = make();
    conn.connect();
    await flush();
    const ws = last();
    ws.json({ type: 'session', session_id: 'S1' });
    conn.close({ end: true });
    expect(ws.sent.at(-1)).toEqual({ type: 'close' });
    expect(rememberedSession('run', 'r1')).toBeNull();
  });

  it('hiding keeps the session for the next panel', async () => {
    const { conn } = make();
    conn.connect();
    await flush();
    last().json({ type: 'session', session_id: 'S1' });
    conn.close();
    expect(rememberedSession('run', 'r1')).toBe('S1');
    expect(last().sent).toEqual([]);
  });
});
