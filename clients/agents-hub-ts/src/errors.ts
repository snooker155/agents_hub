/**
 * One error shape for every failure this SDK can raise, whatever shape the
 * hub answered in.
 *
 * `/api` routes answer a failure as `{"detail": "..."}` or `{"detail": "...",
 * "code": "..."}` (FastAPI's own default, or the hub's widened one, see
 * docs/widget.md's refusal codes and docs/api-keys.md's rate limit body).
 * `/v1` answers the OpenAI error shape instead: `{"error": {"message",
 * "type", "param", "code"}}` (docs/hub-as-provider.md). A caller that wants
 * one error type to catch, regardless of which door it came through, gets
 * `AgentsHubError` with both shapes folded onto the same few fields.
 */
export class AgentsHubError extends Error {
  /** The HTTP status code. */
  readonly status: number;
  /** A stable machine code when the hub sent one (`bad_key`, `model_not_found`, ...). */
  readonly code?: string;
  /** The OpenAI error `type` when the failure came from `/v1` (`invalid_request_error`, ...). */
  readonly type?: string;
  /** The parsed JSON body, for anything this class did not pull out. */
  readonly body?: unknown;

  constructor(message: string, options: { status: number; code?: string; type?: string; body?: unknown }) {
    super(message);
    this.name = "AgentsHubError";
    this.status = options.status;
    this.code = options.code;
    this.type = options.type;
    this.body = options.body;
  }
}

/** Build the error for a response whose status was not ok. Never throws itself. */
export async function errorFromResponse(response: Response): Promise<AgentsHubError> {
  const status = response.status;
  let body: unknown;
  try {
    const text = await response.text();
    body = text ? JSON.parse(text) : undefined;
  } catch {
    body = undefined;
  }

  if (body && typeof body === "object" && "error" in body) {
    const err = (body as { error?: Record<string, unknown> }).error ?? {};
    const message = typeof err.message === "string" ? err.message : `HTTP ${status}`;
    return new AgentsHubError(message, {
      status,
      code: typeof err.code === "string" ? err.code : undefined,
      type: typeof err.type === "string" ? err.type : undefined,
      body,
    });
  }

  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    const message = typeof detail === "string" ? detail : JSON.stringify(detail);
    const code = (body as { code?: unknown }).code;
    return new AgentsHubError(message || `HTTP ${status}`, {
      status,
      code: typeof code === "string" ? code : undefined,
      body,
    });
  }

  return new AgentsHubError(`HTTP ${status}`, { status, body });
}
