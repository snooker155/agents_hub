/**
 * A LangGraph.js graph behind the Agents Hub HTTP contract, without touching it.
 *
 * The situation this exists for: a team already has a working LangGraph flow, in
 * TypeScript, inside their own service, with their prompts, their models, their
 * checkpointer and their deployment. They want the hub's chat, run records, cost
 * accounting and live view. They do not want the graph moved into the hub,
 * rewritten as a hub flow, or edited at all.
 *
 * So nothing here imports *their* code at build time. The graph is named at
 * startup with `AGENTHUB_GRAPH`, in the same `module:export` spelling
 * LangGraph's own `langgraph.json` uses for a JS project:
 *
 *     AGENTHUB_GRAPH=./src/agent.js:graph
 *
 * which means the integration is one process, one env var and zero diffs in the
 * graph. Point it at a compiled graph (`new StateGraph(...).compile()`) or at a
 * zero-argument factory returning one; both are common ways a repository
 * exposes its graph, and guessing wrong would be the first support question.
 *
 * What it serves, the contract from `agents/remote_agent.py`:
 *
 *     GET  /health        liveness, plus what graph was loaded
 *     POST /run           {"prompt","run_id","workspace"} -> {"ok","output","error"},
 *                         or {"ok","status":"awaiting_input","interrupt":{…}} on a pause
 *     POST /run/stream    same body -> NDJSON frames
 *     POST /resume        {"run_id","value"} -> the same frames, continuing a pause
 *     GET  /graph         the graph's own topology, declared as runtime.graph_path
 *
 * The hub's `run_id` becomes the graph's `thread_id`, which is what makes
 * `/resume` possible at all: the checkpointer finds the suspended run by it. A
 * graph compiled without a checkpointer cannot pause, and this adapter serves it
 * exactly as before.
 *
 * `node:http` rather than Express or Fastify, and no other dependency than the
 * graph's own: an adapter a team is asked to run beside their service should add
 * nothing to their dependency tree that they have to justify.
 *
 * The interesting half is `translate`. LangGraph narrates itself through
 * `streamEvents`: every model token, every tool call, every node entry and exit
 * arrives with the node name in its metadata, so the mapping onto the hub's
 * frames is almost mechanical. Four places where the JS runtime differs from the
 * Python one, each found by running a real graph rather than by reading about it:
 *
 *   - **`parent_ids` does not exist here.** The Python adapter recognises the
 *     root chain (the event carrying the graph's final state) by its lack of
 *     parents. The root is instead the first `on_chain_start` of the stream, and
 *     it is matched by `run_id` from then on.
 *   - **A node that throws never reports itself.** Python delivers
 *     `on_chain_error` for the failing node; here the exception aborts the
 *     iteration and no error event arrives at all, so the open node is closed
 *     from the catch block instead.
 *   - **`__interrupt__` arrives on the stream**, as a chunk of the root chain,
 *     which the Python side never sees. A paused run is therefore reported
 *     without asking the checkpointer, and `getState` stays as the fallback.
 *   - **A node's label in `getGraph().toJSON()` lives under `data.name`**, not
 *     at the top level as in Python, so `/graph` reads it from there.
 */
