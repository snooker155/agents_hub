import { Transport, type AgentsHubOptions, type RequestInit } from "./transport.ts";
import type { ApiPaths } from "./generated/types.ts";
import { AgentsResource } from "./agents.ts";
import { TasksResource } from "./tasks.ts";
import { ChatResource } from "./chat.ts";
import { V1Resource } from "./v1.ts";

type BodyOf<P extends keyof ApiPaths, M extends keyof ApiPaths[P]> =
  ApiPaths[P][M] extends { body: infer B } ? B : undefined;

type ResponseOf<P extends keyof ApiPaths, M extends keyof ApiPaths[P]> =
  ApiPaths[P][M] extends { response: infer R } ? R : unknown;

/**
 * A typed client for one Agents Hub.
 *
 * ```ts
 * import { AgentsHub } from "@agents-hub/sdk";
 *
 * const hub = new AgentsHub({ baseUrl: "http://localhost:8000", apiKey: "ahk_..." });
 * const agents = await hub.agents.list();
 * const { response } = await hub.chat.send({ agent_id: "support-bot", message: "Where is order 42?" });
 * ```
 *
 * `agents`, `tasks` and `chat` are the dashboard's own `/api` surface
 * (dashboard/backend/routes/agents.py, tasks.py, chat.py); `v1` is the
 * OpenAI-compatible surface any OpenAI SDK could call instead
 * (docs/hub-as-provider.md). `request()` reaches anything else: every route
 * the hub serves is in the generated `ApiPaths` type, keyed by path and
 * method, so a call against a path that exists is typed even when this class
 * has no dedicated method for it yet.
 */
export class AgentsHub {
  private readonly transport: Transport;
  readonly agents: AgentsResource;
  readonly tasks: TasksResource;
  readonly chat: ChatResource;
  readonly v1: V1Resource;

  constructor(options: AgentsHubOptions) {
    this.transport = new Transport(options);
    this.agents = new AgentsResource(this.transport);
    this.tasks = new TasksResource(this.transport);
    this.chat = new ChatResource(this.transport);
    this.v1 = new V1Resource(this.transport);
  }

  /**
   * Any `/api` or `/v1` route, typed by `ApiPaths`. `path` is the route's own
   * template (`"/api/agents/{agent_id}"`, braces and all); pass the real
   * value through `init.params` and it is substituted into the URL actually
   * requested.
   */
  request<P extends keyof ApiPaths & string, M extends keyof ApiPaths[P] & string>(
    method: M,
    path: P,
    init?: Omit<RequestInit, "body"> & { body?: BodyOf<P, M> },
  ): Promise<ResponseOf<P, M>> {
    return this.transport.request(method, path, init);
  }
}

export type { AgentsHubOptions };
export { AgentsHubError } from "./errors.ts";
export type { AgentListParams, AgentList } from "./agents.ts";
export type { TaskListParams, TaskList } from "./tasks.ts";
export type { ChatStreamEvent, ChatResult, ChatSendOptions } from "./chat.ts";
export * from "./v1.ts";
