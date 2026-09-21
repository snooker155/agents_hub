/**
 * The transport: batched, off the caller's path, and never fatal.
 *
 * The same three rules as the Python client, for the same reason — this runs
 * inside somebody else's service:
 *
 *   1. it never throws into the graph. A monitoring handler that throws takes a
 *      production run down to draw a chart;
 *   2. it never makes the graph wait. Reporting is queued and flushed on a
 *      timer, and the graph's own code awaits nothing here;
 *   3. it never grows without bound. If the hub is unreachable the queue fills
 *      and the oldest events go, because a monitoring buffer that eats a
 *      production process is worse than missing telemetry.
 *
 * `fetch` is the global one (Node 18+), so the package has no dependencies at
 * all — not even on LangChain, since a handler here is a plain object.
 */

export const DEFAULT_FLUSH_MS = 500;
export const DEFAULT_MAX_BATCH = 200;
export const DEFAULT_QUEUE_SIZE = 10000;
// Frames are held until the run they belong to has been opened. If that failed,
// they have nowhere to go, and holding them would make the batch buffer the one
// unbounded thing here.
export const MAX_PENDING_WITHOUT_RUN = DEFAULT_MAX_BATCH * 4;

export class Transport {
  constructor(baseUrl, token, { timeoutMs = 10000 } = {}) {
    this.baseUrl = String(baseUrl || "").replace(/\/+$/, "");
    this.token = token || "";
    this.timeoutMs = timeoutMs;
  }

  async request(method, path, body) {
    // AbortSignal.timeout keeps a hung hub from holding a socket for the life
    // of the process; without it a stalled connection is a slow leak.
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      signal: AbortSignal.timeout(this.timeoutMs),
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.token}`,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} from ${path}`);
    const text = await response.text();
    return text ? JSON.parse(text) : {};
  }

  post(path, body) {
    return this.request("POST", path, body ?? {});
  }

  get(path) {
    return this.request("GET", path);
  }
}

/**
 * Say once that reporting is failing, then never again.
 *
 * Silence is right for a package living in someone's production process, and
 * wrong for the ten minutes somebody spends wiring it up: a mistyped token looks
 * exactly like success. One warning is the compromise, with `onError` and
 * `tracer.errors` for anyone who wants all of them.
 *
 * Once per *process*, not per client: the recommended usage is one tracer per
 * invocation, so a per-client warning would mean a line per run for as long as a
 * hub stayed unreachable, which is the noise this package exists not to make.
 */
let warned = false;

export function warnOnce(error) {
  if (warned) return;
  warned = true;
  const name = error?.name ?? "Error";
  const message = error?.message ?? String(error);
  console.warn(
    `agents-hub: reporting failed (${name}: ${message}). Further failures are ` +
    "silent: pass onError to see them, or read tracer.errors. Telemetry only, " +
    "the graph is unaffected.",
  );
}

/** Allow the warning again. For tests, which run many failures in one process. */
export function resetWarning() {
  warned = false;
}

export class HubClient {
  constructor(transport, {
    flushMs = DEFAULT_FLUSH_MS,
    maxBatch = DEFAULT_MAX_BATCH,
    queueSize = DEFAULT_QUEUE_SIZE,
    onError = null,
  } = {}) {
    this.transport = transport;
    this.flushMs = flushMs;
    this.maxBatch = maxBatch;
    this.queueSize = queueSize;
    this.onError = onError;

    this.runId = null;
    this.sessionId = null;
    // The last run parked for a question, kept after the run itself has gone
    // because the answer is collected against it.
    this.parkedRunId = null;
    this.dropped = 0;
    this.errors = 0;

    this._pending = [];
    this._timer = null;
    // One promise chain: everything the hub is told about a run has to arrive
    // in the order it happened, and `open` has to land before the frames that
    // depend on its run id.
    this._chain = Promise.resolve();
    this._inflight = 0;
  }

  _fail(error) {
    this.errors += 1;
    if (!this.onError) {
      warnOnce(error);
      return;
    }
    try {
      this.onError(error);
    } catch {
      // An error handler that throws must not become the failure this whole
      // class exists to avoid.
    }
  }

  /** Queue work, keeping order, and never let a rejection escape. */
  _enqueue(task) {
    this._inflight += 1;
    this._chain = this._chain
      .then(task)
      .catch((error) => this._fail(error))
      .finally(() => {
        this._inflight -= 1;
      });
    return this._chain;
  }

  open(payload) {
    return this._enqueue(async () => {
      const body = await this.transport.post("/api/ingest/runs", payload);
      this.runId = body?.run_id ?? null;
      this.sessionId = body?.session_id ?? null;
    });
  }

  emit(frame) {
    this._pending.push(frame);
    if (this._pending.length > this.queueSize) {
      // Drop the oldest: what the run is doing now is more use than what it was
      // doing when the hub stopped answering.
      const overflow = this._pending.length - this.queueSize;
      this._pending.splice(0, overflow);
      this.dropped += overflow;
    }
    if (this._pending.length >= this.maxBatch) {
      this._flushFrames();
      return;
    }
    if (this._timer === null) {
      this._timer = setTimeout(() => this._flushFrames(), this.flushMs);
      // A pending batch must not keep a finished script alive.
      if (typeof this._timer.unref === "function") this._timer.unref();
    }
  }

  _flushFrames() {
    if (this._timer !== null) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    if (this._pending.length === 0) return;
    const batch = this._pending;
    this._pending = [];
    this._enqueue(async () => {
      if (!this.runId) {
        // The run never opened; these have nowhere to go.
        this.dropped += batch.length;
        return;
      }
      for (let i = 0; i < batch.length; i += this.maxBatch) {
        await this.transport.post(`/api/ingest/runs/${this.runId}/events`, {
          events: batch.slice(i, i + this.maxBatch),
        });
      }
    });
  }

  close(payload) {
    this._flushFrames();
    return this._enqueue(async () => {
      if (this.runId) await this.transport.post(`/api/ingest/runs/${this.runId}/close`, payload);
      this.runId = null;
      this.sessionId = null;
    });
  }

  /**
   * Park the run: the graph stopped to ask a human something.
   *
   * Takes a payload or something that produces one, because what the graph is
   * waiting for has to be read from the graph, asynchronously, *after* the run
   * has ended. Resolving it inside the queued task is what keeps that read from
   * overtaking the frames the run already produced.
   */
  interrupt(payloadOrThunk) {
    this._flushFrames();
    return this._enqueue(async () => {
      const payload = typeof payloadOrThunk === "function"
        ? await payloadOrThunk()
        : await payloadOrThunk;
      if (this.runId) await this.transport.post(`/api/ingest/runs/${this.runId}/interrupt`, payload);
      this.parkedRunId = this.runId;
      this.runId = null;
      this.sessionId = null;
    });
  }

  topology(payload) {
    return this._enqueue(() => this.transport.post("/api/ingest/topology", payload));
  }

  /** Ask the hub whether a parked run's question has been answered yet. */
  async readAnswer(runId) {
    try {
      return await this.transport.get(`/api/ingest/runs/${runId}/answer`);
    } catch (error) {
      this._fail(error);
      return null;
    }
  }

  /** Wait until everything queued has reached the hub. */
  async flush() {
    this._flushFrames();
    // The chain grows while it drains, so it is awaited until nothing is left
    // rather than once.
    while (this._inflight > 0) {
      await this._chain;
    }
    return this.errors === 0;
  }
}
