import assert from "node:assert/strict";
import { test } from "node:test";

import { AgentsHub } from "../src/client.ts";
import { AgentsHubError } from "../src/errors.ts";
import { FakeFetch, jsonResponse, sseResponse } from "./testing.ts";

test("v1.chat.completions.create() posts to /v1/chat/completions with the Bearer key", async () => {
  const fake = new FakeFetch().queue(
    jsonResponse({
      id: "chatcmpl-1",
      object: "chat.completion",
      created: 0,
      model: "anthropic/claude-sonnet-4-5",
      choices: [{ index: 0, message: { role: "assistant", content: "Hello!" }, finish_reason: "stop" }],
    }),
  );
  const hub = new AgentsHub({ baseUrl: "http://hub.local", apiKey: "ahk_x", fetch: fake.fetch });

  const completion = await hub.v1.chat.completions.create({
    model: "anthropic/claude-sonnet-4-5",
    messages: [{ role: "user", content: "Hello" }],
  });

  assert.equal(fake.calls[0].method, "POST");
  assert.equal(new URL(fake.calls[0].url).pathname, "/v1/chat/completions");
  assert.equal(fake.calls[0].headers.authorization, "Bearer ahk_x");
  assert.equal(completion.choices[0].message.content, "Hello!");
});

test("v1.chat.completions.create() sends X-Agents-Hub-Workspace for an agent model", async () => {
  const fake = new FakeFetch().queue(
    jsonResponse({
      id: "chatcmpl-2",
      object: "chat.completion",
      created: 0,
      model: "agent:support-bot",
      choices: [{ index: 0, message: { role: "assistant", content: "Order 42 shipped." }, finish_reason: "stop" }],
      agents_hub: { agent_id: "support-bot", workspace: "shop", run_id: "run-1" },
    }),
  );
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const completion = await hub.v1.chat.completions.create(
    { model: "agent:support-bot", messages: [{ role: "user", content: "Where is order 42?" }] },
    { workspace: "shop" },
  );

  assert.equal(fake.calls[0].headers["x-agents-hub-workspace"], "shop");
  assert.equal(completion.agents_hub?.run_id, "run-1");
});

test("v1.chat.completions.create() with stream yields chunks and stops at [DONE]", async () => {
  const fake = new FakeFetch().queue(
    sseResponse([
      JSON.stringify({
        id: "chatcmpl-3",
        object: "chat.completion.chunk",
        created: 0,
        model: "anthropic/claude-sonnet-4-5",
        choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }],
      }),
      JSON.stringify({
        id: "chatcmpl-3",
        object: "chat.completion.chunk",
        created: 0,
        model: "anthropic/claude-sonnet-4-5",
        choices: [{ index: 0, delta: { content: "Hi!" }, finish_reason: null }],
      }),
      JSON.stringify({
        id: "chatcmpl-3",
        object: "chat.completion.chunk",
        created: 0,
        model: "anthropic/claude-sonnet-4-5",
        choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
      }),
      "[DONE]",
    ]),
  );
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const chunks = [];
  for await (const chunk of hub.v1.chat.completions.create(
    { model: "anthropic/claude-sonnet-4-5", messages: [{ role: "user", content: "Hi" }], stream: true },
  )) {
    chunks.push(chunk);
  }

  assert.equal(chunks.length, 3);
  assert.equal(chunks[1].choices[0].delta.content, "Hi!");
  assert.equal(chunks.at(-1)?.choices[0].finish_reason, "stop");
});

test("v1.models.list() reads the catalog and agent models", async () => {
  const fake = new FakeFetch().queue(
    jsonResponse({
      object: "list",
      data: [
        { id: "openai/gpt-4o", object: "model", owned_by: "openai", created: 0 },
        { id: "agent:support-bot", object: "model", owned_by: "agents-hub", created: 0, agent_id: "support-bot" },
      ],
    }),
  );
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const models = await hub.v1.models.list();

  assert.equal(new URL(fake.calls[0].url).pathname, "/v1/models");
  assert.equal(models.data.length, 2);
  assert.equal(models.data[1].agent_id, "support-bot");
});

test("a 503 model_not_found error on /v1 maps to AgentsHubError", async () => {
  const fake = new FakeFetch().queue(
    jsonResponse(
      { error: { message: "Unknown model 'nope'", type: "invalid_request_error", param: "model", code: "model_not_found" } },
      { status: 404 },
    ),
  );
  const hub = new AgentsHub({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await assert.rejects(
    () => hub.v1.chat.completions.create({ model: "nope", messages: [{ role: "user", content: "hi" }] }),
    (error: unknown) => {
      assert.ok(error instanceof AgentsHubError);
      assert.equal(error.status, 404);
      assert.equal(error.code, "model_not_found");
      return true;
    },
  );
});
