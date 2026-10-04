#!/usr/bin/env node
/**
 * The other shape an integration takes, next to index.html's embedded widget:
 * a backend process calling an agent directly through @agents-hub/sdk, with
 * no browser and no widget involved.
 *
 * Usage:
 *   AGENTS_HUB_URL=http://localhost:8000 AGENTS_HUB_API_KEY=ahk_... \
 *     node examples/widget/node-example.mjs support-bot "Where is order 42?"
 */
import { AgentsHub } from "../../src/index.ts";

const baseUrl = process.env.AGENTS_HUB_URL ?? "http://localhost:8000";
const apiKey = process.env.AGENTS_HUB_API_KEY;
const agentId = process.argv[2] ?? "support-bot";
const message = process.argv[3] ?? "Hello!";

const hub = new AgentsHub({ baseUrl, apiKey });

for await (const event of hub.chat.send({ agent_id: agentId, message }, { stream: true })) {
  if (event.type === "token") process.stdout.write(String(event.token ?? ""));
  if (event.type === "done" && !event.ok) process.stderr.write(`\n${String(event.error ?? "failed")}\n`);
}
process.stdout.write("\n");
