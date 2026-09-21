import assert from "node:assert/strict";
import { test } from "node:test";

import { resetWarning } from "../src/client.js";
import { HubTracer } from "../src/tracer.js";
import { BrokenTransport, RecordingTransport, loadLangGraph } from "../src/testing.js";

// Driven by a real LangGraph.js graph wherever possible: the shapes these
// callbacks arrive in are the whole difficulty, and a hand-written stand-in
// would only test my memory of them.

const lg = await loadLangGraph();
const needsLangGraph = { skip: lg ? false : "@langchain/langgraph is not installed" };

function approvalGraph() {
  const { Annotation, StateGraph, START, END, MemorySaver, interrupt } = lg;
  const State = Annotation.Root({
    steps: Annotation({ reducer: (a, b) => (a ?? []).concat(b ?? []), default: () => [] }),
  });
  return new StateGraph(State)
    .addNode("plan", () => ({ steps: ["plan"] }))
    .addNode("approve", () => {
      const decision = interrupt({ question: "Ship the release?", choices: ["ship", "hold"] });
      return { steps: [`approve:${decision}`] };
    })
    .addNode("deploy", () => ({ steps: ["deploy"] }))
    .addEdge(START, "plan")
    .addEdge("plan", "approve")
    .addEdge("approve", "deploy")
    .addEdge("deploy", END)
    .compile({ checkpointer: new MemorySaver() });
}

test("a real run is reported as a run with the path it took", needsLangGraph, async () => {
  const { Annotation, StateGraph, START, END } = lg;
  const State = Annotation.Root({
    steps: Annotation({ reducer: (a, b) => (a ?? []).concat(b ?? []), default: () => [] }),
  });
  const graph = new StateGraph(State)
    .addNode("plan", () => ({ steps: ["plan"] }))
    .addNode("finish", () => ({ steps: ["finish"] }))
    .addEdge(START, "plan").addEdge("plan", "finish").addEdge("finish", END)
    .compile();

  const transport = new RecordingTransport();
  const tracer = new HubTracer({ transport, graph });
  await graph.invoke({ steps: [] }, { callbacks: [tracer] });
  await tracer.flush();

  const nodes = transport.frames().filter((f) => f.type === "node_start").map((f) => f.node);
  // __start__ arrives as a node here and is not work: skipping it keeps the
  // path the same one the Python client reports for the same graph.
  assert.deepEqual(nodes, ["plan", "finish"]);
  assert.ok(transport.paths().some((p) => p.endsWith("/close")));
});

test("a graph that stops to ask parks its run with the question", needsLangGraph, async () => {
  const graph = approvalGraph();
  const transport = new RecordingTransport();
  const tracer = new HubTracer({ transport, graph });

  const result = await graph.invoke({ steps: [] }, {
    callbacks: [tracer], configurable: { thread_id: "t1" },
  });
  await tracer.flush();

  assert.ok(result.__interrupt__, "the graph under test did not actually pause");
  const parked = transport.calls.find((c) => c.path.endsWith("/interrupt"));
  assert.ok(parked, "the run was not parked");
  assert.equal(parked.body.question, "Ship the release?");
  assert.deepEqual(parked.body.choices, ["ship", "hold"]);
  assert.equal(parked.body.node, "approve");
  // A pause is not a finish: the run must not also have been closed.
  assert.equal(transport.paths().some((p) => p.endsWith("/close")), false);
});

test("the node it stopped in is reported as interrupted, not as failed", needsLangGraph, async () => {
  const graph = approvalGraph();
  const transport = new RecordingTransport();
  const tracer = new HubTracer({ transport, graph });

  await graph.invoke({ steps: [] }, { callbacks: [tracer], configurable: { thread_id: "t2" } });
  await tracer.flush();

  const ends = transport.frames().filter((f) => f.type === "node_end");
  const approve = ends.find((f) => f.node === "approve");
  assert.equal(approve.status, "interrupted");
  assert.equal(approve.ok, true, "a pause reported as a failure is the wrong story");
});

test("the answer is collected and the next run says what it continues", needsLangGraph, async () => {
  const { Command } = lg;
  const graph = approvalGraph();
  const transport = new RecordingTransport();
  const tracer = new HubTracer({ transport, graph });
  const cfg = { callbacks: [tracer], configurable: { thread_id: "t3" } };

  await graph.invoke({ steps: [] }, cfg);
  await tracer.flush();
  transport.answer = { status: "answered", value: "ship" };

  const answer = await tracer.waitForAnswer({ timeoutMs: 1000, pollMs: 10 });
  assert.equal(answer, "ship");

  const final = await graph.invoke(new Command({ resume: answer }), cfg);
  await tracer.flush();

  assert.deepEqual(final.steps, ["plan", "approve:ship", "deploy"]);
  const opens = transport.calls.filter((c) => c.path === "/api/ingest/runs");
  assert.equal(opens.at(-1).body.resumed_from, "ing-1",
    "the resumed run did not say which run it continues");
});

