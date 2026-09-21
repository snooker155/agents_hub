import assert from "node:assert/strict";
import { test } from "node:test";

import { HubClient } from "../src/client.js";
import { BrokenTransport, RecordingTransport } from "../src/testing.js";

// The transport runs inside somebody else's service. What it must never do
// matters more than what it does.

test("frames wait for the run id the hub answers with", async () => {
  const transport = new RecordingTransport();
  const client = new HubClient(transport);

  client.open({ input: "go" });
  client.emit({ type: "token", token: "a" });
  await client.flush();

  assert.deepEqual(transport.paths(), ["/api/ingest/runs", "/api/ingest/runs/ing-1/events"]);
});

test("a hub that is down is reported, not thrown", async () => {
  const seen = [];
  const client = new HubClient(new BrokenTransport(), { onError: (e) => seen.push(e) });

  client.open({ input: "go" });
  client.emit({ type: "token", token: "a" });
  await client.flush();

  assert.ok(seen.length > 0, "failures were not reported to onError");
  assert.equal(client.errors > 0, true);
});

test("frames for a run that never opened are dropped, not hoarded", async () => {
  const client = new HubClient(new BrokenTransport(), { flushMs: 5 });

  client.open({ input: "go" });
  for (let i = 0; i < 50; i += 1) client.emit({ type: "token", token: String(i) });
  await client.flush();

  assert.equal(client.runId, null, "the open failed, as this test intends");
  assert.ok(client.dropped > 0, "nothing was dropped, so the buffer just grew");
});

test("the buffer is bounded when nothing is draining it", () => {
  const client = new HubClient(new BrokenTransport(), { flushMs: 60000, queueSize: 10, maxBatch: 1000 });

  for (let i = 0; i < 200; i += 1) client.emit({ type: "token", token: String(i) });

  assert.ok(client._pending.length <= 10);
  assert.ok(client.dropped > 0);
});

test("a big run is sent in batches rather than one enormous request", async () => {
  const transport = new RecordingTransport();
  const client = new HubClient(transport, { maxBatch: 10 });

  client.open({ input: "go" });
  for (let i = 0; i < 25; i += 1) client.emit({ type: "token", token: String(i) });
  await client.flush();

  const batches = transport.calls.filter((c) => c.path.endsWith("/events"));
  assert.ok(batches.length >= 3, `expected several batches, got ${batches.length}`);
  assert.equal(transport.frames().length, 25);
});