import { createServer } from "node:http";
import { isAbsolute, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { Command } from "@langchain/langgraph";

const DEFAULT_PORT = 8421;

// A prompt is text, not an upload. Anything larger is a mistake worth naming
// rather than a body worth buffering.
const MAX_BODY_BYTES = 1_000_000;

// LangGraph's own terminals. They arrive as ordinary nodes on the JS stream,
// which is not work anybody wants in a run's path; skipping them also keeps the
// path identical to the one the Python adapter reports for the same graph.
const TERMINALS = new Set(["__start__", "__end__"]);

/**
 * Configuration, read per call rather than captured at import.
 *
 * `AGENTHUB_GRAPH` has no default on purpose: an adapter that silently served
 * the bundled demo graph instead of the graph someone meant to point it at
 * would be a confusing way to lose an afternoon.
 */
export function config() {
  const env = process.env;
  return {
    // The graph to serve, as module:export.
    spec: (env.AGENTHUB_GRAPH ?? "").trim(),
    // How the prompt enters the graph's state. Most message graphs take
    // `{ messages: [...] }`; a graph with a custom state takes its own key, and
    // then the prompt is passed as that key's plain string value.
    inputKey: (env.AGENTHUB_INPUT_KEY ?? "messages").trim() || "messages",
    // Where the answer is read from in the final state. Empty means "same as
    // the input key", which is right for message graphs and most single-key ones.
    outputKey: (env.AGENTHUB_OUTPUT_KEY ?? "").trim(),
    // Tool output can be a whole retrieved document. A frame is for a human
    // reading a run, not for reprocessing, so it is bounded.
    maxFrameChars: Number(env.AGENTHUB_MAX_FRAME_CHARS ?? 4000) || 4000,
    port: Number(env.AGENTHUB_PORT ?? env.PORT ?? DEFAULT_PORT) || DEFAULT_PORT,
  };
}

// ── loading the graph ───────────────────────────────────────────────────────

let cached = null;
let cachedSpec = "";

/** Forget the loaded graph. For tests that serve several graphs in one process. */
export function resetGraph() {
  cached = null;
  cachedSpec = "";
}

/**
 * Import the graph named by `AGENTHUB_GRAPH` and cache it.
 *
 * Cached because importing a real project's graph module builds its models, its
 * tool clients and sometimes its checkpointer connection. Doing that per request
 * would add seconds to every run and open a connection pool per call.
 */
export async function loadGraph() {
  const { spec } = config();
  if (cached && cachedSpec === spec) return cached;
  if (!spec) {
    throw new Error(
      "AGENTHUB_GRAPH is not set. Point it at your compiled graph, " +
      "for example AGENTHUB_GRAPH=./src/agent.js:graph",
    );
  }
  // The *last* colon, not the first: a specifier may be a `file://` URL or a
  // Windows path, and both carry one of their own.
  const cut = spec.lastIndexOf(":");
  if (cut <= 0) {
    throw new Error(`AGENTHUB_GRAPH must be 'module:export', got '${spec}'`);
  }
  const modulePath = spec.slice(0, cut);
  const exportName = spec.slice(cut + 1).trim() || "default";

  // A path is resolved against the working directory, the way `langgraph.json`
  // resolves its own; a bare specifier is left alone so a graph published as a
  // package can be named directly.
  const looksLikePath = modulePath.startsWith(".") || isAbsolute(modulePath);
  const specifier = looksLikePath
    ? pathToFileURL(resolve(process.cwd(), modulePath)).href
    : modulePath;

  const module = await import(specifier);
  let graph = module[exportName];
  if (graph === undefined) {
    const available = Object.keys(module).join(", ") || "none";
    throw new Error(`'${modulePath}' has no export '${exportName}' (exports: ${available})`);
  }
  // A repository may export the compiled graph, or a factory that builds it.
  // `streamEvents` is what tells the two apart without calling anything.
  if (typeof graph?.streamEvents !== "function" && typeof graph === "function") {
    graph = await graph();
  }
  if (typeof graph?.streamEvents !== "function") {
    throw new Error(
      `'${spec}' is not a compiled LangGraph graph (no streamEvents). ` +
      "Did you export the StateGraph instead of the result of .compile()?",
    );
  }
  cached = graph;
  cachedSpec = spec;
  return graph;
}

/** Build the graph's input state from one prompt. */
export function graphInput(prompt) {
  const { inputKey } = config();
  if (inputKey === "messages") {
    // The plain object shape rather than a HumanMessage: the message reducers
    // coerce it, and it keeps this file independent of `@langchain/core`.
    return { messages: [{ role: "user", content: String(prompt ?? "") }] };
  }
  return { [inputKey]: String(prompt ?? "") };
}

/**
 * Config for one run.
 *
 * `thread_id` is set from the hub's run id so a graph with a checkpointer keeps
 * each hub run in its own thread instead of appending every run in the product
 * to one shared conversation. `workspace` is passed through untouched: the hub
 * knows what it means, this adapter does not.
 */
export function runConfig(req) {
  return {
    configurable: {
      thread_id: req?.run_id || "agenthub",
      agenthub_run_id: req?.run_id ?? null,
      agenthub_workspace: req?.workspace ?? null,
    },
  };
}

/** The same, for a streamed run. `version` is required by `streamEvents` here. */
export function streamConfig(req) {
  return { ...runConfig(req), version: "v2" };
}

function clip(text) {
  const { maxFrameChars } = config();
  const value = text ?? "";
  return value.length <= maxFrameChars ? value : `${value.slice(0, maxFrameChars)}…`;
}

function errText(error) {
  if (error === null || error === undefined) return "";
  const name = error?.name ?? error?.constructor?.name ?? "Error";
  const message = error?.message ?? String(error);
  return clip(`${name}: ${message}`);
}

/**
 * Best-effort text of a message or state value.
 *
 * Content is a string or the list-of-blocks shape the newer chat models return;
 * both have to render, because which one a graph produces depends on the
 * provider it was configured with, not on anything visible here.
 */
export function textOf(value) {
  const content = value?.content ?? value;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => (typeof block === "string" ? block : typeof block?.text === "string" ? block.text : ""))
      .join("");
  }
  if (content === null || content === undefined) return "";
  return typeof content === "object" ? JSON.stringify(content) : String(content);
}