test("a hub that is down does not stop the graph", needsLangGraph, async () => {
  const graph = approvalGraph();
  const seen = [];
  const tracer = new HubTracer({ transport: new BrokenTransport(), onError: (e) => seen.push(e) });

  const result = await graph.invoke({ steps: [] }, {
    callbacks: [tracer], configurable: { thread_id: "t4" },
  });
  await tracer.flush();

  assert.ok(result.__interrupt__, "the graph's own result was affected by the tracer");
  assert.ok(seen.length > 0, "failures were not reported to onError");
});

test("the reported topology is the graph's own shape", needsLangGraph, async () => {
  const graph = approvalGraph();
  const transport = new RecordingTransport();
  const tracer = new HubTracer({ transport, graph });

  await tracer.reportGraph();
  await tracer.flush();

  const sent = transport.calls.find((c) => c.path === "/api/ingest/topology").body;
  assert.deepEqual(sent.nodes.map((n) => n.id), ["__start__", "plan", "approve", "deploy", "__end__"]);
  assert.equal(sent.nodes[0].kind, "terminal");
  assert.equal(sent.framework, "langgraph");
});

// ── the parts that need no graph ────────────────────────────────────────────

test("no callback may throw into the graph, whatever it is handed", () => {
  const tracer = new HubTracer({ transport: new RecordingTransport() });

  tracer.handleChainStart(undefined, undefined, "r", undefined, undefined, undefined, undefined, undefined);
  tracer.handleLLMNewToken(undefined);
  tracer.handleLLMEnd({});
  tracer.handleToolStart(undefined, undefined, "t");
  tracer.handleToolEnd(undefined, "t");
  tracer.handleToolError(new Error("x"), "t");
  tracer.handleChainEnd(undefined, "r");
  // Reaching here without an exception is the assertion.
});

test("sharing one tracer across concurrent runs is refused", () => {
  const seen = [];
  const tracer = new HubTracer({ transport: new RecordingTransport(), onError: (e) => seen.push(e) });

  tracer.handleChainStart({}, {}, "first", undefined, [], {}, undefined, "LangGraph");
  tracer.handleChainStart({}, {}, "second", undefined, [], {}, undefined, "LangGraph");

  assert.match(String(seen[0]?.message), /already following a run/);
});

test("token usage is forwarded in whichever spelling it arrives", async () => {
  const transport = new RecordingTransport();
  const tracer = new HubTracer({ transport });
  tracer.handleChainStart({}, {}, "root", undefined, [], {}, undefined, "LangGraph");

  tracer.handleLLMEnd({
    generations: [[{ message: { usage_metadata: { input_tokens: 120, output_tokens: 30 } } }]],
  });
  tracer.handleLLMEnd({ llmOutput: { tokenUsage: { promptTokens: 5, completionTokens: 1 } } });
  await tracer.flush();

  const usage = transport.frames().filter((f) => f.type === "usage");
  assert.equal(usage[0].input_tokens, 120);
  assert.equal(usage[1].input_tokens, 5, "the older spelling was dropped");
});

test("swallowed failures are counted where anyone can read them", async () => {
  // Silence is the rule; a number is how you find out anyway.
  const tracer = new HubTracer({ transport: new BrokenTransport() });
  assert.equal(tracer.errors, 0);

  tracer.handleChainStart({ name: "LangGraph" }, {}, "run-1", undefined, undefined, {});
  await tracer.flush();

  assert.ok(tracer.errors > 0, "nothing said so, and nothing counted it either");
});

test("the first failure says so once, and only with nobody listening", async () => {
  // A mistyped token must not look exactly like a working setup.
  resetWarning();
  const warnings = [];
  const original = console.warn;
  console.warn = (message) => warnings.push(String(message));
  try {
    for (const _ of [1, 2]) {
      const tracer = new HubTracer({ transport: new BrokenTransport() });
      tracer.handleChainStart({ name: "LangGraph" }, {}, "run-1", undefined, undefined, {});
      await tracer.flush();
    }
    // Once per process, not per tracer: the recommended usage is one tracer per
    // invocation, so per-tracer would mean a line per run while a hub is down.
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /onError/);

    const seen = [];
    resetWarning();
    warnings.length = 0;
    const told = new HubTracer({ transport: new BrokenTransport(), onError: (e) => seen.push(e) });
    told.handleChainStart({ name: "LangGraph" }, {}, "run-2", undefined, undefined, {});
    await told.flush();
    assert.ok(seen.length, "the failures went nowhere at all");
    // Passing onError is choosing the channel, which the warning must respect.
    assert.deepEqual(warnings, []);
  } finally {
    console.warn = original;
  }
});
