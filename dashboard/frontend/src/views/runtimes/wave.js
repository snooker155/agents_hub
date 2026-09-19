// Wave-field runtime (client tier) — the 2D wave equation on a small grid with
// a periodic drip source, finite-difference integrated. The real-time
// approximation of scenarios 3/4 field dynamics; the precise counterpart is the
// server `wave2d`. Outputs a values+shape field frame the renderer heatmaps.

export function create(spec) {
  const bounds = spec?.bounds || [100, 100];
  const p0 = spec?.params || {};
  const N = Math.min(128, Math.max(16, Math.round(p0.grid ?? 64)));
  let u = new Float64Array(N * N);        // field
  let v = new Float64Array(N * N);        // du/dt
  let t = 0;
  let nextDrip = 0;

  return {
    step(dt, params = {}) {
      const c = params.c ?? 18;                    // wave speed (cells/s)
      const damping = params.damping ?? 0.12;
      const dripRate = params.drip ?? 0.8;         // drips per second
      const h = Math.min(dt, 1 / 30);
      t += h;
      if (dripRate > 0 && t >= nextDrip) {
        nextDrip = t + 1 / dripRate;
        const ix = 2 + Math.floor(Math.random() * (N - 4));
        const iy = 2 + Math.floor(Math.random() * (N - 4));
        u[iy * N + ix] += params.amplitude ?? 3;
      }
      const c2 = c * c;
      for (let y = 1; y < N - 1; y++) {
        for (let x = 1; x < N - 1; x++) {
          const i = y * N + x;
          const lap = u[i - 1] + u[i + 1] + u[i - N] + u[i + N] - 4 * u[i];
          v[i] += (c2 * lap - damping * v[i]) * h;
        }
      }
      for (let i = 0; i < u.length; i++) u[i] += v[i] * h;
    },
    output() {
      let energy = 0;
      for (let i = 0; i < u.length; i++) energy += u[i] * u[i] + v[i] * v[i];
      return {
        bounds,
        values: Array.from(u),
        shape: [N, N],
        range: [-1.5, 1.5],
        aggregates: { grid: N, energy: energy / u.length },
      };
    },
  };
}
