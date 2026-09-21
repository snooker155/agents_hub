/**
 * The callback handler: one object in your config, nothing else touched.
 *
 *   await graph.invoke(state, { callbacks: [tracer], configurable: { thread_id } });
 *
 * LangGraph passes callbacks into every node, so this sees model tokens, tool
 * calls, node boundaries and token usage without the graph knowing it is
 * watched. It is a plain object rather than a `BaseCallbackHandler` subclass,
 * which is why this package depends on nothing: LangChain accepts either.
 *
 * Three things the JS runtime does differently from the Python one, each found
 * by running a real graph rather than by reading about it:
 *
 *   - `__start__` arrives as a node like any other. It is not work anybody
 *     wants in a run's path, so it is skipped, which also keeps the path
 *     identical to the Python client's for the same graph.
 *   - a suspended node is reported through `handleChainError` with a
 *     `GraphInterrupt`, so the callback that says a node died and the one that
 *     says it is waiting for a person are the same callback.
 *   - nothing in any callback carries the question: `__interrupt__` is added to
 *     what `invoke` *returns*. The graph itself has to be asked, which is why
 *     passing `graph` is what makes a pause carry what it is waiting for.
 */

import { HubClient, Transport, warnOnce } from "./client.js";
import { clip, graphTopology, pendingQuestion } from "./topology.js";

export const ANSWER_POLL_MS = 2000;
export const ANSWER_WAIT_MS = 6 * 60 * 60 * 1000;

const TERMINALS = new Set(["__start__", "__end__"]);

// LangChain turns anything that is not a BaseCallbackHandler into one by
// copying the *own* properties off it (`BaseCallbackHandler.fromMethods`). A
// class instance keeps its methods on the prototype, where that copy cannot see
// them — so a tracer passed as `callbacks: [tracer]` would be accepted and then
// never called. Binding these onto the instance is what makes the documented
// usage work, and it is the kind of thing only a real run finds.
const HANDLER_METHODS = [
  "handleChainStart",
  "handleChainEnd",
  "handleChainError",
  "handleLLMNewToken",
  "handleLLMEnd",
  "handleToolStart",
  "handleToolEnd",
  "handleToolError",
];

function isInterrupt(error) {
  // Matched by name rather than by class: importing LangGraph's internals to
  // recognise one exception would trade this package's only real property — no
  // dependencies — for nothing.
  const name = error?.name ?? error?.constructor?.name ?? "";
  return name === "GraphInterrupt" || name === "NodeInterrupt" || Boolean(error?.interrupts);
}

function stateText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(stateText).filter(Boolean).join("\n");
  if (typeof value === "object") {
    const messages = value.messages;
    if (Array.isArray(messages) && messages.length > 0) {
      const last = messages[messages.length - 1];
      return stateText(last?.content ?? last);
    }
    for (const key of ["output", "input", "question", "answer", "text"]) {
      if (typeof value[key] === "string") return value[key];
    }
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
  return String(value);
}

function usageOf(output) {
  const generation = output?.generations?.[0]?.[0];
  const message = generation?.message ?? generation;
  const usage = message?.usage_metadata
    ?? output?.llmOutput?.tokenUsage
    ?? output?.llmOutput?.estimatedTokenUsage;
  if (!usage) return null;
  const frame = { type: "usage" };
  const pairs = [
    ["input_tokens", usage.input_tokens ?? usage.promptTokens],
    ["output_tokens", usage.output_tokens ?? usage.completionTokens],
    ["total_tokens", usage.total_tokens ?? usage.totalTokens],
  ];
  for (const [key, value] of pairs) {
    if (typeof value === "number") frame[key] = value;
  }
  const cached = usage.input_token_details?.cache_read;
  if (typeof cached === "number") frame.cached_tokens = cached;
  return Object.keys(frame).length > 1 ? frame : null;
}

export class HubTracer {
  constructor({
    url = process.env.AGENTS_HUB_URL ?? "",
    token = process.env.AGENTS_HUB_TOKEN ?? "",
    graph = null,
    thread = null,
    title = null,
    model = null,
    provider = null,
    metadata = {},
    transport = null,
    onError = null,
    flushMs = undefined,
  } = {}) {
    this.client = new HubClient(transport ?? new Transport(url, token), { onError, flushMs });
    this.graph = graph;
    this.thread = thread;
    this.title = title;
    this.model = model;
    this.provider = provider;
    this.metadata = metadata;
    this.onError = onError;
    // Failures on this side of the wire: a callback that went wrong, a graph
    // that could not be read. The client keeps its own count, and `errors` is
    // the two together.
    this._errors = 0;

    this._root = null;
    this._nodes = new Map();
    this._tools = new Map();
    this._depth = 0;
    this._text = [];
    this._threadId = null;
    this._paused = false;
    this._resumedFrom = null;
    this._lastNode = "";

    // Named so it is recognisable in LangChain's own logs, and bound so
    // LangChain can find the methods at all.
    this.name = "agents-hub-tracer";
    for (const method of HANDLER_METHODS) {
      this[method] = this[method].bind(this);
    }
  }

