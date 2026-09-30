import { describe, it, expect } from 'vitest';
import {
  fixtureKeys, resolveGet, resolveWrite, framesForRun, replayStream,
  isStreamRequest, pickRunId, normalizeFrame,
} from '../resolver';
import { createHandlers } from '../handlers';

const FIXTURES = {
  workspace: 'demo',
  responses: {
    'GET /api/agents?workspace=demo': [{ id: 'researcher' }],
    'GET /api/agents': [{ id: 'everyone' }],
    'GET /api/tasks?limit=5&workspace=demo': [{ id: 't1' }],
    'GET /api/auth/mode': { mode: 'multi' },
  },
};

describe('demo GET lookup', () => {
  it('sorts the query so parameter order does not matter', () => {
    expect(fixtureKeys('get', '/api/tasks?workspace=demo&limit=5')).toEqual([
      'GET /api/tasks?limit=5&workspace=demo',
      'GET /api/tasks',
    ]);
  });

  it('tries the exact key with its query first', () => {
    const r = resolveGet(FIXTURES, '/api/agents?workspace=demo');
    expect(r.source).toBe('GET /api/agents?workspace=demo');
    expect(r.body).toEqual([{ id: 'researcher' }]);
    expect(resolveGet(FIXTURES, '/api/tasks?workspace=demo&limit=5').body).toEqual([{ id: 't1' }]);
  });

  it('falls back to the bare path when the query has no recording', () => {
    const r = resolveGet(FIXTURES, '/api/agents?workspace=other');
    expect(r.source).toBe('GET /api/agents');
    expect(r.body).toEqual([{ id: 'everyone' }]);
  });

  it('then answers a shaped empty value per resource', () => {
    expect(resolveGet(FIXTURES, '/api/views?workspace=demo')).toEqual({ source: 'fallback', body: { views: [], total: 0 } });
    expect(resolveGet(FIXTURES, '/api/sessions').body).toEqual({ items: [], total: 0 });
    expect(resolveGet(FIXTURES, '/api/teams').body).toEqual({ teams: [] });
    expect(resolveGet(FIXTURES, '/api/flows').body).toEqual([]);
    expect(resolveGet(FIXTURES, '/api/runs?workspace=demo').body).toEqual([]);
    expect(resolveGet(FIXTURES, '/api/teams/t1/runs').body).toEqual({ runs: [] });
    expect(resolveGet({}, '/api/health').body.status).toBe('ok');
  });

  it('always answers the auth probe as single user', () => {
    expect(resolveGet(FIXTURES, '/api/auth/mode').body.mode).toBe('single');
  });

  it('ignores the token parameter when matching', () => {
    expect(resolveGet(FIXTURES, '/api/agents?token=abc&workspace=demo').body).toEqual([{ id: 'researcher' }]);
  });
});

describe('demo writes', () => {
  it('echoes the body with an id', () => {
    const r = resolveWrite('POST', { title: 'New task' });
    expect(r.title).toBe('New task');
    expect(typeof r.id).toBe('string');
    expect(r.id.length).toBeGreaterThan(0);
    expect(resolveWrite('PUT', { id: 'keep' }).id).toBe('keep');
    expect(resolveWrite('DELETE', null).id).toMatch(/^demo-delete-/);
  });

  it('answers a write through the MSW handler with the echo', async () => {
    const [, generic] = createHandlers({ fixtures: FIXTURES, streams: {} });
    const request = new Request('http://demo.local/api/tasks', {
      method: 'POST', body: JSON.stringify({ title: 'x' }), headers: { 'Content-Type': 'application/json' },
    });
    const result = await generic.run({ request, requestId: '1' });
    const body = await result.response.json();
    expect(body.title).toBe('x');
    expect(body.id).toBeTruthy();
  });
});

