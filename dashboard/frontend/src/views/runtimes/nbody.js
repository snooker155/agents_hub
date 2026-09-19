// N-body runtime (client tier) — 2D softened gravity around a heavy centre,
// leapfrog-integrated: the real-time *approximation* of scenario 1. The precise
// counterpart is the server `nbody` (view_compute); this one is for orbits you
// can watch and perturb live. Pure module: create(spec) → { step, output }.

function rnd(a, b) { return a + Math.random() * (b - a); }

export function create(spec) {
  const bounds = spec?.bounds || [100, 100];
  const p0 = spec?.params || {};
  const [W, H] = bounds;
  const cx = W / 2; const cy = H / 2;
  const count = Math.min(600, Math.max(2, p0.count ?? 120));
  const scale = Math.min(W, H) * 0.42;

  // a disc of bodies on circular-ish orbits around a heavy centre
  const bodies = [{ x: cx, y: cy, vx: 0, vy: 0, m: p0.central_mass ?? 300 }];
  for (let i = 1; i < count; i++) {
    const r = Math.sqrt(rnd(0.05, 1)) * scale;
    const a = rnd(0, Math.PI * 2);
    const v = Math.sqrt((p0.g ?? 40) * bodies[0].m / Math.max(r, 1));
    bodies.push({
      x: cx + r * Math.cos(a), y: cy + r * Math.sin(a),
      vx: -v * Math.sin(a), vy: v * Math.cos(a), m: rnd(0.5, 1.5),
    });
  }

  function accel(out, g, soft2) {
    for (let i = 0; i < bodies.length; i++) { out[i * 2] = 0; out[i * 2 + 1] = 0; }
    for (let i = 0; i < bodies.length; i++) {
      for (let j = i + 1; j < bodies.length; j++) {
        const dx = bodies[j].x - bodies[i].x; const dy = bodies[j].y - bodies[i].y;
        const inv = Math.pow(dx * dx + dy * dy + soft2, -1.5) * g;
        out[i * 2] += inv * dx * bodies[j].m; out[i * 2 + 1] += inv * dy * bodies[j].m;
        out[j * 2] -= inv * dx * bodies[i].m; out[j * 2 + 1] -= inv * dy * bodies[i].m;
      }
    }
  }

  const acc = new Float64Array(count * 2);

  return {
    step(dt, params = {}) {
      const g = params.g ?? 40;
      const soft = params.softening ?? 2;
      const h = Math.min(dt, 1 / 30);
      accel(acc, g, soft * soft);
      for (let i = 0; i < bodies.length; i++) {
        const b = bodies[i];
        b.vx += acc[i * 2] * h * 0.5; b.vy += acc[i * 2 + 1] * h * 0.5;
        b.x += b.vx * h; b.y += b.vy * h;
      }
      accel(acc, g, soft * soft);
      for (let i = 0; i < bodies.length; i++) {
        bodies[i].vx += acc[i * 2] * h * 0.5; bodies[i].vy += acc[i * 2 + 1] * h * 0.5;
      }
    },
    output() {
      const positions = bodies.map((b) => [b.x, b.y, Math.hypot(b.vx, b.vy)]);
      let ke = 0;
      for (const b of bodies) ke += 0.5 * b.m * (b.vx * b.vx + b.vy * b.vy);
      return { bounds, positions, aggregates: { bodies: bodies.length, kinetic: ke } };
    },
  };
}
