require("./setup");
const test = require("node:test");
const assert = require("node:assert/strict");

const Plugin = require("../main.js");
const h = Plugin.helpers;

test("module.exports is the plugin class and carries the helpers", () => {
  assert.equal(typeof Plugin, "function");
  assert.equal(typeof h, "object");
});

test("normalizeBaseUrl strips trailing slashes and a trailing /v1", () => {
  assert.equal(h.normalizeBaseUrl("http://localhost:8000/"), "http://localhost:8000");
  assert.equal(h.normalizeBaseUrl("  https://hub.example.com///  "), "https://hub.example.com");
  assert.equal(h.normalizeBaseUrl("https://hub.example.com/v1"), "https://hub.example.com");
  assert.equal(h.normalizeBaseUrl("https://hub.example.com/v1/"), "https://hub.example.com");
});

test("normalizeBaseUrl adds http:// when no scheme is given and handles empty", () => {
  assert.equal(h.normalizeBaseUrl("localhost:8000"), "http://localhost:8000");
  assert.equal(h.normalizeBaseUrl(""), "");
  assert.equal(h.normalizeBaseUrl(undefined), "");
});

test("buildHeaders carries the key and optional workspace", () => {
  const a = h.buildHeaders({ apiKey: " k1 ", workspace: "" });
  assert.equal(a.Authorization, "Bearer k1");
  assert.equal(a["Content-Type"], "application/json");
  assert.equal("X-Agents-Hub-Workspace" in a, false);
  const b = h.buildHeaders({ apiKey: "k2", workspace: " team " });
  assert.equal(b["X-Agents-Hub-Workspace"], "team");
  // A hub in single mode needs no key, and an empty Bearer is never sent.
  const c = h.buildHeaders({ apiKey: "  ", workspace: "" });
  assert.equal("Authorization" in c, false);
});

test("parseAgents keeps only hub agents and reads their fields", () => {
  const body = {
    object: "list",
    data: [
      { id: "agent:researcher", owned_by: "agents-hub", agent_id: "researcher", name: "Researcher", description: "Finds things" },
      { id: "gpt-5.4-mini", owned_by: "openai" },
      { id: "agent:writer", owned_by: "agents-hub" },
    ],
  };
  const agents = h.parseAgents(body);
  assert.deepEqual(agents.map((a) => a.id), ["researcher", "writer"]);
  assert.equal(agents[0].name, "Researcher");
  assert.equal(agents[0].description, "Finds things");
  assert.equal(agents[1].name, "writer");
});

test("parseAgents survives junk and empty lists", () => {
  assert.deepEqual(h.parseAgents(null), []);
  assert.deepEqual(h.parseAgents({}), []);
  assert.deepEqual(h.parseAgents({ data: [] }), []);
  assert.deepEqual(h.parseAgents({ data: [null, 3, { owned_by: "agents-hub" }] }), []);
});

test("trimHistory keeps the last N and starts on a user message", () => {
  const msgs = [];
  for (let i = 0; i < 30; i++) msgs.push({ role: i % 2 ? "assistant" : "user", content: String(i) });
  const t = h.trimHistory(msgs, 20);
  assert.equal(t.length, 20);
  assert.equal(t[0].role, "user");
  assert.equal(t[t.length - 1].content, "29");
  const odd = h.trimHistory(msgs, 21);
  assert.equal(odd[0].role, "user");
  assert.ok(odd.length <= 21);
  assert.equal(h.trimHistory(msgs.slice(0, 3), 20).length, 3);
});

test("buildContextMessage formats and truncates", () => {
  const m = h.buildContextMessage("My note", "folder/My note.md", "hello");
  assert.equal(m.role, "system");
  assert.equal(m.content, 'Context from the Obsidian note "My note" (path folder/My note.md):\n\nhello');
  const big = h.buildContextMessage("T", "T.md", "x".repeat(70000));
  assert.ok(big.content.includes("(truncated)"));
  assert.ok(big.content.length < 60000 + 300);
});

test("buildRequestBody puts context first, then trimmed history, user last", () => {
  const ctx = h.buildContextMessage("T", "T.md", "body");
  const history = [
    { role: "user", content: "q1" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "q2" },
  ];
  const body = h.buildRequestBody("researcher", history, [ctx], 20);
  assert.equal(body.model, "agent:researcher");
  assert.equal(body.messages[0].role, "system");
  assert.equal(body.messages[body.messages.length - 1].content, "q2");
  assert.equal(body.messages.length, 4);
});

test("extractError reads both hub error shapes and falls back", () => {
  assert.equal(h.extractError(400, { error: { message: "bad model" } }, ""), "bad model");
  assert.equal(h.extractError(401, { detail: "Invalid token" }, ""), "Invalid token");
  assert.equal(h.extractError(422, { detail: [{ msg: "field required" }] }, ""), "field required");
  assert.match(h.extractError(502, null, "upstream down"), /502.*upstream down/);
  assert.match(h.extractError(500, null, ""), /500/);
});

test("extractAnswer and extractRunId", () => {
  const json = { choices: [{ message: { role: "assistant", content: "hi" } }], agents_hub: { run_id: "r1" } };
  assert.equal(h.extractAnswer(json), "hi");
  assert.equal(h.extractRunId(json), "r1");
  assert.equal(h.extractAnswer({ choices: [] }), "");
  assert.equal(h.extractRunId({}), "");
});

test("noteNameFromQuestion makes a safe short file name", () => {
  assert.equal(h.noteNameFromQuestion("What is: the plan/for* Q3?"), "What is the plan for Q3");
  assert.equal(h.noteNameFromQuestion(""), "Agents Hub answer");
  const long = h.noteNameFromQuestion("one two three four five six seven eight nine ten eleven twelve");
  assert.equal(long.split(" ").length, 8);
});
