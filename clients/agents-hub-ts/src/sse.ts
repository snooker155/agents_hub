/**
 * The hub's two streaming transports (`POST /api/chat/stream`, docs/chat.md;
 * `POST /v1/chat/completions` with `stream: true`, docs/hub-as-provider.md)
 * both send plain `text/event-stream` bodies of `data: <json>\n\n` lines, no
 * custom `event:` lines and no `id:` lines to track (that is `/api/stream`'s
 * job, the multiplexed browser connection this SDK does not need). This is
 * just enough of the SSE line format to read those two back: a `data:` line
 * (or several, joined with `\n`, per the spec) ends at the blank line that
 * follows it, and a line starting with `:` is a comment, ignored.
 */
export async function* readSSELines(body: ReadableStream<Uint8Array> | null): AsyncGenerator<string> {
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) {
        buffer += decoder.decode();
        yield* drain(buffer, true);
        return;
      }
      const { events, rest } = split(buffer);
      for (const event of events) yield event;
      buffer = rest;
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Already closed or errored; nothing left to cancel.
    }
  }
}

function* drain(buffer: string, final: boolean): Generator<string> {
  const { events, rest } = split(buffer);
  yield* events;
  if (final && rest.trim()) {
    const event = linesToData(rest.split("\n"));
    if (event !== null) yield event;
  }
}

/** Split complete `\n\n`-terminated events off the front of the buffer. */
function split(buffer: string): { events: string[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  const events: string[] = [];
  for (const part of parts) {
    const event = linesToData(part.split("\n"));
    if (event !== null) events.push(event);
  }
  return { events, rest };
}

/** One event's lines to its `data:` payload (the fields this SDK reads back don't use any other field). */
function linesToData(lines: string[]): string | null {
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("data:")) {
      dataLines.push(line.length > 5 && line[5] === " " ? line.slice(6) : line.slice(5));
    }
  }
  return dataLines.length ? dataLines.join("\n") : null;
}

/** `readSSELines`, with each payload parsed as JSON and `[DONE]` turned into a clean stop. */
export async function* readSSEJson<T = unknown>(body: ReadableStream<Uint8Array> | null): AsyncGenerator<T> {
  for await (const line of readSSELines(body)) {
    if (line === "[DONE]") return;
    yield JSON.parse(line) as T;
  }
}
