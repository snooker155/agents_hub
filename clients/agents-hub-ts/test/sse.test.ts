import assert from "node:assert/strict";
import { test } from "node:test";

import { readSSELines, readSSEJson } from "../src/sse.ts";

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

test("reads one data: line per event", async () => {
  const stream = streamFromChunks(['data: {"a":1}\n\n', 'data: {"a":2}\n\n']);
  const lines: string[] = [];
  for await (const line of readSSELines(stream)) lines.push(line);
  assert.deepEqual(lines, ['{"a":1}', '{"a":2}']);
});

test("reassembles an event split across two network chunks", async () => {
  const stream = streamFromChunks(['data: {"a":1', '}\n\n']);
  const lines: string[] = [];
  for await (const line of readSSELines(stream)) lines.push(line);
  assert.deepEqual(lines, ['{"a":1}']);
});

test("ignores comment lines", async () => {
  const stream = streamFromChunks([': keep-alive\ndata: {"ok":true}\n\n']);
  const lines: string[] = [];
  for await (const line of readSSELines(stream)) lines.push(line);
  assert.deepEqual(lines, ['{"ok":true}']);
});

test("joins a multi-line data payload with \\n, per the SSE spec", async () => {
  const stream = streamFromChunks(["data: line one\ndata: line two\n\n"]);
  const lines: string[] = [];
  for await (const line of readSSELines(stream)) lines.push(line);
  assert.deepEqual(lines, ["line one\nline two"]);
});

test("a final event with no trailing blank line is still read", async () => {
  const stream = streamFromChunks(['data: {"a":1}\n\n', 'data: {"a":2}']);
  const lines: string[] = [];
  for await (const line of readSSELines(stream)) lines.push(line);
  assert.deepEqual(lines, ['{"a":1}', '{"a":2}']);
});

test("readSSEJson parses each payload and stops cleanly at [DONE]", async () => {
  const stream = streamFromChunks(['data: {"n":1}\n\n', 'data: {"n":2}\n\n', "data: [DONE]\n\n"]);
  const events: unknown[] = [];
  for await (const event of readSSEJson(stream)) events.push(event);
  assert.deepEqual(events, [{ n: 1 }, { n: 2 }]);
});

test("a null body yields nothing instead of throwing", async () => {
  const lines: string[] = [];
  for await (const line of readSSELines(null)) lines.push(line);
  assert.deepEqual(lines, []);
});
