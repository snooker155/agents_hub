/**
 * Report a LangGraph.js graph's runs to an Agents Hub, without changing the graph.
 *
 *   import { HubTracer } from "agents-hub-langgraph";
 *
 *   const tracer = new HubTracer({ url, token, graph });
 *   await tracer.reportGraph();                       // once, so the hub can draw it
 *   await graph.invoke(state, { callbacks: [tracer], configurable: { thread_id } });
 *
 * The graph keeps its own models, tools, prompts, deployment and triggers. This
 * only watches, and it is built so that watching can never be the reason a
 * production run fails: every callback swallows its errors, nothing is awaited
 * on the graph's path, and the queue is bounded.
 *
 * Zero dependencies — not even on LangChain, because a callback handler may be
 * a plain object and `fetch` is in the runtime.
 */
export { HubTracer, createTracer, ANSWER_POLL_MS, ANSWER_WAIT_MS } from "./src/tracer.js";
export { HubClient, Transport } from "./src/client.js";
export { graphTopology, pendingQuestion } from "./src/topology.js";
