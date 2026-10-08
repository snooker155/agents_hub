/*
 * The three "rebuilds of the mark" the Orbit Loader artifact draws with
 * JavaScript rather than SMIL: the mark in three dimensions with a fourth
 * satellite, the same body flowing through solids, and the mark tilted into
 * a clock face. Each is a builder over a group centred on (0, 0) (clips.js
 * moves it to the mark's centre) that returns frame(t, dt), t being the
 * clip's own time in seconds.
 *
 * Two departures from the artifact, so a clip starts as the mark: the solid
 * starts turned so three satellites sit where the logo has them and the
 * fourth hides behind the core, and its tumble eases in over the first
 * second and a half instead of being under way.
 */

const NS = 'http://www.w3.org/2000/svg';
const S3 = 1 / Math.sqrt(3);
const PHI = (1 + Math.sqrt(5)) / 2;
const TET = [[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]].map((v) => v.map((c) => c * S3));

function mk(parent, tag, attrs) {
  const e = document.createElementNS(NS, tag);
  if (attrs) Object.keys(attrs).forEach((k) => e.setAttribute(k, attrs[k]));
  parent.appendChild(e);
  return e;
}
const r3 = (n) => Math.round(n * 1000) / 1000;
const clamp = (u) => (u < 0 ? 0 : u > 1 ? 1 : u);
const smooth = (u) => { const v = clamp(u); return v * v * (3 - 2 * v); };
const ease = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);

function mul(a, b) {
  const r = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < 3; i += 1) for (let j = 0; j < 3; j += 1) for (let k = 0; k < 3; k += 1) r[i][j] += a[i][k] * b[k][j];
  return r;
}
function rot(axis, ang) {
  const l = Math.hypot(axis[0], axis[1], axis[2]) || 1;
  const x = axis[0] / l; const y = axis[1] / l; const z = axis[2] / l;
  const c = Math.cos(ang); const s = Math.sin(ang); const t = 1 - c;
  return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y], [t * x * y + s * z, t * y * y + c, t * y * z - s * x], [t * x * z - s * y, t * y * z + s * x, t * z * z + c]];
}
function apply(m, v) {
  return [m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2], m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2], m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2]];
}

// The turn that puts the tetrahedron's vertices 0, 1, 2 where the logo has
// its satellites (up, lower right, lower left, a third of the way towards
// the viewer) and vertex 3 straight behind the core: M = 3/4 Σ d·uᵀ.
const START = (() => {
  const p = Math.sqrt(8 / 9);
  const dirs = [[0, -p, 1 / 3], [p * Math.cos(Math.PI / 6), p * Math.sin(Math.PI / 6), 1 / 3],
    [-p * Math.cos(Math.PI / 6), p * Math.sin(Math.PI / 6), 1 / 3], [0, 0, -1]];
  const unit = TET.map((v) => v.map((c) => c / Math.hypot(...v)));
  const m = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  dirs.forEach((d, n) => { for (let i = 0; i < 3; i += 1) for (let j = 0; j < 3; j += 1) m[i][j] += 0.75 * d[i] * unit[n][j]; });
  return m;
})();

// three slow sines per axis read as a random tumble but never jerk
function wander(t) {
  return [Math.sin(t * 0.37 + 1.0) + 0.5 * Math.sin(t * 0.91), Math.sin(t * 0.29 + 2.0) + 0.5 * Math.sin(t * 0.73 + 1),
    Math.sin(t * 0.23) + 0.5 * Math.sin(t * 0.61 + 2)];
}

/* The mark in three dimensions: the satellites at the vertices of a
   tetrahedron around the core; far ones smaller and paler. */
function build3d(g) {
  const R = 18; const cam = 90;
  const sats = TET.map((v) => v.map((c) => c * R));
  const lines = sats.map(() => mk(g, 'line', { class: 'v2-edge', 'stroke-opacity': 0.55 }));
  const core = mk(g, 'circle', { class: 'mark-core', r: 7 });
  const dots = sats.map(() => mk(g, 'circle', { class: 'mark-sat' }));
  let M = START;
  return {
    frame(t, dt) {
      const w = wander(t);
      M = mul(rot(w, Math.hypot(w[0], w[1], w[2]) * 1.1 * dt * smooth(t / 1.5)), M);
      const pts = sats.map((v) => apply(M, v));
      const order = pts.map((p, i) => ({ i, z: p[2] })).sort((a, b) => a.z - b.z);
      const place = (o) => {
        const p = pts[o.i]; const k = cam / (cam - p[2]);
        const x = p[0] * k; const y = p[1] * k;
        const rr = 4.5 * k; const gap = (7 - 1) / (18 * k); const tip = 1 - (rr - 1) / (18 * k);
        const d = dots[o.i]; const l = lines[o.i];
        d.setAttribute('cx', r3(x)); d.setAttribute('cy', r3(y)); d.setAttribute('r', r3(rr));
        d.setAttribute('fill-opacity', r3(0.75 * (0.7 + 0.3 * k)));
        l.setAttribute('x1', r3(x * gap)); l.setAttribute('y1', r3(y * gap));
        l.setAttribute('x2', r3(x * tip)); l.setAttribute('y2', r3(y * tip));
        l.setAttribute('stroke-opacity', r3(0.55 * (0.6 + 0.4 * k)));
        g.appendChild(l); g.appendChild(d);
      };
      // far things first: lines and dots behind the core, then the core, then the rest
      order.filter((o) => o.z < 0).forEach(place);
      g.appendChild(core);
      order.filter((o) => o.z >= 0).forEach(place);
    },
  };
}

