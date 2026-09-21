import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  health,
  resetGraph,
  runOnce,
  resumeFrames,
  streamFrames,
  topology,
} from "../adapter.js";

// Driven by real LangGraph.js runs rather than by recorded events: the shapes
// these events arrive in are the whole difficulty, and a hand-written stand-in
// would only test my memory of them.

async function frames(source) {
  const out = [];
  for await (const line of source) out.push(JSON.parse(line));
  return out;
}

function serve(spec, extra = {}) {
  process.env.AGENTHUB_GRAPH = spec;
  delete process.env.AGENTHUB_INPUT_KEY;
  delete process.env.AGENTHUB_OUTPUT_KEY;
  Object.assign(process.env, extra);
  resetGraph();
}

const nodesOf = (list, type) => list.filter((f) => f.type === type).map((f) => f.node);

beforeEach(() => {
  delete process.env.AGENTHUB_GRAPH;
  resetGraph();
});

test("a streamed run reports the path it took, the tokens and the tools", async () => {
  serve("./demo_graph.js:graph");
  const out = await frames(streamFrames({ prompt: "what is the price of the team plan?", run_id: "r1" }));

  // __start__ arrives as a node on this runtime and is not work: skipping it
  // keeps the path the same one the Python adapter reports for the same graph.
  assert.deepEqual(nodesOf(out, "node_start"), ["triage", "pricing", "answer"]);
  assert.ok(out.some((f) => f.type === "token" && f.token));

  const toolStart = out.find((f) => f.type === "tool_start");
  assert.equal(toolStart.name, "lookup_pricing");
  // Unwrapped: the raw event double-wraps the arguments under another `input`.
  assert.equal(toolStart.input, JSON.stringify({ product: "team" }));
  assert.match(out.find((f) => f.type === "tool_end").output, /\$99\/mo/);

  const done = out.at(-1);
  assert.equal(done.type, "done");
  assert.equal(done.ok, true);
  assert.ok(done.output);
});

test("the branch a conditional edge took is named on the node that ended", async () => {
  serve("./demo_graph.js:graph");
  const priced = await frames(streamFrames({ prompt: "what does the team plan cost?", run_id: "r2" }));
  const plain = await frames(streamFrames({ prompt: "who are you?", run_id: "r3" }));

  const branchOf = (out) => out.find((f) => f.type === "node_end" && f.node === "triage").next;
  assert.equal(branchOf(priced), "pricing");
  assert.equal(branchOf(plain), "answer");

  // The last node has nothing following it, so its end names no branch.
  const last = plain.filter((f) => f.type === "node_end").at(-1);
  assert.equal(last.node, "answer");
  assert.equal(last.next, undefined);
  assert.equal(last.ok, true);
});

test("every node frame carries the depth the run was at", async () => {
  serve("./demo_graph.js:graph");
  const out = await frames(streamFrames({ prompt: "who are you?", run_id: "r4" }));
  for (const frame of out.filter((f) => f.type === "node_start")) assert.equal(frame.depth, 0);
});

test("the topology is the graph's own shape, labels and branches included", async () => {
  serve("./demo_graph.js:graph");
  const shape = await topology();

  assert.equal(shape.ok, true);
  assert.equal(shape.framework, "langgraph");
  // The label lives under data.name on this runtime, which is the only reason
  // this assertion is not about `id`.
  assert.deepEqual(
    shape.nodes.filter((n) => n.kind === "node").map((n) => n.label),
    ["triage", "pricing", "answer"],
  );
  assert.deepEqual(
    shape.nodes.filter((n) => n.kind === "terminal").map((n) => n.id).sort(),
    ["__end__", "__start__"],
  );
  const branch = shape.edges.find((e) => e.source === "triage" && e.target === "pricing");
  assert.equal(branch.conditional, true);
  assert.equal(shape.edges.find((e) => e.source === "pricing").conditional, false);
});

