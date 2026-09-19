// Boids (flocking) runtime — separation, alignment, cohesion in a wrapped 2D
// world. Models multi-agent behaviour (crowds, swarms, traffic-like flows).
// Pure module: create(spec) → { step(dt, params), output() }.

function rnd(a, b) { return a + Math.random() * (b - a); }

export function create(spec) {
  const bounds = spec?.bounds || [100, 100];
  const p0 = spec?.params || {};
  const [W, H] = bounds;
  const count = Math.min(1000, Math.max(1, p0.count ?? 120));
  const boids = Array.from({ length: count }, () => {
    const a = rnd(0, Math.PI * 2);
    return { x: rnd(0, W), y: rnd(0, H), vx: Math.cos(a) * 10, vy: Math.sin(a) * 10 };
  });

  return {
    step(dt, params = {}) {
      const perception = params.perception ?? 12;
      const sep = params.separation ?? 1.2;
      const ali = params.alignment ?? 0.6;
      const coh = params.cohesion ?? 0.5;
      const maxSpeed = params.speed ?? 18;
      const p2 = perception * perception;
      for (const b of boids) {
        let cx = 0, cy = 0, ax = 0, ay = 0, sx = 0, sy = 0, n = 0;
        for (const o of boids) {
          if (o === b) continue;
          const dx = o.x - b.x, dy = o.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 > 0 && d2 < p2) {
            cx += o.x; cy += o.y; ax += o.vx; ay += o.vy;
            sx -= dx / d2; sy -= dy / d2; n++;
          }
        }
        if (n > 0) {
          b.vx += ((cx / n - b.x) * coh + (ax / n) * ali + sx * sep * 20) * dt;
          b.vy += ((cy / n - b.y) * coh + (ay / n) * ali + sy * sep * 20) * dt;
        }
        const sp = Math.hypot(b.vx, b.vy) || 1;
        b.vx = (b.vx / sp) * maxSpeed; b.vy = (b.vy / sp) * maxSpeed;
        b.x = (b.x + b.vx * dt + W) % W; b.y = (b.y + b.vy * dt + H) % H;
      }
    },
    output() {
      const positions = boids.map((b) => [b.x, b.y, Math.atan2(b.vy, b.vx)]);
      // mean heading alignment as an order parameter (0..1)
      let sx = 0, sy = 0;
      for (const b of boids) { const s = Math.hypot(b.vx, b.vy) || 1; sx += b.vx / s; sy += b.vy / s; }
      return { bounds, positions, heading: true, aggregates: { count: boids.length, order: Math.hypot(sx, sy) / boids.length } };
    },
  };
}