  // ── plumbing ──────────────────────────────────────────────────────────────

  _fail(error) {
    this._errors += 1;
    if (!this.onError) {
      warnOnce(error);
      return;
    }
    try {
      this.onError(error);
    } catch {
      /* an error handler that throws is not allowed to matter */
    }
  }

  /**
   * How many failures this tracer has swallowed.
   *
   * Everything here is swallowed by design, which leaves a mistyped token
   * looking exactly like a working setup. This is the number to assert on in a
   * test and to check once after wiring it up; `onError` is the way to see the
   * failures themselves.
   */
  get errors() {
    return this._errors + this.client.errors;
  }

  /** Every callback runs inside this: none of them may throw into the graph. */
  _guard(fn) {
    try {
      fn();
    } catch (error) {
      this._fail(error);
    }
  }

  _emit(frame) {
    this.client.emit(frame);
  }

  get parkedRunId() {
    return this.client.parkedRunId;
  }

  flush() {
    return this.client.flush();
  }

  /** Send the graph's shape, so the hub can draw it. Call once at startup. */
  async reportGraph(graph = null) {
    try {
      const target = graph ?? this.graph;
      if (!target) throw new Error("reportGraph needs a graph, here or on the tracer");
      this.graph = this.graph ?? target;
      await this.client.topology(await graphTopology(target));
    } catch (error) {
      this._fail(error);
    }
  }

  /**
   * Block until somebody answers the parked run's question.
   *
   * Polling, because in this direction the hub never calls out: your graph
   * reports to it, nothing in it reaches back into your process. Meant to be
   * used around your own resume, which only you can perform:
   *
   *   let result = await graph.invoke(state, cfg);
   *   while (result.__interrupt__) {
   *     const answer = await tracer.waitForAnswer();
   *     result = await graph.invoke(new Command({ resume: answer }), cfg);
   *   }
   */
  async waitForAnswer({ timeoutMs = ANSWER_WAIT_MS, pollMs = ANSWER_POLL_MS } = {}) {
    await this.flush();
    const runId = this.client.parkedRunId;
    if (!runId) return null;

    const deadline = Date.now() + Math.max(0, timeoutMs);
    while (Date.now() < deadline) {
      const body = await this.client.readAnswer(runId);
      if (body?.status === "answered") {
        this._resumedFrom = runId;
        return body.value;
      }
      if (body && body.status !== "waiting") {
        // Parked, then closed or deleted by somebody. Waiting longer would be
        // waiting on a run that no longer exists.
        return null;
      }
      // Not unref'd, unlike the batch timer: here the caller's whole program is
      // waiting on this, and an unref'd timer would let Node decide there is
      // nothing left to do and exit in the middle of the wait.
      await new Promise((resolve) => { setTimeout(resolve, pollMs); });
    }
    return null;
  }

  // ── chains: the run, and the graph's nodes ────────────────────────────────

  handleChainStart(chain, inputs, runId, parentRunId, tags, metadata, runType, runName) {
    this._guard(() => {
      const node = String(metadata?.langgraph_node ?? "");
      const name = String(runName ?? "");

      if (!parentRunId) {
        if (this._root) {
          this._fail(new Error(
            "HubTracer is already following a run; make one per invocation rather "
            + "than sharing one across concurrent runs",
          ));
          return;
        }
        this._root = runId;
        this._text = [];
        this._threadId = metadata?.thread_id ? String(metadata.thread_id) : null;
        const resumedFrom = this._resumedFrom;
        this._resumedFrom = null;
        this.client.open({
          input: clip(stateText(inputs)),
          resumed_from: resumedFrom,
          thread: this.thread,
          title: this.title ?? name ?? null,
          model: this.model,
          provider: this.provider,
          metadata: this.metadata,
        });
        return;
      }

      // Only the chain that *is* the node, and not the graph's own terminals:
      // `__start__` is reported here like any other node and is not work.
      if (!node || node !== name || TERMINALS.has(node)) return;
      this._nodes.set(runId, node);
      this._lastNode = node;
      this._emit({ type: "node_start", node, depth: this._depth });
      this._depth += 1;
    });
  }

