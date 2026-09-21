/**
 * A compiled graph's shape, in the form the hub draws.
 *
 * Observe mode has no way to ask: the hub never calls out to a connection, so a
 * graph that wants a picture has to send one. LangGraph knows its own topology
 * and will hand it over, which makes this a translation rather than an
 * inspection — and the same translation the Python client does, so a graph
 * looks the same whichever language it is written in.
 */

export async function graphTopology(graph) {
  let raw;
  try {
    // getGraphAsync is the current spelling; getGraph is kept by older
    // versions. Neither is worth a hard dependency on which one exists.
    const drawn = typeof graph?.getGraphAsync === "function"
      ? await graph.getGraphAsync()
      : graph?.getGraph?.();
    raw = typeof drawn?.toJSON === "function" ? drawn.toJSON() : drawn;
  } catch {
    // Reporting a shape is a nice-to-have; a wrong argument here must not be
    // the thing that stops a run from being monitored.
    return { framework: "langgraph", nodes: [], edges: [] };
  }
  if (!raw) return { framework: "langgraph", nodes: [], edges: [] };

  const nodes = (raw.nodes ?? []).map((node) => {
    const id = String(node.id ?? "");
    return {
      id,
      name: String(node.name ?? id),
      // __start__/__end__ are LangGraph's own terminals; marking them lets a
      // renderer draw them as terminals rather than as work.
      kind: id === "__start__" || id === "__end__" ? "terminal" : "node",
    };
  });

  const edges = (raw.edges ?? []).map((edge) => ({
    source: String(edge.source ?? ""),
    target: String(edge.target ?? ""),
    label: edge.data ? String(edge.data) : null,
    // A conditional edge is a branch the run may or may not take, which is what
    // someone watching wants to see resolved live.
    conditional: Boolean(edge.conditional),
  }));

  return { framework: "langgraph", nodes, edges };
}

/** What a paused graph is waiting for, or null if it is not paused. */
export async function pendingQuestion(graph, threadId) {
  if (!graph || !threadId) return null;
  let snapshot;
  try {
    snapshot = await graph.getState({ configurable: { thread_id: threadId } });
  } catch {
    return null;
  }

  const interrupts = [];
  for (const task of snapshot?.tasks ?? []) {
    for (const item of task?.interrupts ?? []) interrupts.push(item);
  }
  for (const item of snapshot?.interrupts ?? []) interrupts.push(item);
  if (interrupts.length === 0) return null;

  const first = interrupts[0];
  const value = first?.value ?? first;
  const key = String(first?.id ?? "");
  // `next` names the node the graph will re-enter, which is the node it stopped
  // inside: the one thing someone looking at a paused graph wants.
  const node = String(snapshot?.next?.[0] ?? "");

  if (value && typeof value === "object" && !Array.isArray(value)) {
    const question = value.question ?? value.prompt ?? value.message;
    const choices = value.choices ?? value.options ?? [];
    return {
      // No recognised question field: show the payload itself rather than an
      // empty prompt, so the person is asked something they can act on.
      question: clip(question ?? JSON.stringify(value)),
      choices: choices.filter((c) => typeof c === "string" || typeof c === "number").map(String),
      key,
      node,
    };
  }
  return { question: clip(value), choices: [], key, node };
}

export function clip(value, limit = 2000) {
  const text = value === null || value === undefined ? "" : String(value);
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}
