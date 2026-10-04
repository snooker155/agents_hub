import type { ApiPaths } from "./generated/types.ts";
import { errorFromResponse } from "./errors.ts";
import { readSSEJson } from "./sse.ts";

export interface AgentsHubOptions {
  /** The hub's origin, e.g. "https://hub.example.com" or "http://localhost:8000". No trailing slash needed. */
  baseUrl: string;
  /**
   * `AUTH_MODE=multi`: a personal API key (`ahk_...`, docs/api-keys.md) or a session token.
   * `AUTH_MODE=token`: the shared `AGENTS_HUB_API_TOKEN`.
   * `AUTH_MODE=single`: omit; nothing is asked.
   * Sent as `Authorization: Bearer <apiKey>`, the header `common.auth.extract_bearer` reads.
   */
  apiKey?: string;
  /** Defaults to the global `fetch` (Node 18+, or a browser). Override for a custom dispatcher or a test double. */
  fetch?: typeof fetch;
  /** Milliseconds before a non-streaming request is aborted. Streaming requests are not bounded by this: they run for as long as the hub keeps sending. */
  timeoutMs?: number;
}

type Query = Record<string, string | number | boolean | undefined>;

export interface RequestInit {
  query?: Query;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /**
   * Values for the `{name}` placeholders in a templated path, e.g.
   * `{ agent_id: "support-bot" }` for `/api/agents/{agent_id}`. The literal
   * template (with the braces) is what types the call against `ApiPaths`;
   * this is what actually gets substituted into the URL.
   */
  params?: Record<string, string | number>;
}

/** Substitute `{name}` placeholders in a templated path with `params`' values, URL-encoded. */
function fillPath(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{([^}]+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined ? match : encodeURIComponent(String(value));
  });
}

/** `ApiPaths[P][M]`'s `body` field, or `undefined` for an operation that takes none. */
type BodyOf<P extends keyof ApiPaths, M extends keyof ApiPaths[P]> =
  ApiPaths[P][M] extends { body: infer B } ? B : undefined;

/** `ApiPaths[P][M]`'s `response` field. */
type ResponseOf<P extends keyof ApiPaths, M extends keyof ApiPaths[P]> =
  ApiPaths[P][M] extends { response: infer R } ? R : unknown;

/**
 * The transport every resource (`agents`, `tasks`, `chat`, `v1`) and
 * `AgentsHub.request()` itself sit on: one place that builds the URL,
 * attaches the auth header, and turns a non-ok response into
 * `AgentsHubError` (see errors.ts).
 */
export class Transport {
  readonly baseUrl: string;
  readonly apiKey?: string;
  readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: AgentsHubOptions) {
    if (!options.baseUrl) throw new Error("AgentsHub: baseUrl is required");
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    if (!this.fetchImpl) {
      throw new Error("AgentsHub: no fetch available; pass options.fetch (Node 18+ has a global fetch)");
    }
  }

  url(path: string, query?: Query): string {
    const url = new URL(this.baseUrl + path);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  authHeaders(): Record<string, string> {
    return this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {};
  }

  /**
   * One request, parsed as JSON (or `undefined` for an empty body). Throws
   * `AgentsHubError` on a non-ok response. `init.body`, when the operation
   * takes one, is typed as that operation's own request body (`ApiPaths[P][M]`),
   * not just `unknown`: passing the wrong shape is a compile error here, same
   * as the response you get back is typed as that operation's own response.
   */
  async request<P extends keyof ApiPaths & string, M extends keyof ApiPaths[P] & string>(
    method: M,
    path: P,
    init: Omit<RequestInit, "body"> & { body?: BodyOf<P, M> } = {},
  ): Promise<ResponseOf<P, M>> {
    const response = await this.raw(method, path, init);
    if (!response.ok) throw await errorFromResponse(response);
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as ResponseOf<P, M>;
  }

  /** The same request, but returning the raw `Response` instead of parsing it (streaming callers read the body themselves). */
  async raw(method: string, path: string, init: RequestInit = {}): Promise<Response> {
    const controller = init.signal ? undefined : new AbortController();
    const signal = init.signal ?? controller?.signal;
    const timer = controller ? setTimeout(() => controller.abort(), this.timeoutMs) : undefined;
    try {
      return await this.fetchImpl(this.url(fillPath(path, init.params), init.query), {
        // ApiPaths' keys are OpenAPI's lowercase method names ("get", "post", ...);
        // sent as-is a strict server could reject them, so this is upper-cased here
        // once rather than trusted to whatever the fetch implementation does with it.
        method: method.toUpperCase(),
        headers: {
          ...(init.body !== undefined ? { "content-type": "application/json" } : {}),
          ...this.authHeaders(),
          ...init.headers,
        },
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        signal,
      });
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /**
   * A streaming POST: no request timeout (a long-running agent turn is
   * supposed to take a while), the raw response checked for an error before
   * any of its body is read, then handed to `readSSEJson`.
   */
  async *stream<T = unknown>(path: string, init: RequestInit = {}): AsyncGenerator<T> {
    const response = await this.fetchImpl(this.url(path, init.query), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
        ...this.authHeaders(),
        ...init.headers,
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: init.signal,
    });
    if (!response.ok) throw await errorFromResponse(response);
    yield* readSSEJson<T>(response.body);
  }
}

export type { BodyOf, ResponseOf };
