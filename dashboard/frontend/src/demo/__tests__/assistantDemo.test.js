import { describe, expect, it } from 'vitest';
import { createHandlers } from '../handlers';
import { ASSISTANT_DEMO_FRAMES, ASSISTANT_DEMO_THREAD } from '../assistantDemo';
import { framesForRun, replayStream } from '../resolver';
import { stateForTool } from '../../components/liveMark/activity';
import { isScene } from '../../components/liveMark/scenes';

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
    const { response } = await run(request);
    expect(response.headers.get('Content-Type')).toContain('text/event-stream');
    await response.body.cancel();
    // the same frames, replayed without the real pauses
    const frames = framesForRun({ 'demo-assistant': ASSISTANT_DEMO_FRAMES }, 'demo-assistant');
    const text = await new Response(replayStream(frames, { wait: async () => {} })).text();
    expect(text.indexOf('"type":"run"')).toBeLessThan(text.indexOf('"type":"tool_start"'));
    expect(text.indexOf('"type":"tool_end"')).toBeLessThan(text.indexOf('"type":"token"'));
    expect(text).toContain('"type":"done"');
    expect(text).not.toContain('demo_pause');
  });

  it('takes steps the live mark plays a scene for, each as long as a real one', async () => {
    const starts = ASSISTANT_DEMO_FRAMES.filter((f) => f.event === 'tool_start');
    expect(starts.length).toBeGreaterThan(1);
    for (const { data } of starts) expect(isScene(stateForTool(data.tool, data.input))).toBe(true);
    const waits = [];
    const frames = framesForRun({ 'demo-assistant': ASSISTANT_DEMO_FRAMES }, 'demo-assistant');
    await new Response(replayStream(frames, { wait: async (ms) => { waits.push(ms); } })).text();
    expect(waits.filter((ms) => ms >= 2000)).toHaveLength(starts.length);
  });

  it('answers the voice routes with model_not_added, so the browser speaks and listens', async () => {
    const request = new Request('http://demo.local/api/assistant/speak', { method: 'POST', body: '{}' });
    const { response } = await run(request);
    expect(response.status).toBe(409);
    expect((await response.json()).detail.code).toBe('model_not_added');
  });
});
