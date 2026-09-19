// Process token-flow runtime — animates execution of a process graph. Tokens
// spawn at start nodes and travel along edges; reaching a node they fan out to
// its outgoing edges. Drives scenario 7 (process execution). Pure module:
// create(spec) → { step(dt, params), output() }.

function asMap(coll) {
  if (Array.isArray(coll)) {
    const m = {};
    coll.forEach((el, i) => { m[el?.id ?? String(i)] = el; });
    return m;
  }
  return coll && typeof coll === 'object' ? coll : {};
}

export function create(spec) {
  const nodes = asMap(spec?.nodes);
  const edges = asMap(spec?.edges);
  const outFrom = {};
  Object.entries(edges).forEach(([eid, e]) => {
    (outFrom[e.source] = outFrom[e.source] || []).push({ eid, target: e.target });
  });
  const starts = Object.keys(nodes).filter(
    (id) => nodes[id]?.type === 'start' || !Object.values(edges).some((e) => e.target === id),
  );
  let tokens = [];
  let spawnTimer = 0;

  function spawn() {
    starts.forEach((s) => {
      (outFrom[s] || []).forEach((o) => tokens.push({ eid: o.eid, from: s, to: o.target, p: 0 }));
      if (!(outFrom[s] || []).length) tokens.push({ eid: null, from: s, to: s, p: 1 });
    });
  }

  return {
    step(dt, params = {}) {
      const speed = params.speed ?? 0.6;
      const rate = params.spawn_interval ?? 2.5;
      spawnTimer -= dt;
      if (tokens.length === 0 || spawnTimer <= 0) { spawn(); spawnTimer = rate; }
      const next = [];
      for (const t of tokens) {
        t.p += speed * dt;
        if (t.p < 1) { next.push(t); continue; }
        const outs = outFrom[t.to] || [];
        outs.forEach((o) => next.push({ eid: o.eid, from: t.to, to: o.target, p: 0 }));
        // tokens reaching an end node (no outgoing) simply disappear
      }
      tokens = next.slice(0, 500);
    },
    output() {
      const active = {};
      tokens.forEach((t) => { active[t.to] = true; if (t.p < 0.5) active[t.from] = true; });
      return {
        tokens: tokens.map((t) => ({ edge: t.eid, from: t.from, to: t.to, p: t.p })),
        activeNodes: Object.keys(active),
        aggregates: { in_flight: tokens.length },
      };
    },
  };
}
