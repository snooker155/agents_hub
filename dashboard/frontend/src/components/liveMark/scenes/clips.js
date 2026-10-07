import { poseFor, satXY, spokeLines } from '../poses';
import { JS_CLIPS } from './clips3d';

/*
 * The rest of the "Orbit Loader" artifact: its rebuilds of the mark (the
 * mark as a tetrahedron, gears, a clock, liquid, an hourglass, a growing
 * graph and the rest) and its hub relay stories (a request and its answer,
 * a task handed round, a streamed answer, a pipeline, a build and launch).
 *
 * The artifact draws most of them in SMIL; they are kept as they are, one
 * file each in clips/, loaded the first time they are wanted. Their clock is
 * the mark's: the clip's SVG timeline is paused and set to the clip's time on
 * every frame, so a clip starts at its beginning whenever it is mounted, runs
 * at the mark's speed (slower under reduced motion) and can be held or
 * finished. Three are JavaScript (clips3d.js) and draw on the same clock.
 *
 * Every clip is centred on the mark's core, at the mark's own scale, and cut
 * at the artifact's framing of it. The framing is wider than the mark's box
 * in most clips; the clip tells LiveMark how far it has to step back to
 * show it (`fit`), and LiveMark zooms the whole mark, not the clip, so one
 * clip after another does not grow the mark and shrink it again. A clip
 * starts as a copy of the mark exactly where LiveMark leaves it,
 * cross-fades into its first frame (the mark again, in most of them), plays
 * round and round for as long as its state lasts, and when the state ends
 * either plays out the round if little of it is left, or fades back into
 * the mark; both go faster once the state has ended.
 *
 * In several clips the core shrinks while its spokes stay where they were
 * drawn for the full-size core, which leaves a gap between them; the spokes
 * are redrawn each frame to start inside the core it has now (spokeFixer).
 * The relay stories drawn as the artifact's morphing mark (flow, lead, team,
 * deploy) have their spokes 3 units further out than the logo's, so they
 * never reach the core; they are moved back in when the clip is loaded
 * (reachCore).
 */

const NS = 'http://www.w3.org/2000/svg';
const C = 24;
const BOX = 48;
const INTRO = 0.45;   // seconds: cross-fade from the mark
const FOLD = 0.45;    // seconds: cross-fade back into the mark
const TAIL = 0.9;     // a round with at most this much left is played out
const LEAVE = 1.8;    // how much faster the clip goes once its state has ended
const CORE_R = 7;     // the core the artifact drew the spokes for

const FILES = import.meta.glob('./clips/*.svg', { query: '?raw', import: 'default' });
const fileOf = (name) => FILES[`./clips/${name}.svg`];

const fileNames = Object.keys(FILES).map((k) => k.replace(/^\.\/clips\/|\.svg$/g, ''));
/** The hub relay stories: the mark stays a hub and its agents. */
export const RELAY_STATES = fileNames.filter((n) => n.startsWith('relay-')).sort();
/** The rebuilds of the mark. */
export const REBUILD_STATES = [...new Set([...fileNames.filter((n) => n.startsWith('mark-')), ...Object.keys(JS_CLIPS)])].sort();
export const CLIP_STATES = [...RELAY_STATES, ...REBUILD_STATES];

export function isClip(state) {
  return Boolean(fileOf(state) || JS_CLIPS[state]);
}

const clamp = (u) => (u < 0 ? 0 : u > 1 ? 1 : u);
const ease = (u) => { const v = clamp(u); return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2; };
const lerp = (a, b, u) => a + (b - a) * u;
const r3 = (n) => Math.round(n * 1000) / 1000;

function mk(parent, tag, attrs) {
  const e = document.createElementNS(NS, tag);
  if (attrs) Object.keys(attrs).forEach((k) => e.setAttribute(k, attrs[k]));
  parent.appendChild(e);
  return e;
}

// The mark as LiveMark paints its idle pose, part for part, in the classes
// the other scenes draw it with (scenes.css colours them as LiveMark does).
function drawMark(g) {
  const pose = poseFor('idle', 0);
  spokeLines(pose).forEach(([x1, y1, x2, y2], i) => {
    mk(g, 'line', { class: 'srch-handle', 'stroke-width': 2.5, x1, y1, x2, y2, opacity: pose.spokes[i] });
  });
  mk(g, 'circle', { class: 'srch-core', cx: pose.core.x, cy: pose.core.y, r: pose.core.r, opacity: pose.core.o });
  pose.sats.forEach((s) => {
    const p = satXY(s);
    mk(g, 'circle', { class: 'srch-sat', cx: r3(p.x), cy: r3(p.y), r: s.r, opacity: s.o });
  });
}

