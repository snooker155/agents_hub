// Particle-system runtime — a 2D box of particles under gravity + damping with
// wall bounce. Pure module: create(spec) → { step(dt, params), output() }. The
// agent parameterizes it (count, gravity, damping, bounds); it ships no code.

function rnd(a, b) { return a + Math.random() * (b - a); }

export function create(spec) {
  const bounds = spec?.bounds || [100, 100];
  const p0 = spec?.params || {};
  const count = Math.min(2000, Math.max(1, p0.count ?? 200));
  const [W, H] = bounds;
  const parts = Array.from({ length: count }, () => ({
    x: rnd(0, W), y: rnd(0, H), vx: rnd(-10, 10), vy: rnd(-10, 10),
  }));

  return {
    step(dt, params = {}) {
      const g = params.gravity ?? 20;
      const damping = params.damping ?? 0.999;
      const wind = params.wind ?? 0;
      for (const p of parts) {
        p.vy += g * dt;
        p.vx += wind * dt;
        p.vx *= damping; p.vy *= damping;
        p.x += p.vx * dt; p.y += p.vy * dt;
        if (p.x < 0) { p.x = 0; p.vx = -p.vx * 0.8; }
        if (p.x > W) { p.x = W; p.vx = -p.vx * 0.8; }
        if (p.y < 0) { p.y = 0; p.vy = -p.vy * 0.8; }
        if (p.y > H) { p.y = H; p.vy = -p.vy * 0.8; }
      }
    },
    output() {
      const positions = new Array(parts.length);
      let speedSum = 0;
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        positions[i] = [p.x, p.y, Math.hypot(p.vx, p.vy)];
        speedSum += Math.hypot(p.vx, p.vy);
      }
      return { bounds, positions, aggregates: { count: parts.length, avg_speed: speedSum / parts.length } };
    },
  };
}