describe('demo streams', () => {
  const STREAMS = {
    run_a: [
      { event: 'token', data: { text: 'Hello ' } },
      { event: 'tool_start', data: { tool: 'read_file', input: 'a.txt' } },
      { event: 'tool_end', data: { output: 'ok' } },
      { event: 'token', data: { token: 'world' } },
      { event: 'done', data: {} },
    ],
    run_b: [{ event: 'token', data: { token: 'b', agent_id: 'coder' } }],
  };

  it('knows which requests stream', () => {
    expect(isStreamRequest('POST', '/api/chat/stream')).toBe(true);
    expect(isStreamRequest('POST', '/api/views/v1/chat')).toBe(true);
    expect(isStreamRequest('GET', '/api/views/v1/chat')).toBe(false);
    expect(isStreamRequest('POST', '/api/chat/stream-sse')).toBe(false);
    expect(isStreamRequest('POST', '/api/stream/c1/channels')).toBe(false);
  });

  it('normalises the recorded shape to the flat one the UI reads', () => {
    expect(normalizeFrame({ event: 'token', data: { text: 'x' } }, 'r')).toEqual({ type: 'token', text: 'x', token: 'x\n', run_id: 'r' });
    expect(normalizeFrame({ event: 'tool_start', data: {} }, 'r').tool).toBe('recorded_step');
    expect(normalizeFrame({ type: 'done', ok: false }, 'r')).toEqual({ type: 'done', ok: false, run_id: 'r' });
  });

  it('picks the run by id, then by agent, then the first one', () => {
    expect(pickRunId(STREAMS, { body: { run_id: 'run_b' } })).toBe('run_b');
    expect(pickRunId(STREAMS, { body: { agent_id: 'coder' } })).toBe('run_b');
    expect(pickRunId(STREAMS, { pathname: '/api/runs/run_a/stream' })).toBe('run_a');
    expect(pickRunId(STREAMS, {})).toBe('run_a');
  });

  it('replays the recorded frames in order', async () => {
    const frames = framesForRun(STREAMS, 'run_a');
    const stream = replayStream(frames, { delay: () => 0, wait: () => Promise.resolve() });
    const text = await new Response(stream).text();
    const events = text.split('\n\n').filter(Boolean).map((chunk) => JSON.parse(chunk.slice(6)));
    expect(events.map((e) => e.type)).toEqual(['meta', 'token', 'tool_start', 'tool_end', 'token', 'done']);
    expect(events[1].token).toBe('Hello ');
    const done = events.at(-1);
    expect(done.ok).toBe(true);
    expect(done.response).toBe('Hello world');
    expect(events.every((e) => e.run_id === 'run_a')).toBe(true);
  });

  it('closes a recording that has no done frame, and prefers a chat run for a chat turn', () => {
    const streams = {
      demo_task_run: [{ event: 'token', data: { text: 'working' } }],
      demo_chat_run: [{ event: 'tool_start', data: {} }, { event: 'token', data: { text: 'answer' } }, { event: 'tool_end', data: {} }],
    };
    expect(pickRunId(streams, { pathname: '/api/chat/stream', body: { agent_id: 'x' } })).toBe('demo_chat_run');
    const frames = framesForRun(streams, 'demo_chat_run');
    expect(frames.map((f) => f.type)).toEqual(['meta', 'tool_start', 'token', 'tool_end', 'done']);
    expect(frames.at(-1).response).toBe('answer');
  });

  it('gives an unrecorded run the two frame default', () => {
    const frames = framesForRun(STREAMS, 'nope');
    expect(frames.map((f) => f.type)).toEqual(['meta', 'token', 'done']);
    expect(frames[1].token).toBe('This is a recorded demo.');
  });

  it('spaces frames 60 to 150 ms apart by default', async () => {
    const waits = [];
    const stream = replayStream(framesForRun({}, null), { wait: (ms) => { waits.push(ms); return Promise.resolve(); } });
    await new Response(stream).text();
    expect(waits.length).toBe(3);
    for (const ms of waits) {
      expect(ms).toBeGreaterThanOrEqual(60);
      expect(ms).toBeLessThanOrEqual(150);
    }
  });
});
