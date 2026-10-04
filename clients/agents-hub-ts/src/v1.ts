import type { Transport } from "./transport.ts";
import { errorFromResponse } from "./errors.ts";

/**
 * `POST /v1/chat/completions`'s body (dashboard/backend/routes/openai_compat.py,
 * docs/hub-as-provider.md). The route reads this as plain JSON, not a Pydantic
 * model, so it carries no named schema in the hub's OpenAPI document; this is
 * hand-written from the route and the doc instead of generated.
 */
export interface ChatCompletionMessage {
  role: "system" | "developer" | "user" | "assistant" | "tool";
  content: string | Array<{ type: "text"; text: string }>;
  /** Only on an `assistant` message that called a tool. Refused for an agent model: the hub runs an agent's own tools. */
  tool_calls?: Array<{ id: string; type: "function"; function: { name: string; arguments: string } }>;
  /** Only on a `tool` message, pairing it with the `tool_calls` entry it answers. */
  tool_call_id?: string;
}

export interface ChatCompletionTool {
  type: "function";
  function: { name: string; description?: string; parameters?: Record<string, unknown> };
}

export interface ChatCompletionRequest {
  /** A catalog id (`"openai/gpt-4o"`, a bare id, `"default"`) or `"agent:<agent_id>"` (docs/hub-as-provider.md, "Agents as models"). */
  model: string;
  messages: ChatCompletionMessage[];
  temperature?: number;
  max_tokens?: number;
  max_completion_tokens?: number;
  stop?: string | string[];
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
  /** Refused (400) for an agent model: an agent runs its own tools on the hub. */
  tools?: ChatCompletionTool[];
  tool_choice?: "none" | "auto" | "required" | { type: "function"; function: { name: string } };
  response_format?: { type: "text" | "json_object" } | { type: "json_schema"; json_schema: Record<string, unknown> };
  /** Only `1` is accepted. */
  n?: number;
  user?: string;
  /** An agent model only: build the turn from this stored version instead of the live definition. */
  agent_version?: number;
  /** An agent model only: the per-run overrides object (docs/agents.md, "Per-run overrides"). */
  overrides?: Record<string, unknown>;
}

export interface ChatCompletionUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated?: boolean;
}

export interface ChatCompletionChoice {
  index: number;
  message: ChatCompletionMessage;
  finish_reason: string | null;
}

/** Present only when `model` was `"agent:<agent_id>"`, so a caller can link to the run (docs/hub-as-provider.md). */
export interface AgentsHubExtra {
  agent_id: string;
  workspace: string;
  run_id: string;
}

export interface ChatCompletion {
  id: string;
  object: "chat.completion";
  created: number;
  model: string;
  choices: ChatCompletionChoice[];
  usage?: ChatCompletionUsage;
  agents_hub?: AgentsHubExtra;
}

export interface ChatCompletionChunkDelta {
  role?: "assistant";
  content?: string;
  tool_calls?: Array<{
    index: number;
    id?: string;
    type?: "function";
    function?: { name?: string; arguments?: string };
  }>;
}

export interface ChatCompletionChunkChoice {
  index: number;
  delta: ChatCompletionChunkDelta;
  finish_reason: string | null;
}

export interface ChatCompletionChunk {
  id: string;
  object: "chat.completion.chunk";
  created: number;
  model: string;
  choices: ChatCompletionChunkChoice[];
  usage?: ChatCompletionUsage;
  agents_hub?: AgentsHubExtra;
  /** A provider failure after streaming started (`finish_reason: "error"`). */
  error?: { message: string; type?: string; code?: string };
}

export interface ChatCompletionsOptions {
  /** `X-Agents-Hub-Workspace`: which workspace's agents answer for an `agent:<id>` model (docs/hub-as-provider.md, "Agents as models"). */
  workspace?: string;
  signal?: AbortSignal;
}

/** `GET /v1/models`'s `data` entries: the catalog's models, then every agent the caller may run as `agent:<id>`. */
export interface V1Model {
  id: string;
  object: "model";
  owned_by: string;
  created: number;
  provider?: string;
  model?: string;
  context_window?: number;
  agent_id?: string;
  name?: string;
  description?: string;
}

class ChatCompletionsResource {
  private readonly transport: Transport;

  constructor(transport: Transport) {
    this.transport = transport;
  }

  create(body: ChatCompletionRequest & { stream?: false }, options?: ChatCompletionsOptions): Promise<ChatCompletion>;
  create(
    body: ChatCompletionRequest & { stream: true },
    options?: ChatCompletionsOptions,
  ): AsyncGenerator<ChatCompletionChunk>;
  create(
    body: ChatCompletionRequest,
    options: ChatCompletionsOptions = {},
  ): Promise<ChatCompletion> | AsyncGenerator<ChatCompletionChunk> {
    const headers = options.workspace ? { "X-Agents-Hub-Workspace": options.workspace } : undefined;
    if (body.stream) {
      return this.transport.stream<ChatCompletionChunk>("/v1/chat/completions", {
        body,
        headers,
        signal: options.signal,
      });
    }
    return this.transport.raw("POST", "/v1/chat/completions", { body, headers, signal: options.signal }).then(
      async (response) => {
        if (!response.ok) throw await errorFromResponse(response);
        return (await response.json()) as ChatCompletion;
      },
    );
  }
}

class ModelsResource {
  private readonly transport: Transport;

  constructor(transport: Transport) {
    this.transport = transport;
  }

  list(): Promise<{ object: "list"; data: V1Model[] }> {
    return this.transport.raw("GET", "/v1/models").then(async (response) => {
      if (!response.ok) throw await errorFromResponse(response);
      return (await response.json()) as { object: "list"; data: V1Model[] };
    });
  }

  /** `id` may be a full `provider/model`, a bare id, `"default"` or `"agent:<id>"` (docs/hub-as-provider.md, "Model names"). */
  get(id: string): Promise<V1Model> {
    return this.transport.raw("GET", `/v1/models/${encodeURIComponent(id)}`).then(async (response) => {
      if (!response.ok) throw await errorFromResponse(response);
      return (await response.json()) as V1Model;
    });
  }
}

/** The OpenAI-compatible surface under `/v1` (docs/hub-as-provider.md): any OpenAI SDK's `base_url` and `api_key` work here too; this is for not pulling one in. */
export class V1Resource {
  readonly chat: { completions: ChatCompletionsResource };
  readonly models: ModelsResource;

  constructor(transport: Transport) {
    this.chat = { completions: new ChatCompletionsResource(transport) };
    this.models = new ModelsResource(transport);
  }
}
