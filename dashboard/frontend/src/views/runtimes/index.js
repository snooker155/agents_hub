// Client runtime registry. A runtime is a pure module exposing create(spec) →
// { step(dt, params), output() }. The useRuntime hook steps it in a Web Worker
// (runtimeWorker.js) so heavy populations never block the UI thread, falling
// back to a main-thread RAF clock where workers are unavailable. Adding a
// runtime = one entry here + a renderer that understands its output.
import { create as particles } from './particles';
import { create as boids } from './boids';
import { create as tokens } from './tokens';
import { create as nbody } from './nbody';
import { create as wave } from './wave';
import { create as sph2d } from './sph2d';
import { create as agents } from './agents';

const RUNTIMES = { particles, boids, tokens, nbody, wave, sph2d, agents };

export function createRuntime(name, spec) {
  const factory = RUNTIMES[name];
  if (!factory) return null;
  try { return factory(spec || {}); } catch { return null; }
}

export function hasRuntime(name) {
  return !!RUNTIMES[name];
}
