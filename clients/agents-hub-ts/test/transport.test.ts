import assert from "node:assert/strict";
import { test } from "node:test";

import { Transport } from "../src/transport.ts";
import { AgentsHubError } from "../src/errors.ts";
import { FakeFetch, jsonResponse } from "./testing.ts";

test("attaches the Authorization: Bearer header when an apiKey is set", async () => {
  const fake = new FakeFetch().queue(jsonResponse([]));
  const transport = new Transport({ baseUrl: "http://hub.local", apiKey: "ahk_test", fetch: fake.fetch });

  await transport.request("get", "/api/agents", {});

  assert.equal(fake.calls[0].headers.authorization, "Bearer ahk_test");
});

test("sends no Authorization header without an apiKey (single mode)", async () => {
  const fake = new FakeFetch().queue(jsonResponse([]));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await transport.request("get", "/api/agents", {});

  assert.equal("authorization" in fake.calls[0].headers, false);
});

test("builds query params and skips undefined values", async () => {
  const fake = new FakeFetch().queue(jsonResponse([]));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await transport.request("get", "/api/agents", { query: { workspace: "shop", limit: undefined } });

  const url = new URL(fake.calls[0].url);
  assert.equal(url.searchParams.get("workspace"), "shop");
  assert.equal(url.searchParams.has("limit"), false);
});

test("substitutes {param} placeholders into the real URL, not the literal template", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ id: "support-bot" }));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await transport.request("get", "/api/agents/{agent_id}", { params: { agent_id: "support-bot" } });

  assert.equal(fake.calls[0].url, "http://hub.local/api/agents/support-bot");
});

test("JSON-encodes a request body and sets content-type", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ id: "t1" }));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await transport.request("post", "/api/tasks", { body: { title: "Ship it" } });

  assert.equal(fake.calls[0].headers["content-type"], "application/json");
  assert.deepEqual(JSON.parse(fake.calls[0].body ?? "{}"), { title: "Ship it" });
});

test("a plain {detail} error becomes an AgentsHubError with that message", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ detail: "Rate limit exceeded", retry_after: 30 }, { status: 429 }));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await assert.rejects(
    () => transport.request("get", "/api/agents", {}),
    (error: unknown) => {
      assert.ok(error instanceof AgentsHubError);
      assert.equal(error.status, 429);
      assert.equal(error.message, "Rate limit exceeded");
      return true;
    },
  );
});

test("a {detail, code} error carries the code (e.g. the widget's bad_key)", async () => {
  const fake = new FakeFetch().queue(jsonResponse({ detail: "Invalid widget key", code: "bad_key" }, { status: 403 }));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await assert.rejects(
    () => transport.request("get", "/api/agents", {}),
    (error: unknown) => {
      assert.ok(error instanceof AgentsHubError);
      assert.equal(error.code, "bad_key");
      return true;
    },
  );
});

test("an OpenAI-shaped /v1 error carries message, type and code", async () => {
  const fake = new FakeFetch().queue(
    jsonResponse(
      { error: { message: "Unknown model", type: "invalid_request_error", param: "model", code: "model_not_found" } },
      { status: 404 },
    ),
  );
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  await assert.rejects(
    () => transport.request("get", "/v1/models/{model_id}", { params: { model_id: "nope" } }),
    (error: unknown) => {
      assert.ok(error instanceof AgentsHubError);
      assert.equal(error.message, "Unknown model");
      assert.equal(error.type, "invalid_request_error");
      assert.equal(error.code, "model_not_found");
      return true;
    },
  );
});

test("an empty 204-style body resolves to undefined rather than a JSON parse error", async () => {
  const fake = new FakeFetch().queue(new Response(null, { status: 204 }));
  const transport = new Transport({ baseUrl: "http://hub.local", fetch: fake.fetch });

  const result = await transport.request("delete", "/api/agents/{agent_id}", { params: { agent_id: "x" } });
  assert.equal(result, undefined);
});