test("a graph that stops to ask parks the run with the question, and no outcome", async () => {
  serve("./approval_graph.js:graph", { AGENTHUB_INPUT_KEY: "request", AGENTHUB_OUTPUT_KEY: "steps" });
  const out = await frames(streamFrames({ prompt: "ship 4.2", run_id: "pause-1" }));

  const parked = out.at(-1);
  assert.equal(parked.type, "interrupt");
  assert.match(parked.question, /Approve this plan\?/);
  assert.deepEqual(parked.choices, ["approve", "reject"]);
  assert.equal(parked.node, "approve");
  assert.ok(parked.key);

  // The node it stopped in is closed as interrupted rather than left open, and
  // the run has no `done`: the hub parks it and waits for a person.
  const interrupted = out.filter((f) => f.type === "node_end" && f.status === "interrupted");
  assert.deepEqual(interrupted.map((f) => f.node), ["approve"]);
  assert.equal(out.some((f) => f.type === "done"), false);
});

test("the answer continues the graph where it stopped", async () => {
  serve("./approval_graph.js:graph", { AGENTHUB_INPUT_KEY: "request", AGENTHUB_OUTPUT_KEY: "steps" });
  await frames(streamFrames({ prompt: "ship 4.2", run_id: "pause-2" }));
  const out = await frames(resumeFrames({ run_id: "pause-2", value: "approve" }));

  assert.deepEqual(nodesOf(out, "node_start"), ["approve", "carry_out"]);
  const done = out.at(-1);
  assert.equal(done.ok, true);
  assert.match(done.output, /carried out/);
});

test("a rejected plan is still a finished run", async () => {
  serve("./approval_graph.js:graph", { AGENTHUB_INPUT_KEY: "request", AGENTHUB_OUTPUT_KEY: "steps" });
  await frames(streamFrames({ prompt: "ship 4.2", run_id: "pause-3" }));
  const out = await frames(resumeFrames({ run_id: "pause-3", value: "reject" }));
  assert.match(out.at(-1).output, /rejected/);
});

test("a run nobody streams reports the pause as awaiting_input", async () => {
  serve("./approval_graph.js:graph", { AGENTHUB_INPUT_KEY: "request", AGENTHUB_OUTPUT_KEY: "steps" });
  const result = await runOnce({ prompt: "ship 4.2", run_id: "pause-4" });

  assert.equal(result.ok, true);
  assert.equal(result.status, "awaiting_input");
  assert.equal(result.interrupt.node, "approve");
  assert.match(result.output, /Approve this plan\?/);
});

test("a node that throws closes as failed and the run keeps its explanation", async () => {
  serve("./test/fixtures.js:broken");
  const out = await frames(streamFrames({ prompt: "go", run_id: "r5" }));

  // Nothing on this runtime reports the failing node, so the adapter closes it
  // from the catch block; a node left open would light up forever on the mirror.
  const failed = out.filter((f) => f.type === "node_end" && f.ok === false);
  assert.deepEqual(failed.map((f) => f.node), ["boom"]);
  const done = out.at(-1);
  assert.equal(done.ok, false);
  assert.match(done.error, /the model provider said no/);
});

test("a repository that exports a factory is served like one that exports a graph", async () => {
  serve("./test/fixtures.js:makeGraph");
  const out = await frames(streamFrames({ prompt: "go", run_id: "r6" }));
  assert.deepEqual(nodesOf(out, "node_start"), ["only"]);
  assert.equal(out.at(-1).ok, true);
});

test("a graph that did not load is reported, not thrown", async () => {
  serve("./demo_graph.js:nope");
  const probe = await health();
  assert.equal(probe.status, "error");
  assert.match(probe.error, /has no export 'nope'/);

  const out = await frames(streamFrames({ prompt: "go", run_id: "r7" }));
  assert.deepEqual(out.map((f) => f.type), ["done"]);
  assert.equal(out[0].ok, false);

  const shape = await topology();
  assert.equal(shape.ok, false);
  assert.deepEqual(shape.nodes, []);
});

test("an unset AGENTHUB_GRAPH says so instead of serving something else", async () => {
  resetGraph();
  const probe = await health();
  assert.equal(probe.status, "error");
  assert.match(probe.error, /AGENTHUB_GRAPH is not set/);
  assert.equal((await runOnce({ prompt: "go" })).ok, false);
});

test("a healthy graph reports what it loaded", async () => {
  serve("./demo_graph.js:graph");
  const probe = await health();
  assert.equal(probe.status, "ok");
  assert.equal(probe.graph, "./demo_graph.js:graph");
  assert.equal(probe.input_key, "messages");
  assert.ok(probe.nodes > 0);
});