/* The same tumbling mark flowing into solids and back: cube, octahedron,
   icosahedron, a tesseract as a cube in a cube. Points and edges are the
   same throughout; depth gives size and the order they are drawn in. */
function build3dmorph(g) {
  const sc = (pts, k) => pts.map((p) => [p[0] * k, p[1] * k, p[2] * k]);
  const cube = [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1], [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    .map((v) => [v[0] - 0.5, v[1] - 0.5, v[2] - 0.5].map((c) => c * 1.25));
  const cubeE = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
  const octa = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
  const octaE = [[0, 2], [2, 1], [1, 3], [3, 0], [4, 0], [4, 1], [4, 2], [4, 3], [5, 0], [5, 1], [5, 2], [5, 3]];
  const ico = [[0, 1, PHI], [0, -1, PHI], [0, 1, -PHI], [0, -1, -PHI], [1, PHI, 0], [-1, PHI, 0], [1, -PHI, 0], [-1, -PHI, 0],
    [PHI, 0, 1], [PHI, 0, -1], [-PHI, 0, 1], [-PHI, 0, -1]].map((v) => v.map((c) => c / PHI / 1.05));
  const icoE = [];
  for (let i = 0; i < 12; i += 1) {
    for (let j = i + 1; j < 12; j += 1) {
      const d = Math.hypot(ico[i][0] - ico[j][0], ico[i][1] - ico[j][1], ico[i][2] - ico[j][2]);
      if (Math.abs(d - 2 / PHI / 1.05) < 1e-6) icoE.push([i, j]);
    }
  }
  const tess = sc(cube, 1.15).concat(sc(cube, 0.5));
  const tessE = cubeE.concat(cubeE.map((e) => [e[0] + 8, e[1] + 8])).concat([0, 1, 2, 3, 4, 5, 6, 7].map((i) => [i, i + 8]));
  // core radius (0 = hidden), points, edges (indices into the points), point radius
  const shapes = [
    { core: 7, pts: TET, edges: [], spokes: true, r: 4.5 },
    { core: 0, pts: cube, edges: cubeE, r: 3.4 },
    { core: 0, pts: octa, edges: octaE, r: 3.8 },
    { core: 0, pts: ico, edges: icoE, r: 2.8 },
    { core: 0, pts: tess, edges: tessE, r: 2.4 },
  ];
  const MAXN = 16; const MAXE = 32; const R = 19; const cam = 95;
  const HOLD = 1.6; const MORPH = 1.1; const PERIOD = HOLD + MORPH;
  const lines = []; const dots = [];
  for (let k = 0; k < MAXE; k += 1) lines.push(mk(g, 'line', { class: 'v2-edge' }));
  const core = mk(g, 'circle', { class: 'mark-core' });
  for (let i = 0; i < MAXN; i += 1) dots.push(mk(g, 'circle', { class: 'mark-sat' }));
  let M = START;
  return {
    frame(t, dt) {
      const w = wander(t);
      M = mul(rot(w, Math.hypot(w[0], w[1], w[2]) * 0.9 * dt * smooth(t / 1.5)), M);
      // which shape, and how far into the morph to the next
      const total = PERIOD * shapes.length; const cyc = ((t % total) + total) % total;
      const idx = Math.floor(cyc / PERIOD); const phs = cyc - idx * PERIOD;
      const u = phs < HOLD ? 0 : ease((phs - HOLD) / MORPH);
      const A = shapes[idx]; const B = shapes[(idx + 1) % shapes.length];
      const lerp = (a, b) => a + (b - a) * u;
      const pts = []; const rad = [];
      for (let i = 0; i < MAXN; i += 1) {
        const pa = A.pts[i] || [0, 0, 0]; const pb = B.pts[i] || [0, 0, 0];
        pts.push([lerp(pa[0], pb[0]) * R, lerp(pa[1], pb[1]) * R, lerp(pa[2], pb[2]) * R]);
        rad.push(lerp(A.pts[i] ? A.r : 0, B.pts[i] ? B.r : 0));
      }
      const coreR = lerp(A.core, B.core);
      const P = pts.map((v) => apply(M, v));
      const pr = P.map((p) => { const kk = cam / (cam - p[2]); return { x: p[0] * kk, y: p[1] * kk, k: kk, z: p[2] }; });
      // endpoints of an edge: ['c', i] is the core to point i, [i, j] two points
      const ep = (e) => {
        if (!e) return null;
        if (e[0] === 'c') return { x1: 0, y1: 0, x2: pr[e[1]].x, y2: pr[e[1]].y, z: pr[e[1]].z / 2 };
        return { x1: pr[e[0]].x, y1: pr[e[0]].y, x2: pr[e[1]].x, y2: pr[e[1]].y, z: (pr[e[0]].z + pr[e[1]].z) / 2 };
      };
      const items = [];
      for (let i = 0; i < MAXN; i += 1) items.push({ kind: 'dot', i, z: P[i][2] });
      items.push({ kind: 'core', z: 0 });
      for (let k = 0; k < MAXE; k += 1) {
        // edge k of A turns into edge k of B; a missing side collapses to a point
        const ea = A.spokes ? (k < 4 ? ['c', k] : null) : A.edges[k];
        const eb = B.spokes ? (k < 4 ? ['c', k] : null) : B.edges[k];
        let A1 = ep(ea); let B1 = ep(eb);
        if (!A1 && !B1) { lines[k].setAttribute('stroke-opacity', 0); continue; }
        let o = 0.55;
        if (!A1) { A1 = { x1: B1.x2, y1: B1.y2, x2: B1.x2, y2: B1.y2, z: B1.z }; o = 0.55 * u; }
        if (!B1) { B1 = { x1: A1.x2, y1: A1.y2, x2: A1.x2, y2: A1.y2, z: A1.z }; o = 0.55 * (1 - u); }
        let x1 = lerp(A1.x1, B1.x1); let y1 = lerp(A1.y1, B1.y1);
        const x2 = lerp(A1.x2, B1.x2); const y2 = lerp(A1.y2, B1.y2);
        // keep the gap from the core when the edge starts there
        if ((ea && ea[0] === 'c') || (eb && eb[0] === 'c')) {
          const L = Math.hypot(x2 - x1, y2 - y1) || 1; const gap = Math.max(0, coreR - 1) / L;
          x1 += (x2 - x1) * gap; y1 += (y2 - y1) * gap;
        }
        items.push({ kind: 'line', k, z: lerp(A1.z, B1.z), x1, y1, x2, y2, o });
      }
      items.sort((a, b) => a.z - b.z);
      items.forEach((it) => {
        if (it.kind === 'dot') {
          const d = dots[it.i]; const q = pr[it.i];
          d.setAttribute('cx', r3(q.x)); d.setAttribute('cy', r3(q.y)); d.setAttribute('r', r3(rad[it.i] * q.k));
          d.setAttribute('fill-opacity', r3(0.75 * (0.7 + 0.3 * q.k)));
          g.appendChild(d);
        } else if (it.kind === 'core') {
          core.setAttribute('r', r3(coreR));
          g.appendChild(core);
        } else {
          const l = lines[it.k];
          l.setAttribute('x1', r3(it.x1)); l.setAttribute('y1', r3(it.y1));
          l.setAttribute('x2', r3(it.x2)); l.setAttribute('y2', r3(it.y2));
          l.setAttribute('stroke-opacity', r3(it.o));
          g.appendChild(l);
        }
      });
    },
  };
}

/* The clock in space: the mark tilts into a dial, a rim appears, the marks
   move out to 12, 4 and 8 and the spokes run round as hands while the dial
   sways; then it lies back down into the flat mark. */
function build3dclock(g) {
  const A3 = [-90, 30, 150]; const HANDS = [[19.5, 2.2, 3], [15, 2.8, 1], [11, 3.6, 1]];   // length, width, turns
  const T = 10.0; const T_IN = 0.6; const T_TILT = 1.0; const T_RUN = 6.0; const T_OUT = 1.0;
  const cam = 90;
  const ring = mk(g, 'path', { class: 'v2-dial', fill: 'none' });
  const hands = HANDS.map(() => mk(g, 'line', { class: 'v2-edge v2-hand', 'stroke-opacity': 0.55 }));
  const core = mk(g, 'circle', { class: 'mark-core', r: 7 });
  const marks = A3.map(() => mk(g, 'circle', { class: 'mark-sat', 'fill-opacity': 0.75 }));
  const rotXY = (p, pitch, yaw) => {
    const [x, y, z] = p;
    const x1 = x * Math.cos(yaw) + z * Math.sin(yaw); const z1 = -x * Math.sin(yaw) + z * Math.cos(yaw);
    return [x1, y * Math.cos(pitch) - z1 * Math.sin(pitch), y * Math.sin(pitch) + z1 * Math.cos(pitch)];
  };
  const proj = (p) => { const k = cam / (cam - p[2]); return { x: p[0] * k, y: p[1] * k, k, z: p[2] }; };
  return {
    frame(time) {
      const t = ((time % T) + T) % T;
      let tilt; let run;
      if (t < T_IN) { tilt = 0; run = 0; } else if (t < T_IN + T_TILT) { tilt = smooth((t - T_IN) / T_TILT); run = 0; } else if (t < T_IN + T_TILT + T_RUN) { tilt = 1; run = smooth((t - T_IN - T_TILT) / T_RUN); } else if (t < T_IN + T_TILT + T_RUN + T_OUT) { tilt = 1 - smooth((t - T_IN - T_TILT - T_RUN) / T_OUT); run = 1; } else { tilt = 0; run = 1; }
      const pitch = tilt * (0.95 + 0.12 * Math.sin(time * 0.7)); const yaw = tilt * 0.45 * Math.sin(time * 0.5);
      const markR = 18 + 4.5 * tilt; const markSize = 4.5 - 1.1 * tilt;
      if (tilt > 0.02) {
        let d = '';
        for (let i = 0; i <= 48; i += 1) {
          const a = (i / 48) * Math.PI * 2; const q = proj(rotXY([Math.cos(a) * 22, Math.sin(a) * 22, 0], pitch, yaw));
          d += `${i ? 'L' : 'M'}${q.x.toFixed(2)} ${q.y.toFixed(2)}`;
        }
        ring.setAttribute('d', `${d}Z`); ring.setAttribute('stroke-opacity', r3(tilt));
      } else ring.setAttribute('stroke-opacity', 0);
      const items = [];
      HANDS.forEach((h, i) => {
        const L = 16.5 + (h[0] - 16.5) * tilt; const w = 2.5 + (h[1] - 2.5) * tilt;
        const ang = ((A3[i] + h[2] * 360 * run) * Math.PI) / 180;
        const p0 = proj(rotXY([Math.cos(ang) * 6.5 * (1 - tilt), Math.sin(ang) * 6.5 * (1 - tilt), 0], pitch, yaw));
        const p1 = proj(rotXY([Math.cos(ang) * L, Math.sin(ang) * L, 0], pitch, yaw));
        items.push({ kind: 'hand', i, z: (p0.z + p1.z) / 2, x1: p0.x, y1: p0.y, x2: p1.x, y2: p1.y, w });
      });
      A3.forEach((a, i) => {
        const r = (a * Math.PI) / 180; const q = proj(rotXY([Math.cos(r) * markR, Math.sin(r) * markR, 0], pitch, yaw));
        items.push({ kind: 'mark', i, z: q.z, x: q.x, y: q.y, r: markSize * q.k });
      });
      items.push({ kind: 'core', z: 0 });
      items.sort((a, b) => a.z - b.z);
      items.forEach((it) => {
        if (it.kind === 'hand') {
          const l = hands[it.i];
          l.setAttribute('x1', r3(it.x1)); l.setAttribute('y1', r3(it.y1)); l.setAttribute('x2', r3(it.x2)); l.setAttribute('y2', r3(it.y2));
          l.setAttribute('stroke-width', r3(it.w));
          g.appendChild(l);
        } else if (it.kind === 'mark') {
          const m = marks[it.i];
          m.setAttribute('cx', r3(it.x)); m.setAttribute('cy', r3(it.y)); m.setAttribute('r', r3(it.r));
          g.appendChild(m);
        } else g.appendChild(core);
      });
    },
  };
}

/** name → { build, size (the artifact's view box, centred on the mark), dur (null: no cycle to finish) } */
export const JS_CLIPS = {
  'mark-3d': { build: build3d, size: 60, dur: null, cls: 'v2 v2--3d' },
  'mark-3dmorph': { build: build3dmorph, size: 68, dur: null, cls: 'v2 v2--3dmorph' },
  'mark-3dclock': { build: build3dclock, size: 60, dur: 10, cls: 'v2 v2--3dclock' },
};