  handleChainEnd(outputs, runId, parentRunId) {
    this._guard(() => {
      const node = this._nodes.get(runId);
      if (node !== undefined) {
        this._nodes.delete(runId);
        this._depth = Math.max(0, this._depth - 1);
        this._emit({ type: "node_end", node, ok: true });
        return;
      }
      if (this._root !== runId) return;

      const text = stateText(outputs) || this._text.join("");
      if (this._paused || this._nodes.size > 0) {
        this._closeOpenNodes(true);
        this._parkRun(text);
      } else {
        this.client.close({ ok: true, output: clip(text) });
      }
      this._reset();
    });
  }

  handleChainError(error, runId, parentRunId) {
    this._guard(() => {
      const node = this._nodes.get(runId);
      if (node !== undefined) {
        this._nodes.delete(runId);
        this._depth = Math.max(0, this._depth - 1);
        if (isInterrupt(error)) {
          // Not a failure: the graph stopped inside this node to ask somebody
          // something, and will re-enter it when answered.
          this._paused = true;
          this._emit({ type: "node_end", node, status: "interrupted", ok: true });
        } else {
          this._emit({ type: "node_end", node, ok: false, error: clip(error?.message ?? error) });
        }
        return;
      }
      if (this._root !== runId) return;

      if (isInterrupt(error)) {
        this._closeOpenNodes(true);
        this._parkRun(this._text.join(""));
      } else {
        this._closeOpenNodes(false);
        // Partial output is kept: a graph that died in its last node has
        // usually already done most of the work.
        this.client.close({
          ok: false,
          error: clip(error?.message ?? error),
          output: clip(this._text.join("")),
        });
      }
      this._reset();
    });
  }

  _parkRun(text) {
    // The question lives in the graph, not in any callback, so it is read when
    // the park is sent rather than now. Handing the client a thunk keeps the
    // run's own frames ahead of it: they were queued first, and they go first.
    const fallback = { question: "", choices: [], key: "", node: this._lastNode ?? "" };
    this.client.interrupt(async () => {
      let pending = fallback;
      try {
        pending = (await pendingQuestion(this.graph, this._threadId)) ?? fallback;
      } catch {
        // No graph, or a checkpointer that will not answer. "This run is
        // waiting, in this node" is still true and still worth reporting.
      }
      return { ...pending, output: clip(text) };
    });
  }

  _closeOpenNodes(interrupted) {
    for (const node of [...this._nodes.values()].reverse()) {
      this._emit({ type: "node_end", node, status: interrupted ? "interrupted" : "", ok: !interrupted });
    }
    this._nodes.clear();
    this._depth = 0;
  }

  _reset() {
    this._root = null;
    this._nodes.clear();
    this._tools.clear();
    this._depth = 0;
    this._paused = false;
  }

  // ── models ────────────────────────────────────────────────────────────────

  handleLLMNewToken(token) {
    this._guard(() => {
      if (!token) return;
      this._text.push(String(token));
      this._emit({ type: "token", token: String(token) });
    });
  }

  handleLLMEnd(output) {
    this._guard(() => {
      const usage = usageOf(output);
      // The only place the cost of a run exists: the model call happened in
      // this process, so a tracer that ignores this leaves the run unpriced.
      if (usage) this._emit(usage);
    });
  }

  // ── tools ─────────────────────────────────────────────────────────────────

  handleToolStart(tool, input, runId, parentRunId, tags, metadata, runName) {
    this._guard(() => {
      const name = String(runName ?? tool?.id?.at?.(-1) ?? "tool");
      this._tools.set(runId, name);
      this._emit({ type: "tool_start", name, input: clip(input, 4000) });
    });
  }

  handleToolEnd(output, runId) {
    this._guard(() => {
      const name = this._tools.get(runId) ?? "";
      this._tools.delete(runId);
      this._emit({ type: "tool_end", name, output: clip(stateText(output), 4000) });
    });
  }

  handleToolError(error, runId) {
    this._guard(() => {
      const name = this._tools.get(runId) ?? "";
      this._tools.delete(runId);
      this._emit({ type: "tool_error", name, error: clip(error?.message ?? error) });
    });
  }
}

export function createTracer(options) {
  return new HubTracer(options);
}