/** Pull the answer out of the graph's final state. */
export function finalOutput(state) {
  const { inputKey, outputKey } = config();
  const key = outputKey || inputKey;
  if (state === null || typeof state !== "object" || Array.isArray(state)) return textOf(state);
  const value = key in state ? state[key] : state.messages;
  if (Array.isArray(value)) return value.length ? textOf(value[value.length - 1]) : "";
  return textOf(value);
}

// ── event translation ──────────────────────────────────────────────────────

/**
 * What one streamed run accumulates while it is translated.
 *
 * `depth` exists because a subgraph re-emits node events of its own. Without
 * tracking how deep the open nodes are, a graph containing a subgraph would
 * report the inner nodes as if they were top-level, and the run would read as a
 * flat list of nodes that does not match the picture of the graph.
 */
export class StreamState {
  constructor() {
    this.textParts = [];
    this.nodeStack = [];
    this.usageSent = false;
    // The root chain's run id, learned from the first `on_chain_start`. The JS
    // events carry no `parent_ids`, so this is how the event holding the final
    // state is told apart from every nested chain's.
    this.rootRunId = null;
    // The graph's final state, captured from the root chain event. Preferred
    // over the concatenated tokens because a graph may post-process what the
    // model said, and because a graph whose model does not stream produces no
    // tokens at all while still answering perfectly well.
    this.finalState = null;
    this.lastMessage = "";
    // What the graph stopped to ask, as it arrived on the stream.
    this.interruptEntry = null;
    // A finished node's frame, held back until the next node starts so it can
    // name the branch that was taken. LangGraph does not announce the routing
    // decision itself: the only evidence of which way a conditional edge went
    // is which node runs next, and a graph's branches are the main thing
    // someone watching it wants answered. The wait is the microseconds between
    // one node returning and the next being entered.
    this.pendingEnd = null;
  }

  /**
   * Release the held `node_end`, naming the next node when it fits.
   *
   * `next` is only filled for a node at the same depth: a subgraph's last node
   * is not followed by its sibling but by whatever comes after the subgraph, and
   * claiming otherwise would draw an edge that does not exist.
   */
  flushEnd(nextNode = "", nextDepth = -1) {
    const frame = this.pendingEnd;
    this.pendingEnd = null;
    if (!frame) return [];
    const depth = frame._depth ?? -1;
    delete frame._depth;
    if (nextNode && nextDepth === depth) frame.next = nextNode;
    return [frame];
  }

  /** The answer, from the most authoritative source that has one. */
  output() {
    if (this.finalState !== null && this.finalState !== undefined) {
      const text = finalOutput(this.finalState);
      if (text) return text;
    }
    return this.textParts.join("").trim() || this.lastMessage;
  }
}

