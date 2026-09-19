// SPH fluid runtime (client tier) — 2D smoothed-particle hydrodynamics
// (Müller-style poly6/spiky kernels): dam-break water you can stir with the
// viscosity/stiffness/gravity controls mid-run. The real-time approximation of
// scenario 3; a precise solver stays the server tier's job. Grid-hashed
// neighbour search keeps a few hundred particles interactive.

function rnd(a, b) { return a + Math.random() * (b - a); }

export function create(spec) {
  const bounds = spec?.bounds || [100, 100];
  const p0 = spec?.params || {};
  const [W, H] = bounds;
  const count = Math.min(700, Math.max(20, p0.count ?? 300));
  const h = p0.smoothing ?? 4;                 // smoothing length (world units)
  const h2 = h * h;
  const poly6 = 4 / (Math.PI * Math.pow(h, 8));
  const spikyGrad = -30 / (Math.PI * Math.pow(h, 5));
  const viscLap = 40 / (Math.PI * Math.pow(h, 5));
  const mass = 1;

  // dam-break column against the left wall
  const parts = Array.from({ length: count }, () => ({
    x: rnd(1, W * 0.35), y: rnd(H * 0.3, H - 1),
    vx: 0, vy: 0, rho: 0, p: 0,
  }));

  // uniform-grid neighbour hash, rebuilt each step
  const cell = h;
  function neighbours() {
    const map = new Map();
    for (let i = 0; i < parts.length; i++) {
      const k = `${Math.floor(parts[i].x / cell)},${Math.floor(parts[i].y / cell)}`;
      (map.get(k) || map.set(k, []).get(k)).push(i);
    }
    return (i) => {
      const gx = Math.floor(parts[i].x / cell); const gy = Math.floor(parts[i].y / cell);
      const out = [];
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          const b = map.get(`${gx + dx},${gy + dy}`);
          if (b) out.push(...b);
        }
      }
      return out;
    };
  }

  // Agent-authored specs vary: gravity may be a [gx, gy] vector in a y-up
  // convention (screen y points down here, so flip it), names may be camelCase.
  function num(v, dflt) { return typeof v === 'number' && Number.isFinite(v) ? v : dflt; }

  return {
    step(dt, params = {}) {
      const gravity = Array.isArray(params.gravity)
        ? num(-params.gravity[1], 30)
        : num(params.gravity, 30);
      const restRho = num(params.rest_density ?? params.restDensity, 1.1);
      const stiffness = num(params.stiffness, 60);
      const viscosity = num(params.viscosity, 6);
      const step = Math.min(dt, 1 / 60);
      const near = neighbours();

      for (let i = 0; i < parts.length; i++) {
        const a = parts[i];
        let rho = 0;
        for (const j of near(i)) {
          const b = parts[j];
          const r2 = (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
          if (r2 < h2) rho += mass * poly6 * Math.pow(h2 - r2, 3);
        }
        a.rho = Math.max(rho, 1e-6);
        a.p = stiffness * Math.max(0, a.rho - restRho);
      }

      for (let i = 0; i < parts.length; i++) {
        const a = parts[i];
        let fx = 0; let fy = gravity * a.rho;   // gravity as body force
        for (const j of near(i)) {
          if (j === i) continue;
          const b = parts[j];
          const dx = b.x - a.x; const dy = b.y - a.y;
          const r = Math.hypot(dx, dy);
          if (r <= 0 || r >= h) continue;
          const common = mass * (a.p + b.p) / (2 * b.rho) * spikyGrad * Math.pow(h - r, 2);
          fx += common * (dx / r); fy += common * (dy / r);
          const lap = viscLap * (h - r) * viscosity * mass / b.rho;
          fx += lap * (b.vx - a.vx); fy += lap * (b.vy - a.vy);
        }
        a.vx += (fx / a.rho) * step; a.vy += (fy / a.rho) * step;
      }

      for (const a of parts) {
        a.x += a.vx * step; a.y += a.vy * step;
        if (a.x < 0.5) { a.x = 0.5; a.vx *= -0.3; }
        if (a.x > W - 0.5) { a.x = W - 0.5; a.vx *= -0.3; }
        if (a.y < 0.5) { a.y = 0.5; a.vy *= -0.3; }
        if (a.y > H - 0.5) { a.y = H - 0.5; a.vy *= -0.3; }
      }
    },
    output() {
      let rhoSum = 0;
      const positions = new Array(parts.length);
      for (let i = 0; i < parts.length; i++) {
        const a = parts[i];
        positions[i] = [a.x, a.y, Math.hypot(a.vx, a.vy)];
        rhoSum += a.rho;
      }
      return { bounds, positions, aggregates: { count: parts.length, avg_density: rhoSum / parts.length } };
    },
  };
}