let uid = 0;

// A spoke of the morphing mark: from 10.5 to 16.5 out from the centre, where
// the logo's run from 7.5 to the satellite's edge at 13.5. Its inner end
// goes in to 6, under the core even when the core shrinks to 4.5 (the lead
// story), and an outer end at 16.5 to the satellite's edge; an end further
// out (the deploy story throws the spokes out) is left where it is.
const SPOKE_SEG = /M\s*(-?[\d.]+)[\s,]+(-?[\d.]+)\s*L\s*(-?[\d.]+)[\s,]+(-?[\d.]+)/g;
function reachCore(content) {
  const fix = (d) => d.replace(SPOKE_SEG, (seg, ...m) => {
    const [x1, y1, x2, y2] = m.slice(0, 4).map(Number);
    const d1 = Math.hypot(x1 - C, y1 - C);
    const d2 = Math.hypot(x2 - C, y2 - C);
    if (Math.abs(d1 - 10.5) > 0.6 || d2 < d1 + 1) return seg;
    const ux = (x1 - C) / d1;
    const uy = (y1 - C) / d1;
    const out = Math.abs(d2 - 16.5) < 0.6 ? 13.5 : d2;
    return `M${r3(C + ux * 6)} ${r3(C + uy * 6)} L${r3(C + ux * out)} ${r3(C + uy * out)}`;
  });
  content.querySelectorAll('path.mark-spoke, .mark-spoke path').forEach((el) => {
    if (el.hasAttribute('d')) el.setAttribute('d', fix(el.getAttribute('d')));
    el.querySelectorAll('animate[attributeName="d"]').forEach((a) => a.setAttribute('values', fix(a.getAttribute('values') || '')));
  });
}

/*
 * Each SMIL line is hidden and redrawn by a copy that follows it, with its
 * ends near a core that has shrunk moved in by as much as the core shrank,
 * so a spoke still meets the core. A line further out than the core's edge
 * is left alone, smoothly, so a spoke the clip draws away from the core
 * keeps doing that. The part moved in lies under the core, in the core's
 * colour, and does not show. Null where the browser has no SMIL values
 * (tests).
 */
function spokeFixer(content) {
  const view = content.ownerDocument?.defaultView;
  const cores = [...content.querySelectorAll('circle.mark-core, circle.v2-goo__core, circle.ex-relay__core')]
    .filter((c) => c.r?.animVal);
  const lines = [...content.querySelectorAll('line')].filter((l) => l.x1?.animVal);
  if (!cores.length || !lines.length || typeof view?.getComputedStyle !== 'function') return null;
  const pairs = lines.map((line) => {
    const copy = line.cloneNode(false);
    line.parentNode.insertBefore(copy, line.nextSibling);
    line.setAttribute('visibility', 'hidden');
    return [line, copy];
  });
  const pull = (cs, x, y) => {
    let best = null;
    cs.forEach((c) => {
      const d = Math.hypot(x - c.x, y - c.y);
      if (!best || d - c.r < best.d - best.c.r) best = { c, d };
    });
    if (!best || best.d < 0.01 || best.c.r >= CORE_R || best.c.r < 2) return [x, y];
    const near = best.d <= CORE_R + 0.5 ? 1 : best.d >= CORE_R + 3 ? 0 : (CORE_R + 3 - best.d) / 2.5;
    const d = Math.max(0, best.d - (CORE_R - best.c.r) * near);
    const k = d / best.d;
    return [best.c.x + (x - best.c.x) * k, best.c.y + (y - best.c.y) * k];
  };
  return () => {
    const cs = cores.map((c) => ({ x: c.cx.animVal.value, y: c.cy.animVal.value, r: c.r.animVal.value }));
    pairs.forEach(([line, copy]) => {
      const [x1, y1] = pull(cs, line.x1.animVal.value, line.y1.animVal.value);
      const [x2, y2] = pull(cs, line.x2.animVal.value, line.y2.animVal.value);
      const st = view.getComputedStyle(line);
      copy.setAttribute('x1', r3(x1));
      copy.setAttribute('y1', r3(y1));
      copy.setAttribute('x2', r3(x2));
      copy.setAttribute('y2', r3(y2));
      copy.setAttribute('opacity', st.opacity);
      copy.setAttribute('stroke-opacity', st.strokeOpacity);
      copy.setAttribute('stroke-width', st.strokeWidth);
    });
  };
}