function nodeOf(event) {
  return String(event?.metadata?.langgraph_node ?? "");
}

/**
 * Whether this chain event is a graph node starting or finishing.
 *
 * LangGraph runs every node as a chain, so `on_chain_start` fires for the node,
 * for the runnable inside it and for each `ChannelWrite` and `Branch` it wraps.
 * Only the event whose *name* is the node's own name is the node boundary; the
 * rest are its internals, which belong in the run log, not on the graph picture.
 */
function isNodeBoundary(event) {
  const node = nodeOf(event);
  return Boolean(node) && !TERMINALS.has(node) && String(event?.name ?? "") === node;
}

/**
 * Token counts a model reported, in the hub's spelling.
 *
 * This is the only place the numbers exist: the model call happens in this
 * process, so a run the hub launched has no other way to learn what it cost.
 * `usage_metadata` is the modern, provider-independent field. The older
 * `response_metadata.tokenUsage` is still what some JS integrations fill, and it
 * is camelCase where the Python one is not, so both spellings are read.
 */
export function usageOf(message) {
  const usage = message?.usage_metadata;
  if (usage && typeof usage === "object") {
    const out = {
      prompt_tokens: Number(usage.input_tokens ?? 0) || 0,
      completion_tokens: Number(usage.output_tokens ?? 0) || 0,
      total_tokens: Number(usage.total_tokens ?? 0) || 0,
    };
    const cached = Number(usage.input_token_details?.cache_read ?? 0) || 0;
    if (cached) out.cached_tokens = cached;
    if (out.prompt_tokens || out.completion_tokens || out.total_tokens) return out;
  }
  const legacy = message?.response_metadata?.tokenUsage ?? message?.response_metadata?.token_usage;
  if (legacy && typeof legacy === "object") {
    const out = {
      prompt_tokens: Number(legacy.promptTokens ?? legacy.prompt_tokens ?? 0) || 0,
      completion_tokens: Number(legacy.completionTokens ?? legacy.completion_tokens ?? 0) || 0,
      total_tokens: Number(legacy.totalTokens ?? legacy.total_tokens ?? 0) || 0,
    };
    if (out.prompt_tokens || out.completion_tokens || out.total_tokens) return out;
  }
  return null;
}

/**
 * The input a tool was called with, as text.
 *
 * The JS events wrap it twice: `data.input` is `{ input: "<the json>" }` for a
 * structured tool, where the Python side hands over the arguments themselves.
 * Unwrapping it here is the difference between a readable tool trail and one
 * that reads `{"input":"{\"product\":\"team\"}"}` on every line.
 */
function toolInput(data) {
  const raw = data?.input;
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object") {
    const keys = Object.keys(raw);
    if (keys.length === 1 && keys[0] === "input" && typeof raw.input === "string") return raw.input;
    return JSON.stringify(raw);
  }
  return "";
}

function interruptFrame(entry, node = "") {
  const value = entry?.value ?? entry;
  const frame = { type: "interrupt", key: String(entry?.id ?? ""), node: node || "" };
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const question = value.question ?? value.prompt ?? value.message;
    frame.question = clip(question === undefined ? JSON.stringify(value) : String(question));
    const choices = value.choices ?? value.options ?? [];
    frame.choices = (Array.isArray(choices) ? choices : []).map((c) => String(c).slice(0, 200));
  } else {
    frame.question = clip(textOf(value));
    frame.choices = [];
  }
  return frame;
}

/**
 * Map one LangGraph event onto zero or more hub frames.
 *
 * Kept pure, and separate from the HTTP layer, so it can be tested against real
 * graph runs and reused by the push-mode tracer, which needs the same mapping
 * over the same events from inside a graph the hub never called.
 */
