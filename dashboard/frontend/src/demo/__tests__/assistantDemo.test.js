import { describe, expect, it } from 'vitest';
import { createHandlers } from '../handlers';
import { ASSISTANT_DEMO_THREAD } from '../assistantDemo';

/** The Assistant page in the demo: two recorded turns, a fixed reply, browser voice. */
describe('demo Assistant', () => {
  const generic = () => createHandlers({ fixtures: {}, streams: {} })[1];
  const run = (request) => generic().run({ request, requestId: String(Math.random()) });

  it('loads a thread with two turns and no speech models', async () => {
    const body = await (await run(new Request('http://demo.local/api/assistant'))).response.json();
    expect(body.messages.filter((m) => m.role === 'user')).toHaveLength(2);
    expect(body.voice).toMatchObject({ transcription: null, speech: null });
    expect(body.service_available).toBe(false);
    expect(ASSISTANT_DEMO_THREAD.trace.some((e) => e.k === 'tool')).toBe(true);
  });

  it('streams the fixed reply with its run id first', async () => {
    const request = new Request('http://demo.local/api/assistant', {
      method: 'POST', body: JSON.stringify({ message: 'hi' }), headers: { 'Content-Type': 'application/json' },
    });
    const text = await (await run(request)).response.text();
    expect(text.indexOf('"type":"run"')).toBeLessThan(text.indexOf('"type":"token"'));
    expect(text).toContain('"type":"done"');
  });

  it('answers the voice routes with model_not_added, so the browser speaks and listens', async () => {
    const request = new Request('http://demo.local/api/assistant/speak', { method: 'POST', body: '{}' });
    const { response } = await run(request);
    expect(response.status).toBe(409);
    expect((await response.json()).detail.code).toBe('model_not_added');
  });
});
