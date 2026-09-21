/**
 * A graph that stops to ask a person, standing in for one that already does.
 *
 * The interesting half of human-in-the-loop is not the question, it is that the
 * graph genuinely *suspends*: its state sits in a checkpointer and the run comes
 * back to the node it stopped in. That is why a paused agent has to be continued
 * rather than run again with the answer in its prompt, because the second is a
 * different execution that merely reads the same way.
 *
 * A checkpointer is required for `interrupt()` to work at all: without one the
 * graph throws `GraphValueError: No checkpointer set`. This one is in memory,
 * which is right for an example and wrong for production, where a restart would
 * forget every paused run. A real deployment points the same graph at a durable
 * checkpointer and changes nothing else.
 *
 * Serve it with the bundled adapter:
 *
 *     AGENTHUB_GRAPH=./approval_graph.js:graph AGENTHUB_INPUT_KEY=request \
 *     AGENTHUB_OUTPUT_KEY=steps node adapter.js
 */
import { Annotation, END, MemorySaver, START, StateGraph, interrupt } from "@langchain/langgraph";

const State = Annotation.Root({
  request: Annotation,
  steps: Annotation({
    reducer: (left, right) => (left ?? []).concat(right ?? []),
    default: () => [],
  }),
});

function plan(state) {
  return { steps: [`planned: ${state.request}`] };
}

/**
 * Stop, and wait for a person.
 *
 * Whatever is passed here reaches the hub as the question. An object with
 * `question` and `choices` renders as a prompt with buttons; anything else is
 * shown as it is, so a graph that was not written for this hub still asks
 * something a person can answer.
 */
function approve(state) {
  const decision = interrupt({
    question: `Approve this plan?\n\n${state.steps.at(-1)}`,
    choices: ["approve", "reject"],
  });
  return { steps: [`decision: ${decision}`] };
}

function carryOut(state) {
  const decided = String(state.steps.at(-1) ?? "");
  if (decided.includes("reject")) return { steps: ["stopped: the plan was rejected"] };
  return { steps: ["done: the plan was carried out"] };
}

export function build() {
  return new StateGraph(State)
    .addNode("plan", plan)
    .addNode("approve", approve)
    .addNode("carry_out", carryOut)
    .addEdge(START, "plan")
    .addEdge("plan", "approve")
    .addEdge("approve", "carry_out")
    .addEdge("carry_out", END);
}

export const graph = build().compile({ checkpointer: new MemorySaver() });
