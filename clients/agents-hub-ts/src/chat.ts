import type { Transport } from "./transport.ts";
import type { ChatRequest } from "./generated/types.ts";

/**
 * One event off `chat.send(request, { stream: true })`. The pipeline
 * (`chat/pipelines.py`) emits several shapes under one `type`; the common
 * ones, besides the generic `token`/`meta`/`done` every target sends:
 *
 * - a single agent: `meta`, `token` (one per piece of the answer),
 *   `tool_start`/`tool_end`, `handoff`, `done`
 * - a flow: `flow_meta`, `node_start`, `node_done`, `node_skip`, `flow_finish` or `flow_stopped`, `done`
 * - a team: `team_meta`, `team_message`, `done`
 *
 * This type only pins down the field every event has; narrow on `type` for the rest.
 */
export interface ChatStreamEvent {
  type: string;
  [key: string]: unknown;
}

/** The blocking answer from `POST /api/chat/message` (chat/send.py's own shape; not modelled further by the hub's OpenAPI schema). */
export interface ChatResult {
  ok?: boolean;
  response?: string;
  run_id?: string;
  session_id?: string;
  error?: string;
  [key: string]: unknown;
}

export interface ChatSendOptions {
  /** `false` (the default): `POST /api/chat/message`, one awaited answer. `true`: `POST /api/chat/stream`, an async iterable of `ChatStreamEvent`. */
  stream?: boolean;
  signal?: AbortSignal;
}

/**
 * `POST /api/chat/message` and `POST /api/chat/stream` (dashboard/backend/routes/chat.py):
 * direct, in-process conversation with an agent, a flow or a team, the same
 * turn the web Chat page runs, each exchange recorded as a run.
 *
 * `request` needs exactly one of `agent_id`, `flow_id` or `team_id` (the
 * hub rejects a request with none or several); `message` is the one
 * required field otherwise.
 */
export class ChatResource {
  private readonly transport: Transport;

  constructor(transport: Transport) {
    this.transport = transport;
  }

  send(request: ChatRequest, options?: { stream?: false; signal?: AbortSignal }): Promise<ChatResult>;
  send(request: ChatRequest, options: { stream: true; signal?: AbortSignal }): AsyncGenerator<ChatStreamEvent>;
  send(
    request: ChatRequest,
    options: ChatSendOptions = {},
  ): Promise<ChatResult> | AsyncGenerator<ChatStreamEvent> {
    if (options.stream) {
      return this.transport.stream<ChatStreamEvent>("/api/chat/stream", {
        body: request,
        signal: options.signal,
      });
    }
    return this.transport.request("post", "/api/chat/message", {
      body: request,
      signal: options.signal,
    }) as Promise<ChatResult>;
  }
}

export type { ChatRequest };
