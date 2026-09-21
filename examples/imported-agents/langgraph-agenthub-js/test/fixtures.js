/** Graphs the tests need that no reader needs: a broken one, and a factory. */
import { Annotation, END, START, StateGraph } from "@langchain/langgraph";

const State = Annotation.Root({
  steps: Annotation({ reducer: (a, b) => (a ?? []).concat(b ?? []), default: () => [] }),
});

/** A node that throws, for the failure path. */
export const broken = new StateGraph(State)
  .addNode("fine", () => ({ steps: ["fine"] }))
  .addNode("boom", () => {
    throw new Error("the model provider said no");
  })
  .addEdge(START, "fine")
  .addEdge("fine", "boom")
  .addEdge("boom", END)
  .compile();

/** A repository that exports a builder rather than a compiled graph. */
export function makeGraph() {
  return new StateGraph(State)
    .addNode("only", () => ({ steps: ["built by a factory"] }))
    .addEdge(START, "only")
    .addEdge("only", END)
    .compile();
}