export function translate(event, state) {
  const kind = String(event?.event ?? "");
  const data = event?.data ?? {};
  const frames = [];

  if (state.rootRunId === null && kind === "on_chain_start") {
    state.rootRunId = event?.run_id ?? "";
  }
  const isRoot = Boolean(state.rootRunId) && event?.run_id === state.rootRunId;

  if (kind === "on_chat_model_stream") {
    const token = textOf(data.chunk);
    if (token) {
      state.textParts.push(token);
      frames.push({ type: "token", token });
    }
    return frames;
  }

  if (kind === "on_chat_model_end") {
    const message = data.output;
    const text = textOf(message);
    if (text) state.lastMessage = text;
    const usage = usageOf(message);
    if (usage) {
      state.usageSent = true;
      frames.push({ type: "usage", ...usage });
    }
    return frames;
  }

  if (kind === "on_tool_start") {
    frames.push({
      type: "tool_start",
      name: event?.name || "tool",
      input: clip(toolInput(data)),
    });
    return frames;
  }

  if (kind === "on_tool_end") {
    frames.push({
      type: "tool_end",
      name: event?.name || "tool",
      output: clip(textOf(data.output)),
    });
    return frames;
  }

  // The pause, as it arrives on the stream: LangGraph adds `__interrupt__` to
  // the root chain's chunks. No frame yet, because the node it stopped in is
  // known from the open nodes once the stream has ended.
  if (isRoot && kind === "on_chain_stream") {
    const pending = data.chunk?.__interrupt__;
    if (Array.isArray(pending) && pending.length) state.interruptEntry = pending[0];
    return frames;
  }

  // The root chain end carries the graph's final state. It fires on a pause too,
  // with the state as far as the graph got, so it does not mean "finished".
  if (isRoot && kind === "on_chain_end") {
    state.finalState = data.output;
    return frames;
  }

  if (kind === "on_chain_start" && isNodeBoundary(event)) {
    const node = nodeOf(event);
    state.nodeStack.push(node);
    const depth = state.nodeStack.length - 1;
    frames.push(...state.flushEnd(node, depth));
    frames.push({ type: "node_start", node, depth });
    return frames;
  }

  if (kind === "on_chain_end" && isNodeBoundary(event)) {
    const node = nodeOf(event);
    const depth = Math.max(0, state.nodeStack.length - 1);
    if (state.nodeStack[state.nodeStack.length - 1] === node) state.nodeStack.pop();
    // Two nodes cannot be pending at once: a node ends before the next one
    // starts, and the start is what releases the previous end.
    frames.push(...state.flushEnd());
    state.pendingEnd = { type: "node_end", node, ok: true, _depth: depth };
    return frames;
  }

  // No `on_chain_error` branch: on this runtime a node that throws aborts the
  // iteration without delivering one. The open node is closed by the caller.
  return frames;
}

/**
 * What the graph stopped to ask, from the checkpointer.
 *
 * The fallback for the stream chunk above, and the only source on a path with no
 * stream at all. In JS the pending interrupts hang off the snapshot's *tasks*,
 * where the Python snapshot also exposes them directly.
 */
export async function pendingInterrupt(graph, runId) {
  if (!graph || !runId || typeof graph.getState !== "function") return null;
  let snapshot;
  try {
    snapshot = await graph.getState({ configurable: { thread_id: runId } });
  } catch {
    return null;
  }
  const entries = [...(snapshot?.interrupts ?? [])];
  for (const task of snapshot?.tasks ?? []) entries.push(...(task?.interrupts ?? []));
  if (!entries.length) return null;
  return interruptFrame(entries[0], String(snapshot?.next?.[0] ?? ""));
}

// ── the endpoints, as functions ────────────────────────────────────────────

/** Liveness, plus enough to debug a graph that did not load. */
export async function health() {
  const { spec, inputKey } = config();
  let graph;
  try {
    graph = await loadGraph();
  } catch (error) {
    // Reported, not thrown: this is the probe, and a hub that gets a 500 here
    // learns less than one that gets the reason.
    return { status: "error", graph: spec, error: errText(error) };
  }
  let nodes = null;
  try {
    const drawn = graph.getGraph().nodes;
    nodes = Array.isArray(drawn) ? drawn.length : Object.keys(drawn ?? {}).length;
  } catch {
    nodes = null;
  }
  return { status: "ok", graph: spec, nodes, input_key: inputKey };
}

