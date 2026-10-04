/*
 * The poses of the live mark: one pure function per state, from the time the
 * state has been on show (seconds) to where every part of the mark is.
 *
 * The mark is public/logo.svg: a core at (24, 24) r 7, three satellites r 4.5
 * about 18 out at -90°, 32° and 148°, and a spoke from the core to each.
 * A pose places the core in x/y and every satellite in polar coordinates
 * around the centre (24, 24), so a blend between two poses moves a satellite
 * along an arc rather than through the core. `spokes` is one opacity per
 * satellite; `lines` the spokes' ends, which a pose may give itself (the logo
 * does: its lower spokes are not quite on the line to their satellites) and
 * otherwise run from just outside the core to the satellite's edge.
 *
 * LiveMark.jsx blends the outgoing pose into the incoming one while both keep
 * moving, which is what makes a switch a movement instead of a cut. Searching
 * and making things are scenes (scenes/), not poses; scenes start and end as
 * `idle`, drawn to the same coordinates, so the hand-over does not move a
 * thing.
 */

export const CX = 24;
export const CY = 24;
const BASE = [-90, 30, 150];
const TAU = Math.PI * 2;

const rad = (deg) => (deg * Math.PI) / 180;
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const easeInOut = (v) => (v < 0.5 ? 4 * v * v * v : 1 - (-2 * v + 2) ** 3 / 2);
const frac = (v) => v - Math.floor(v);

/** A satellite from x/y, for poses that are easier to think of on a grid. */
function at(x, y, r, o) {
  const dx = x - CX;
  const dy = y - CY;
  return { a: Math.atan2(dy, dx), d: Math.hypot(dx, dy), r, o };
}

function pose({ core, sats, spokes = [0, 0, 0], lines = null }) {
  return { core, sats, spokes, lines };
}

// public/logo.svg, to the decimal.
export const LOGO_SATS = [[24, 6], [40, 34], [8, 34]];
export const LOGO_SPOKES = [[24, 16.5, 24, 10.5], [29.6, 28.2, 35.6, 31.6], [18.4, 28.2, 12.4, 31.6]];

/** The satellite's centre in x/y. */
export function satXY(s) {
  return { x: CX + s.d * Math.cos(s.a), y: CY + s.d * Math.sin(s.a) };
}

/** The spokes' ends: the pose's own, or from just outside the core to each satellite's edge. */
export function spokeLines(p) {
  if (p.lines) return p.lines;
  return p.sats.map((s) => {
    const q = satXY(s);
    const dx = q.x - p.core.x;
    const dy = q.y - p.core.y;
    const len = Math.hypot(dx, dy) || 1;
    const start = p.core.r + 0.5;
    const end = Math.max(start, len - s.r);
    return [p.core.x + (dx / len) * start, p.core.y + (dy / len) * start,
      p.core.x + (dx / len) * end, p.core.y + (dy / len) * end];
  });
}

// The logo as it is drawn everywhere else: its satellites sit at (24, 6),
// (40, 34) and (8, 34), so the lower two are a little further out (18.9)
// than the top one, at 32° rather than 30°, and its spokes are its own.
function idle() {
  return pose({
    core: { x: CX, y: CY, r: 7, o: 1 },
    sats: LOGO_SATS.map(([x, y]) => at(x, y, 4.5, 0.75)),
    spokes: [0.55, 0.55, 0.55],
    lines: LOGO_SPOKES,
  });
}

// The page loader's orbits: three radii, the outer one turning the other way.
function working(t) {
  const turn = (t / 2.8) * TAU;
  return pose({
    core: { x: CX, y: CY, r: 5.4 + 0.5 * Math.sin((t / 1.4) * TAU), o: 0.95 },
    sats: [
      { a: rad(BASE[0]) - turn, d: 20.5, r: 4.2, o: 0.95 },
      { a: rad(BASE[1]) + turn, d: 14.5, r: 3.6, o: 0.8 },
      { a: rad(BASE[2]) + turn * 1.4, d: 9.8, r: 2.8, o: 0.7 },
    ],
  });
}

// Neurons: the satellites close in round a breathing core and the spokes
// fire one after another.
function think(t) {
  const turn = (t / 4) * TAU;
  return pose({
    core: { x: CX, y: CY, r: 6.6 + 0.8 * Math.sin((t / 1.6) * TAU), o: 1 },
    sats: BASE.map((deg, i) => {
      const fire = Math.max(0, Math.sin((t / 1.2) * TAU - i * 2.1)) ** 2;
      return {
        a: rad(deg) + turn,
        d: 15 + 1.6 * Math.sin((t / 1.1) * TAU + i * 2.1),
        r: 3.3 + 0.7 * fire,
        o: 0.45 + 0.55 * fire,
      };
    }),
    spokes: BASE.map((_, i) => 0.7 * Math.max(0, Math.sin((t / 1.2) * TAU - i * 2.1)) ** 2),
  });
}

