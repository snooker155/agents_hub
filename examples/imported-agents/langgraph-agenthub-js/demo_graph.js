/**
 * A small LangGraph.js graph, standing in for the one a company already has.
 *
 * Nothing in this file knows about the Agents Hub, and that is the point: it is
 * ordinary LangGraph code of the shape most teams write, and `adapter.js` serves
 * it without a single line of it changing. Read it as "their repository".
 *
 * It has a conditional edge on purpose. A straight line of nodes would not show
 * what the hub's live view is actually for: watching which branch a run took.
 *
 * The model is real when a provider key is present and a deterministic fake
 * otherwise, so the example runs, streams and can be demoed offline.
 *
 * Written as JavaScript with no build step so it can be read and run as it is.
 * The TypeScript version of this file is the same code with types on it: the
 * adapter loads either, since by then it is whatever your own toolchain runs.
 */
import { tool } from "@langchain/core/tools";
import { END, START, StateGraph } from "@langchain/langgraph";
import { MessagesAnnotation } from "@langchain/langgraph";
import { z } from "zod";

export const lookupPricing = tool(
  async ({ product }) => {
    const prices = { starter: "$29/mo", team: "$99/mo", enterprise: "talk to sales" };
    const key = String(product ?? "").trim().toLowerCase();
    return prices[key] ?? `no price on file for '${product}'`;
  },
  {
    name: "lookup_pricing",
    description: "Look up the list price of a product.",
    schema: z.object({ product: z.string() }),
  },
);

let model = null;

/**
 * A real chat model when configured, a streaming fake otherwise.
 *
 * Cached so the fake's scripted replies advance across nodes instead of every
 * node getting the first line again, and so a real model is constructed once.
 */
async function chatModel() {
  if (model) return model;
  if (process.env.OPENAI_API_KEY) {
    const { ChatOpenAI } = await import("@langchain/openai");
    model = new ChatOpenAI({ model: process.env.DEMO_MODEL ?? "gpt-4o-mini", streaming: true });
    return model;
  }
  const { FakeListChatModel } = await import("@langchain/core/utils/testing");
  model = new FakeListChatModel({
    responses: [
      "Reading the question and deciding what is needed.",
      "Here is the answer, based on what the tools returned.",
    ],
  });
  return model;
}

function lastHumanText(messages) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    const role = message?.getType?.() ?? message?.role ?? message?.type;
    if (role === "human" || role === "user") return String(message?.content ?? "").toLowerCase();
  }
  return "";
}

/** First node: think about the request. */
async function triage(state) {
  const reply = await (await chatModel()).invoke(state.messages);
  return { messages: [reply] };
}

/** The branch. Whether this request needs a pricing lookup. */
function needsPricing(state) {
  const text = lastHumanText(state.messages);
  return ["price", "pricing", "cost", "plan"].some((word) => text.includes(word)) ? "pricing" : "answer";
}

/** Tool node: the graph's own tool layer, which the hub never supplies. */
async function pricing(state) {
  let plan = "team";
  for (const candidate of ["starter", "team", "enterprise"]) {
    for (const message of state.messages) {
      if (String(message?.content ?? "").toLowerCase().includes(candidate)) plan = candidate;
    }
  }
  const result = await lookupPricing.invoke({ product: plan });
  return { messages: [{ role: "assistant", content: `Pricing for ${plan}: ${result}` }] };
}

/** Last node: produce the reply the caller sees. */
async function answer(state) {
  const reply = await (await chatModel()).invoke(state.messages);
  return { messages: [reply] };
}

export function build() {
  return new StateGraph(MessagesAnnotation)
    .addNode("triage", triage)
    .addNode("pricing", pricing)
    .addNode("answer", answer)
    .addEdge(START, "triage")
    .addConditionalEdges("triage", needsPricing, { pricing: "pricing", answer: "answer" })
    .addEdge("pricing", "answer")
    .addEdge("answer", END);
}

// What AGENTHUB_GRAPH points at: ./demo_graph.js:graph
export const graph = build().compile();