/**
 * The graph's own shape, so the hub can draw it instead of guessing.
 *
 * LangGraph knows its topology and will hand it over (`getGraph().toJSON()`),
 * which is what makes a read-only mirror of a foreign graph cheap: the hub never
 * parses anyone's code to learn the picture, it asks the graph.
 *
 * Normalised here rather than in the hub, because the hub should not learn one
 * framework's JSON shape to support it, and because this shape is not even the
 * same between LangGraph's two runtimes: a node's label is `data.name` here.
 */
export async function topology() {
  let raw;
  try {
    raw = (await loadGraph()).getGraph().toJSON();
  } catch (error) {
    return { ok: false, error: errText(error), nodes: [], edges: [] };
  }
  const nodes = (raw?.nodes ?? []).map((node) => {
    const id = String(node?.id ?? "");
    return {
      id,
      label: String(node?.data?.name ?? node?.name ?? id),
      // __start__/__end__ are LangGraph's own terminals. Marking them lets a
      // renderer draw them as terminals rather than as work.
      kind: TERMINALS.has(id) ? "terminal" : "node",
    };
  });
  const edges = (raw?.edges ?? []).map((edge) => ({
    source: String(edge?.source ?? ""),
    target: String(edge?.target ?? ""),
    label: edge?.data ? String(edge.data) : null,
    // A conditional edge is a branch the run may or may not take, which is
    // exactly what someone watching wants to see resolved live.
    conditional: Boolean(edge?.conditional),
  }));
  return { ok: true, framework: "langgraph", nodes, edges };
}

/**
 * Run the graph once and report the outcome.
 *
 * Failures come back as `{ok: false, error}` with HTTP 200: the hub records both
 * identically, and the body keeps the graph's own explanation, which a bare 500
 * would throw away.
 */
export async function runOnce(req) {
  let graph;
  try {
    graph = await loadGraph();
  } catch (error) {
    return { ok: false, output: "", error: errText(error) };
  }

  let result;
  try {
    result = await graph.invoke(graphInput(req?.prompt), runConfig(req));
  } catch (error) {
    return { ok: false, output: "", error: errText(error) };
  }

  // A paused graph returns `{__interrupt__: [...]}` and nothing else here, so
  // this is read from the result rather than from the state it did not return.
  const entries = Array.isArray(result?.__interrupt__) ? result.__interrupt__ : [];
  if (entries.length) {
    let node = "";
    try {
      const snapshot = await graph.getState(runConfig(req));
      node = String(snapshot?.next?.[0] ?? "");
    } catch {
      node = "";
    }
    const frame = interruptFrame(entries[0], node);
    const { type, ...interrupt } = frame;
    // Paused, not finished, and this is the path a run takes when nobody is
    // streaming it. Saying "ok, here is your answer" here would leave the graph
    // suspended with nothing that will ever answer it.
    return {
      ok: true,
      status: "awaiting_input",
      interrupt,
      output: frame.question || "",
      error: null,
    };
  }

  const output = finalOutput(result);
  return {
    ok: true,
    output: output || "The graph finished without producing output.",
    error: null,
  };
}

function frameOf(payload) {
  return `${JSON.stringify(payload)}\n`;
}

async function* framesFor(graph, input, cfg, runId) {
  const state = new StreamState();
  try {
    for await (const event of graph.streamEvents(input, cfg)) {
      for (const payload of translate(event, state)) yield frameOf(payload);
    }
  } catch (error) {
    for (const payload of state.flushEnd()) yield frameOf(payload);
    // The node that threw gets its own ending here: nothing on this runtime
    // reports it, and a node left open would light up forever on the mirror.
    const open = state.nodeStack[state.nodeStack.length - 1];
    if (open) yield frameOf({ type: "node_end", node: open, ok: false, error: errText(error) });
    // Partial output is kept: a graph that died in its last node has usually
    // already shown the user real work, and discarding it helps nobody.
    yield frameOf({ type: "done", ok: false, output: state.output(), error: errText(error) });
    return;
  }

  // The last node of a run has nothing following it, so its held frame is
  // released here without a branch.
  for (const payload of state.flushEnd()) yield frameOf(payload);

  const open = state.nodeStack[state.nodeStack.length - 1] ?? "";
  const pending = state.interruptEntry
    ? interruptFrame(state.interruptEntry, open)
    : await pendingInterrupt(graph, runId);
  if (pending) {
    // Paused, not finished. No `done` frame: the hub parks the run with this
    // question on it and waits for a person, and /resume picks it up.
    if (open) yield frameOf({ type: "node_end", node: open, status: "interrupted", ok: true });
    yield frameOf(pending);
    return;
  }

  yield frameOf({
    type: "done",
    ok: true,
    output: state.output() || "The graph finished without producing output.",
    error: null,
  });
}