// A line of text under an eye: a highlight runs along the three dots left to
// right, then a quick carriage return; the core follows it.
function read(t) {
  const p = frac(t / 1.6);
  const hx = p < 0.8
    ? 8 + 32 * easeInOut(p / 0.8)
    : 40 - 32 * easeInOut((p - 0.8) / 0.2);
  const xs = [13, 24, 35];
  return pose({
    core: { x: CX + (hx - CX) * 0.3, y: 17, r: 5.6, o: 1 },
    sats: xs.map((x) => {
      const g = Math.exp(-(((x - hx) / 6) ** 2));
      return at(x, 34, 2.9 + 0.9 * g, 0.35 + 0.65 * g);
    }),
  });
}

// A machine: the three satellites hold a triangle on its spokes and turn it
// a third of a circle at a time, the core ticking with every step. One
// satellite leads (larger, solid); without it a third of a turn would leave
// the triangle looking exactly as it was.
function code(t) {
  const STEP = 0.7;
  const n = Math.floor(t / STEP);
  const move = easeInOut(clamp01(frac(t / STEP) / 0.5));
  const turn = rad(120) * (n + move);
  return pose({
    core: { x: CX, y: CY, r: 6.2 + 0.7 * (1 - move), o: 1 },
    sats: BASE.map((deg, i) => ({ a: rad(deg) + turn, d: 15.5, r: i ? 3.3 : 4.4, o: i ? 0.55 : 1 })),
    spokes: [0.7, 0.4, 0.4],
  });
}

// A voice: the core moves left and the satellites leave it to the right one
// after another, fading as they go.
function speak(t) {
  const core = { x: 15, y: CY, r: 6 + 0.5 * Math.abs(Math.sin((t / 0.9) * TAU)), o: 1 };
  return pose({
    core,
    sats: [0, 1, 2].map((i) => {
      const p = frac(t / 1.5 + i / 3);
      const x = core.x + 9 + 20 * p;
      const y = CY + 2.2 * Math.sin(p * TAU);
      return at(x, y, 3.6 - 1.4 * p, 0.95 * Math.sin(Math.PI * p) ** 0.7);
    }),
  });
}

// Listening: the mirror of speaking. The core moves right and the satellites
// come in to it from the left one after another, growing as they arrive.
function listen(t) {
  const core = { x: 33, y: CY, r: 6 + 0.6 * Math.abs(Math.sin((t / 1.1) * TAU)), o: 1 };
  return pose({
    core,
    sats: [0, 1, 2].map((i) => {
      const p = frac(t / 1.6 + i / 3);
      const x = core.x - 9 - 20 * (1 - p);
      const y = CY + 2.2 * Math.sin(p * TAU);
      return at(x, y, 2.2 + 1.4 * p, 0.95 * Math.sin(Math.PI * p) ** 0.7);
    }),
  });
}

// Waiting for a person: the mark, slowed right down and breathing.
function wait(t) {
  const breathe = 0.5 + 0.5 * Math.sin((t / 2.4) * TAU);
  const turn = (t / 20) * TAU;
  return pose({
    core: { x: CX, y: CY, r: 6.4 + 0.4 * breathe, o: 0.85 },
    sats: BASE.map((deg) => ({ a: rad(deg) + turn, d: 18, r: 4, o: 0.35 + 0.35 * breathe })),
    spokes: [0.3, 0.3, 0.3].map((s) => s * (0.5 + 0.5 * breathe)),
  });
}

export const POSES = { idle, working, think, read, code, speak, listen, wait };
export const STATES = Object.keys(POSES);
/** States whose pose does not change with time: the loop can stop on them. */
export const STATIC_STATES = new Set(['idle']);

export function poseFor(state, t) {
  return (POSES[state] || POSES.working)(t);
}

const lerp = (a, b, w) => a + (b - a) * w;
function lerpAngle(a, b, w) {
  const diff = ((b - a) % TAU + TAU * 1.5) % TAU - Math.PI;
  return a + diff * w;
}

/** Pose `a` at w = 0, pose `b` at w = 1. */
export function mixPose(a, b, w) {
  if (w <= 0) return a;
  if (w >= 1) return b;
  return {
    core: {
      x: lerp(a.core.x, b.core.x, w),
      y: lerp(a.core.y, b.core.y, w),
      r: lerp(a.core.r, b.core.r, w),
      o: lerp(a.core.o, b.core.o, w),
    },
    sats: a.sats.map((s, i) => {
      const e = b.sats[i];
      return {
        a: lerpAngle(s.a, e.a, w),
        d: lerp(s.d, e.d, w),
        r: lerp(s.r, e.r, w),
        o: lerp(s.o, e.o, w),
      };
    }),
    spokes: a.spokes.map((s, i) => lerp(s, b.spokes[i], w)),
    lines: a.lines || b.lines
      ? spokeLines(a).map((la, i) => la.map((v, j) => lerp(v, spokeLines(b)[i][j], w)))
      : null,
  };
}

export { easeInOut };
