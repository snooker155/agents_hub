/**
 * Test doubles, shipped on purpose.
 *
 * The transport is replaceable so this package can be tested without a hub, and
 * anyone writing tests around their own graph wants the same two doubles: one
 * that records what would have been sent, one that fails the way a hub that is
 * down fails. Keeping them here means a consumer does not write them again.
 *
 * They live in `src/` rather than in `test/` because Node's test runner treats
 * every file under a directory it is given as a test file, and a helper module
 * with no tests in it fails the run.
 */
export class RecordingTransport {
  constructor() {
    this.calls = [];
    this.runCounter = 0;
    this.answer = { status: "waiting" };
  }

  async post(path, body) {
    this.calls.push({ path, body });
    if (path === "/api/ingest/runs") {
      this.runCounter += 1;
      return { run_id: `ing-${this.runCounter}`, session_id: "s1" };
    }
    return { ok: true };
  }

  async get(path) {
    this.calls.push({ path, body: null });
    return this.answer;
  }

  frames() {
    return this.calls
      .filter((c) => c.path.endsWith("/events"))
      .flatMap((c) => c.body.events);
  }

  paths() {
    return this.calls.map((c) => c.path);
  }
}

export class BrokenTransport {
  constructor() {
    this.attempts = 0;
  }

  async post() {
    this.attempts += 1;
    throw new Error("hub unreachable");
  }

  async get() {
    this.attempts += 1;
    throw new Error("hub unreachable");
  }
}

/** LangGraph.js, or null when the dev dependency is not installed. */
export async function loadLangGraph() {
  try {
    return await import("@langchain/langgraph");
  } catch {
    return null;
  }
}