/**
 * Translate one graph execution into NDJSON frames.
 *
 * The `done` frame is authoritative for the hub, so it is sent on every path
 * including failure: a run must have exactly one outcome even when the stream
 * was noisy or died halfway.
 */
export async function* streamFrames(req) {
  let graph;
  try {
    graph = await loadGraph();
  } catch (error) {
    yield frameOf({ type: "done", ok: false, output: "", error: errText(error) });
    return;
  }
  yield* framesFor(graph, graphInput(req?.prompt), streamConfig(req), req?.run_id);
}

/**
 * Continue a suspended run and stream what the graph does next.
 *
 * The run id is the thread the checkpointer filed the suspension under, which is
 * why the hub sends the id of the run that paused rather than a new one. A graph
 * may stop again on the way: an approval flow with two approvals is an ordinary
 * thing, and each pause is reported the same way as the first.
 */
export async function* resumeFrames(req) {
  let graph;
  try {
    graph = await loadGraph();
  } catch (error) {
    yield frameOf({ type: "done", ok: false, output: "", error: errText(error) });
    return;
  }
  const cfg = streamConfig({ run_id: req?.run_id, workspace: req?.workspace });
  yield* framesFor(graph, new Command({ resume: req?.value ?? null }), cfg, req?.run_id);
}

// ── the HTTP layer ─────────────────────────────────────────────────────────

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      const error = new Error(`body over ${MAX_BODY_BYTES} bytes`);
      error.status = 413;
      throw error;
    }
    chunks.push(chunk);
  }
  const body = Buffer.concat(chunks).toString("utf8").trim();
  if (!body) return {};
  try {
    return JSON.parse(body);
  } catch (err) {
    const error = new Error(`body is not JSON: ${err.message}`);
    error.status = 400;
    throw error;
  }
}

async function sendFrames(res, frames) {
  res.writeHead(200, {
    "content-type": "application/x-ndjson",
    "cache-control": "no-cache",
    // A buffering proxy in front of this would defeat streaming entirely, and
    // the symptom (everything arrives at the end) looks like a bug here.
    "x-accel-buffering": "no",
  });
  for await (const frame of frames) {
    // Backpressure matters on a chatty graph: without it a slow reader is
    // absorbed by this process's memory.
    if (!res.write(frame)) await new Promise((r) => res.once("drain", r));
  }
  res.end();
}

export const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const route = `${req.method} ${url.pathname}`;
  try {
    if (route === "GET /health") return sendJson(res, 200, await health());
    if (route === "GET /graph") return sendJson(res, 200, await topology());
    if (route === "POST /run") return sendJson(res, 200, await runOnce(await readJson(req)));
    if (route === "POST /run/stream") return sendFrames(res, streamFrames(await readJson(req)));
    if (route === "POST /resume") return sendFrames(res, resumeFrames(await readJson(req)));
    return sendJson(res, 404, { ok: false, error: `no route for ${route}` });
  } catch (error) {
    if (res.headersSent) return res.end();
    return sendJson(res, error?.status ?? 500, { ok: false, output: "", error: errText(error) });
  }
});

// Started only when this file is the program, so the functions above can be
// imported by a test without a socket being opened.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { port, spec } = config();
  server.listen(port, "0.0.0.0", () => {
    console.log(`LangGraph adapter on :${port}, serving ${spec || "(AGENTHUB_GRAPH unset)"}`);
  });
}