// A SMIL clip from its file: its parts into `g`, its filter ids made unique
// (several marks can be on one page), its framing and the length of a round.
function insertFile(g, text) {
  uid += 1;
  const id = `ah-clip-${uid}`;
  const doc = new DOMParser().parseFromString(text.replaceAll('"goo"', `"${id}"`).replaceAll('#goo)', `#${id})`), 'image/svg+xml');
  const svg = doc.documentElement;
  if (!svg || svg.nodeName !== 'svg') return null;
  Array.from(svg.childNodes).forEach((n) => g.appendChild(document.importNode(n, true)));
  const vb = (svg.getAttribute('viewBox') || '0 0 48 48').split(/[\s,]+/).map(Number);
  return { size: vb[2] || BOX, dur: Number(svg.getAttribute('data-dur')) || null, cls: svg.getAttribute('class') || '' };
}

/**
 * Mount clip `name` into the scene group `g`; the controller has the shape
 * scenes/index.js expects: update(dt, next) returns 'done' once the clip is
 * the mark again, accepts(next) is true only for the clip itself.
 */
export function mountClip(g, name) {
  const js = JS_CLIPS[name];
  const load = fileOf(name);
  if (!js && !load) return null;
  const root = g.ownerSVGElement;
  // cut at the artifact's own framing (its <svg> cut there too, and some
  // clips fly parts in from outside it); the mark itself draws with overflow
  uid += 1;
  const cut = mk(mk(g, 'clipPath', { id: `ah-clip-cut-${uid}` }), 'rect', { x: 0, y: 0, width: BOX, height: BOX });
  const framed = mk(g, 'g', { 'clip-path': `url(#ah-clip-cut-${uid})` });
  const content = mk(framed, 'g', { opacity: 0 });
  const mark = mk(framed, 'g');
  drawMark(mark);

  const s = { phase: 'load', t: 0, k: 0, a: 0, fit: null, dur: null, end: 0, from: null };
  let player = null;
  let gone = false;

  const start = (info, play) => {
    s.fit = Math.min(1, BOX / info.size);
    const half = Math.max(BOX, info.size) / 2;
    cut.setAttribute('x', C - half);
    cut.setAttribute('y', C - half);
    cut.setAttribute('width', 2 * half);
    cut.setAttribute('height', 2 * half);
    s.dur = info.dur;
    content.setAttribute('class', `clip ${info.cls}`.trim());
    player = play;
    player(0, 0);
    s.phase = 'intro';
  };
  if (js) {
    const inner = mk(content, 'g', { transform: `translate(${C} ${C})` });
    const clip = js.build(inner);
    start(js, (t, dt) => clip.frame(t, dt));
  } else {
    load().then((text) => {
      if (gone) return;
      const info = insertFile(content, text);
      if (!info) return;
      reachCore(content);
      // the root holds nothing else that animates by itself
      root?.pauseAnimations?.();
      const fix = spokeFixer(content);
      start(info, (t) => { root?.setCurrentTime?.(t); fix?.(); });
    }).catch(() => { /* the clip stays the mark */ });
  }

  const fold = () => { s.phase = 'fold'; s.k = 0; s.from = s.a; };
  const paint = (dt) => {
    content.setAttribute('opacity', r3(s.a));
    mark.setAttribute('opacity', r3(1 - s.a));
    if (player) player(s.t, dt);
  };

  function update(step, next) {
    const leaving = next !== name;
    const dt = leaving ? step * LEAVE : step;
    if (s.phase === 'load') {
      // not here yet: the mark is all there is, so a step that ended is done
      return leaving ? 'done' : 'running';
    }
    if (s.phase === 'intro') {
      s.k += dt / INTRO;
      const w = ease(s.k);
      s.a = w;
      if (leaving) fold();
      else if (s.k >= 1) s.phase = 'work';
    } else if (s.phase === 'work' || s.phase === 'tail') {
      if (!leaving) s.phase = 'work';
      s.t += dt;
      if (leaving && s.phase === 'work') {
        const left = s.dur ? s.dur - (s.t % s.dur) : Infinity;
        if (left <= TAIL) { s.phase = 'tail'; s.end = s.t + left; } else fold();
      } else if (s.phase === 'tail' && s.t >= s.end) {
        s.t = s.end;
        fold();
      }
    } else {
      s.k += dt / FOLD;
      const w = ease(s.k);
      s.a = lerp(s.from, 0, w);
      s.t += dt;
      if (s.k >= 1) { s.a = 0; paint(0); return 'done'; }
    }
    paint(dt);
    return 'running';
  }

  return {
    accepts: (next) => next === name,
    update,
    get phase() { return s.phase; },
    /** How far LiveMark has to zoom out to show the clip's framing; null until it is known. */
    get fit() { return s.fit; },
    destroy() {
      gone = true;
      while (g.firstChild) g.removeChild(g.firstChild);
    },
  };
}
