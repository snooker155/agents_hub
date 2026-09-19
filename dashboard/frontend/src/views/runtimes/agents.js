// Agents runtime (client tier) — structured goal-seeking entities: each agent
// steers toward a goal, separates from neighbours and avoids obstacles, and on
// arrival scores a throughput point and picks the next goal. Scenario 6's step
// up from boids (traffic/crowd-like flows); obstacles and goals come from
// spec.entities so the agent builds the environment, drop-an-obstacle included.

function rnd(a, b) { return a + Math.random() * (b - a); }

export function create(spec) {
  const bounds = spec?.bounds || [100, 100];
  const p0 = spec?.params || {};
  const ent = spec?.entities || {};
  const [W, H] = bounds;
  const count = Math.min(800, Math.max(1, p0.count ?? ent.count ?? 100));

  // environment: obstacles [{x,y,r}] and named goals [{x,y}] (defaults: corners)
  const obstacles = (Array.isArray(ent.obstacles) ? ent.obstacles : [])
    .map((o) => ({ x: o.x ?? W / 2, y: o.y ?? H / 2, r: o.r ?? 6 }));
  const goals = (Array.isArray(ent.goals) && ent.goals.length ? ent.goals
    : [{ x: W * 0.08, y: H * 0.08 }, { x: W * 0.92, y: H * 0.92 },
       { x: W * 0.92, y: H * 0.08 }, { x: W * 0.08, y: H * 0.92 }])
    .map((g) => ({ x: g.x ?? W / 2, y: g.y ?? H / 2 }));

  const agents = Array.from({ length: count }, () => ({
    x: rnd(2, W - 2), y: rnd(2, H - 2), vx: 0, vy: 0,
    goal: Math.floor(rnd(0, goals.length)),
  }));
  let arrivals = 0;
  let t = 0;

  return {
    step(dt, params = {}) {
      const speed = params.speed ?? 14;
      const separation = params.separation ?? 1.0;
      const avoidance = params.avoidance ?? 2.0;
      const arriveR = params.arrive_radius ?? 3;
      const h = Math.min(dt, 1 / 30);
      t += h;
      for (const a of agents) {
        const g = goals[a.goal % goals.length];
        let dx = g.x - a.x; let dy = g.y - a.y;
        const dg = Math.hypot(dx, dy) || 1;
        if (dg < arriveR) {                       // arrived → score + next goal
          arrivals++;
          a.goal = (a.goal + 1 + Math.floor(rnd(0, goals.length - 1))) % goals.length;
          continue;
        }
        let fx = (dx / dg) * speed; let fy = (dy / dg) * speed;

        for (const o of agents) {                 // separation from neighbours
          if (o === a) continue;
          const ox = a.x - o.x; const oy = a.y - o.y;
          const d2 = ox * ox + oy * oy;
          if (d2 > 0 && d2 < 16) { fx += (ox / d2) * separation * 8; fy += (oy / d2) * separation * 8; }
        }
        for (const ob of obstacles) {             // steer around obstacles
          const ox = a.x - ob.x; const oy = a.y - ob.y;
          const d = Math.hypot(ox, oy);
          const margin = ob.r + 4;
          if (d > 0 && d < margin) {
            const push = (margin - d) / margin * avoidance * speed;
            fx += (ox / d) * push; fy += (oy / d) * push;
          }
        }
        a.vx += (fx - a.vx) * Math.min(1, 4 * h);   // smooth steering
        a.vy += (fy - a.vy) * Math.min(1, 4 * h);
        const sp = Math.hypot(a.vx, a.vy);
        if (sp > speed) { a.vx = (a.vx / sp) * speed; a.vy = (a.vy / sp) * speed; }
        a.x = Math.max(0.5, Math.min(W - 0.5, a.x + a.vx * h));
        a.y = Math.max(0.5, Math.min(H - 0.5, a.y + a.vy * h));
        for (const ob of obstacles) {              // hard keep-out
          const ox = a.x - ob.x; const oy = a.y - ob.y;
          const d = Math.hypot(ox, oy);
          if (d < ob.r && d > 0) { a.x = ob.x + (ox / d) * ob.r; a.y = ob.y + (oy / d) * ob.r; }
        }
      }
    },
    output() {
      const positions = agents.map((a) => [a.x, a.y, Math.atan2(a.vy, a.vx)]);
      let sp = 0;
      for (const a of agents) sp += Math.hypot(a.vx, a.vy);
      return {
        bounds, positions, heading: true,
        obstacles: obstacles.map((o) => [o.x, o.y, o.r]),
        goals: goals.map((g) => [g.x, g.y]),
        aggregates: {
          count: agents.length,
          throughput: t > 0 ? +(arrivals / t).toFixed(2) : 0,
          avg_speed: sp / agents.length,
        },
      };
    },
  };
}
