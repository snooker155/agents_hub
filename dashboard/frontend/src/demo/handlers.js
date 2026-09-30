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
