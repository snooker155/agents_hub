import assert from "node:assert/strict";
import { test } from "node:test";

import { AgentsHub } from "../src/client.ts";
import { FakeFetch, jsonResponse, sseResponse } from "./testing.ts";

test("agents.list() calls GET /api/agents with the workspace query param", async () => {
  const fake = new FakeFetch().queue(jsonResponse([{ id: "support-bot", name: "Support bot" }]));
  const hub = new AgentsHub({ baseUrl: "http://hub.local", apiKey: "ahk_x", fetch: fake.fetch });

  const agents = await hub.agents.list({ workspace: "shop" });

  assert.equal(fake.calls[0].method, "GET");
  assert.equal(new URL(fake.calls[0].url).pathname, "/api/agents");
  assert.equal(new URL(fake.calls[0].url).searchParams.get("workspace"), "shop");
  assert.deepEqual(agents, [{ id: "support-bot", name: "Support bot" }]);
});

test("agents.get(id) substitutes the id into the path", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ id: "support-bot", name: "Support bot" }));
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const agent = await hub.agents.get("support-bot");

  assert.equal(new URL(fake.calls[0].url).pathname, "/api/agents/support-bot");
  assert.equal(agent.id, "support-bot");
});

test("tasks.create() posts the task and tasks.get() reads it back", async () => {
  const fake = new FakeFetch()
    .queue(jsonResponse({ id: "t1" }))
    .queue(jsonResponse({ id: "t1", title: "Ship it", status: "open" }));
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await hub.tasks.create({ title: "Ship it" });
  const task = await hub.tasks.get("t1");

  assert.equal(fake.calls[0].method, "POST");
  assert.equal(new URL(fake.calls[0].url).pathname, "/api/tasks");
  assert.deepEqual(JSON.parse(fake.calls[0].body ?? "{}"), { title: "Ship it" });
  assert.equal(new URL(fake.calls[1].url).pathname, "/api/tasks/t1");
  assert.equal(task.title, "Ship it");
});

test("chat.send() without stream posts to /api/chat/message and returns the answer", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ ok: true, response: "Order 42 shipped yesterday." }));
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const result = await hub.chat.send({ agent_id: "support-bot", message: "Where is order 42?" });

  assert.equal(new URL(fake.calls[0].url).pathname, "/api/chat/message");
  assert.equal(result.response, "Order 42 shipped yesterday.");
});

test("chat.send() with stream iterates the pipeline's SSE events in order", async () => {
  const fake = new FakeFetch().queue(
    sseResponse([
      JSON.stringify({ type: "meta", run_id: "r1", session_id: "s1" }),
      JSON.stringify({ type: "token", token: "Order " }),
      JSON.stringify({ type: "token", token: "42 shipped." }),
      JSON.stringify({ type: "done", ok: true, response: "Order 42 shipped." }),
    ]),
  );
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const events = [];
  for await (const event of hub.chat.send(
    { agent_id: "support-bot", message: "Where is order 42?" },
    { stream: true },
  )) {
    events.push(event);
  }

  assert.equal(new URL(fake.calls[0].url).pathname, "/api/chat/stream");
  assert.equal(fake.calls[0].headers.accept, "text/event-stream");
  assert.deepEqual(
    events.map((e) => e.type),
    ["meta", "token", "token", "done"],
  );
  assert.equal(events.at(-1)?.response, "Order 42 shipped.");
});

test("request() reaches a route with no dedicated method, params and all", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ accents: ["navy", "blue"] }));
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const options = await hub.request("get", "/api/widgets/options", {});

  assert.equal(new URL(fake.calls[0].url).pathname, "/api/widgets/options");
  assert.deepEqual(options, { accents: ["navy", "blue"] });
});
