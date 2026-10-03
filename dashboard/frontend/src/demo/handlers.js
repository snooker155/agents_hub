/**
 * MSW request handlers for the demo build (VITE_DEMO=1): every `/api/*`
 * request the dashboard makes is answered from the recorded fixtures, so the
 * real UI runs with no backend behind it. The resolution rules live in
 * ./resolver.js; this file only adapts them to MSW.
 */
import { http, HttpResponse } from 'msw';
import {
  framesForRun, isStreamRequest, pickRunId, replayStream, resolveGet,
  resolveWrite, sharedStreamFrames,
} from './resolver';

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache',
  Connection: 'keep-alive',
};

/** The Help panel's one demo answer: the shape of a real one, links included. */
export const HELP_DEMO_TEXT = [
  'This is the public demo, so the Support agent answers with this one fixed reply.',
  'In a real install it reads what you have set up and suggests what to do next, for example:',
  '1. Add a model provider on [Models](/models).',
  '2. Talk to an agent in [Chat](/chat).',
  '3. Browse ready made agents in the [Marketplace](/marketplace).',
  'New here? [Take the tour](#tour).',
].join('\n');

const HELP_DEMO_FRAMES = [
  { event: 'token', data: { token: HELP_DEMO_TEXT } },
  { event: 'done', data: { ok: true, response: HELP_DEMO_TEXT } },
];

async function readBody(request) {
  try {
    return await request.clone().json();
  } catch {
    return null;
  }
}

/**
 * Build the handler list over one fixture set. `fixtures` is the parsed
 * fixtures.json, `streams` the parsed streams.json; either may be empty.
 */
export function createHandlers({ fixtures = {}, streams = {} } = {}) {
  return [
    // The one shared EventSource every page listens on. It announces a
    // client id (StreamContext waits for `ready`) and then stays open.
    http.get('*/api/stream', () => new HttpResponse(
      replayStream(sharedStreamFrames(), { delay: () => 0, keepOpen: true }),
      { headers: SSE_HEADERS },
    )),

    http.all('*/api/*', async ({ request }) => {
      const url = new URL(request.url);
      const method = request.method.toUpperCase();

      // Help (header button, docs/help.md): there is no model behind the demo,
      // so a question gets one fixed answer that still shows what the panel
      // does: next steps as links that open pages, and the tour.
      if (/\/api\/help-chat$/.test(url.pathname)) {
        if (method === 'GET') {
          return HttpResponse.json({ messages: [], trace: [], chat_ref: null, agent_id: 'support' });
        }
        if (method === 'POST') {
          return new HttpResponse(
            replayStream(framesForRun({ 'demo-help': HELP_DEMO_FRAMES }, 'demo-help')),
            { headers: SSE_HEADERS },
          );
        }
      }

      if (isStreamRequest(method, url.pathname)) {
        const body = method === 'GET' ? null : await readBody(request);
        const runId = pickRunId(streams, { pathname: url.pathname, search: url.search, body });
        return new HttpResponse(replayStream(framesForRun(streams, runId)), { headers: SSE_HEADERS });
      }

      if (method === 'GET' || method === 'HEAD') {
        return HttpResponse.json(resolveGet(fixtures, url).body);
      }

      // A write: recorded when the fixtures happen to hold one, else echoed.
      const recorded = fixtures?.responses?.[`${method} ${url.pathname}`];
      if (recorded !== undefined) return HttpResponse.json(recorded);
      return HttpResponse.json(resolveWrite(method, await readBody(request)));
    }),
  ];
}
