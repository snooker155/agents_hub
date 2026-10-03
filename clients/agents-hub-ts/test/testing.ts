/**
 * A tiny fetch double shared by the test files: records every call it was
 * given and answers from a queue of canned responses, so a test can assert
 * on the request the SDK built (method, URL, headers, body) without a real
 * hub listening anywhere.
 */
export interface RecordedCall {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: string | undefined;
}

export class FakeFetch {
  calls: RecordedCall[] = [];
  private responses: Response[] = [];

  /** Queue one answer, consumed in order by the next call. */
  queue(response: Response): this {
    this.responses.push(response);
    return this;
  }

  fetch: typeof fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    const headers: Record<string, string> = {};
    if (init?.headers) {
      for (const [key, value] of new Headers(init.headers as HeadersInit).entries()) {
        headers[key] = value;
      }
    }
    this.calls.push({
      method: init?.method ?? "GET",
      url,
      headers,
      body: typeof init?.body === "string" ? init.body : undefined,
    });
    const next = this.responses.shift();
    if (!next) throw new Error(`FakeFetch: no queued response for ${init?.method ?? "GET"} ${url}`);
    return next;
  };
}

export function jsonResponse(body: unknown, init: { status?: number } = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "content-type": "application/json" },
  });
}

/** An `event-stream` response from a list of already-framed `data: ...\n\n` strings. */
export function sseResponse(events: string[], init: { status?: number } = {}): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) controller.enqueue(encoder.encode(`data: ${event}\n\n`));
      controller.close();
    },
  });
  return new Response(stream, {
    status: init.status ?? 200,
    headers: { "content-type": "text/event-stream" },
  });
}
