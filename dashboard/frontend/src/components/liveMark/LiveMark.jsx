import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useI18n } from '../../i18n';
import { CX, CY, STATIC_STATES, mixPose, poseFor, easeInOut, satXY, spokeLines } from './poses';
import { isScene, mountScene } from './scenes';
import useSteadyState from './useSteadyState';
import './scenes/scenes.css';

/*
 * The project mark, alive: it shows what an agent is doing and moves from
 * one state to the next instead of cutting between clips.
 *
 * A state is either a pose or a scene.
 *
 * A pose (poses.js: working, thinking, reading, running code, answering,
 * waiting) is the mark's own parts placed by a function of time. On a switch
 * the outgoing pose keeps moving while it is blended into the incoming one,
 * so a satellite that was circling carries on and curves into its new place;
 * a switch in the middle of a blend freezes the blend where it is and blends
 * from that.
 *
 * A scene (scenes/: eleven kinds of search, writing a file or code, making a
 * view, a page, a 3D model, a flow, a team, an agent, delegating) is drawn
 * after the "Orbit Loader" artifact: the mark rebuilds itself into the
 * thing, works on it for as long as the state lasts (the lens keeps
 * searching until the answer comes), finishes it (the find, the saved file,
 * the result coming back) and folds back. Scenes start and end as the mark,
 * so the mark is where they hand over: a pose blends into it and the scene
 * unfolds from it. One search goes straight on into the next: the lens stays
 * and only the field under it changes.
 *
 * A clip whose drawing needs more room than the mark's box (scenes/clips.js)
 * makes the whole mark step back, poses and scenes alike. It stays back for
 * as long as the work goes on, so a clip after a clip does not grow the mark
 * and shrink it again, and only comes forward once the mark rests, answers,
 * listens or waits; while it works it only ever steps further back.
 *
 * Frames are written straight into the SVG, not through React, and the loop
 * stops once the mark rests on a still pose (idle).
 *
 * `minHold` keeps every state on show for at least that long, so a tool that
 * returns in 100 ms does not make the mark flicker; only the latest state
 * that arrived in the meantime is shown next. `frame="logo"` uses the crop
 * of public/logo.svg, so the idle mark is identical to the image.
 */

const VIEWBOX = { square: '0 0 48 48', logo: '2.5 0.5 43 39' };
const BLEND_MS = 650;
const SETTLE_MS = 800;
const GATHER_MS = 450;       // a pose gathering into the mark before a scene
const UNFOLD_MS = 450;       // the mark opening into a pose after a scene
const ZOOM_TAU = 0.16;       // seconds: how fast the mark steps back or comes forward
// The states in which the mark is at its own size again.
const FULL_SIZE = new Set(['idle', 'speak', 'listen', 'wait']);
const FILL = 'var(--brand-500, #3f66d8)';

function reducedMotion() {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

function paint(parts, pose) {
  const { core } = pose;
  const c = parts.core;
  if (!c) return;
  c.setAttribute('cx', core.x.toFixed(2));
  c.setAttribute('cy', core.y.toFixed(2));
  c.setAttribute('r', Math.max(0, core.r).toFixed(2));
  c.setAttribute('opacity', core.o.toFixed(3));
  const lines = spokeLines(pose);
  pose.sats.forEach((s, i) => {
    const el = parts.sats[i];
    const sp = parts.spokes[i];
    const p = satXY(s);
    if (el) {
      el.setAttribute('cx', p.x.toFixed(2));
      el.setAttribute('cy', p.y.toFixed(2));
      el.setAttribute('r', Math.max(0, s.r).toFixed(2));
      el.setAttribute('opacity', Math.max(0, s.o).toFixed(3));
    }
    if (sp) {
      // A spoke never outlives its satellite: one leading to a dot that has
      // faded or shrunk away would be a stray line.
      const [x1, y1, x2, y2] = lines[i];
      const o = pose.spokes[i] * Math.min(1, Math.max(0, s.o) / 0.5) * Math.min(1, Math.max(0, s.r) / 2);
      const visible = Math.hypot(x2 - x1, y2 - y1) > 0.5 && o > 0.01;
      sp.setAttribute('x1', x1.toFixed(2));
      sp.setAttribute('y1', y1.toFixed(2));
      sp.setAttribute('x2', x2.toFixed(2));
      sp.setAttribute('y2', y2.toFixed(2));
      sp.setAttribute('opacity', visible ? o.toFixed(3) : '0');
    }
  });
}

function createEngine(initialPose) {
  const reduce = reducedMotion();
  const entry = (name, t0) => ({
    name,
    at: (n) => poseFor(name, reduce ? 0 : (n - t0) / 1000),
  });
  return {
    reduce,
    speed: reduce ? 0.35 : 1,
    entry,
    mode: 'pose',
    to: entry(initialPose, now()),
    from: null,
    blendStart: 0,
    blendMs: BLEND_MS,
    scene: null,
    target: initialPose,
    zoom: 1,
    zoomGoal: 1,
    last: 0,
    raf: 0,
  };
}

export default function LiveMark({
  state = 'idle', initial = 'idle', size = 32, frame = 'square', minHold = 700,
  label, className = '', style,
}) {
  const { t } = useI18n();
  const shown = useSteadyState(state, minHold);
  const initialPose = isScene(initial) ? 'idle' : initial;
  const [first] = useState(() => poseFor(initialPose, 0));
  const parts = useRef({ core: null, sats: [], spokes: [], pose: null, scene: null, zoom: null });
  const engine = useRef(null);

  // The first frame goes in before the browser paints, spokes included.
  useLayoutEffect(() => { paint(parts.current, first); }, [first]);

  useEffect(() => {
    if (engine.current == null) engine.current = createEngine(initialPose);
    const e = engine.current;
    const P = parts.current;
    const raf = typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function';
    const weight = (n) => easeInOut(Math.min(1, (n - e.blendStart) / e.blendMs));
    const frameAt = (n) => (e.from ? mixPose(e.from(n), e.to.at(n), weight(n)) : e.to.at(n));

    // Blend the pose on show into `name`, from wherever it is right now; a
    // blend cut short keeps both its poses moving at the weight it reached.
    const retarget = (name, n, ms) => {
      if (name === e.to.name) return;
      const { from, to } = e;
      const w = from ? weight(n) : 1;
      e.from = from ? (m) => mixPose(from(m), to.at(m), w) : to.at;
      e.to = e.entry(name, n);
      e.blendStart = n;
      e.blendMs = e.reduce ? 250 : ms || (name !== 'idle' ? BLEND_MS : isScene(e.target) ? GATHER_MS : SETTLE_MS);
    };
    const mount = (name, n) => {
      const api = P.scene && mountScene(name, P.scene);
      if (!api) { retarget('working', n); return; }
      P.pose?.setAttribute('display', 'none');
      e.mode = 'scene';
      e.scene = { name, api };
    };
    // The scene has folded into the mark: the poses take over there, a
    // little quicker than between poses, as the scene has taken its time.
    const handOver = (n) => {
      e.scene.api.destroy();
      e.scene = null;
      e.mode = 'pose';
      P.pose?.removeAttribute('display');
      e.to = e.entry('idle', n);
      e.from = null;
      paint(P, poseFor('idle', 0));
      if (isScene(e.target)) mount(e.target, n);
      else retarget(e.target, n, UNFOLD_MS);
    };
    // The scene is told what is wanted now: its own state (or, for a search,
    // the next search) to carry on, anything else to finish and fold.
    const stepScene = (n, dt) => {
      const { api } = e.scene;
      const next = api.accepts(e.target) ? e.target : null;
      if (api.update(dt * e.speed, next) === 'done') handOver(n);
    };
    // Back as far as the clip on show needs, forward once the mark is at rest.
    const stepZoom = (dt) => {
      const fit = e.mode === 'scene' ? e.scene?.api.fit : null;
      if (fit) e.zoomGoal = Math.min(e.zoomGoal, fit);
      else if (e.mode === 'pose' && FULL_SIZE.has(e.target)) e.zoomGoal = 1;
      if (e.zoom === e.zoomGoal) return;
      e.zoom += (e.zoomGoal - e.zoom) * (e.reduce ? 1 : 1 - Math.exp(-dt / ZOOM_TAU));
      if (Math.abs(e.zoom - e.zoomGoal) < 0.002) e.zoom = e.zoomGoal;
      P.zoom?.setAttribute('transform', e.zoom === 1 ? ''
        : `translate(${CX} ${CY}) scale(${e.zoom.toFixed(3)}) translate(${-CX} ${-CY})`);
    };
    const tick = (n) => {
      e.raf = 0;
      const dt = e.last ? Math.min(0.1, (n - e.last) / 1000) : 0;
      e.last = n;
      if (e.mode === 'pose') {
        if (isScene(e.target) && !e.from && e.to.name === 'idle') mount(e.target, n);
        else {
          paint(P, frameAt(n));
          if (e.from && n - e.blendStart >= e.blendMs) e.from = null;
        }
      } else {
        stepScene(n, dt);
      }
      stepZoom(dt);
      const still = e.mode === 'pose' && !e.from && !isScene(e.target) && e.zoom === e.zoomGoal
        && (e.reduce || STATIC_STATES.has(e.to.name));
      if (!still && raf) e.raf = window.requestAnimationFrame(tick);
      else e.last = 0;
    };

    e.target = shown;
    if (e.mode === 'pose') retarget(isScene(shown) ? 'idle' : shown, now());
    if (!e.raf) {
      if (raf) e.raf = window.requestAnimationFrame(tick);
      else paint(P, frameAt(now()));
    }
  }, [shown, initialPose]);

  useEffect(() => () => {
    const e = engine.current;
    if (e?.raf && typeof window !== 'undefined') window.cancelAnimationFrame(e.raf);
    if (e) e.raf = 0;
  }, []);

  const ratio = frame === 'logo' ? 39 / 43 : 1;
  const name = label === undefined ? t(`liveMark.states.${shown}`) : label;
  const set = (key, i) => (el) => {
    if (i === undefined) parts.current[key] = el;
    else parts.current[key][i] = el;
  };
  return (
    <svg
      viewBox={VIEWBOX[frame] || VIEWBOX.square}
      width={size}
      height={size * ratio}
      overflow="visible"
      role={name ? 'img' : undefined}
      aria-label={name || undefined}
      aria-hidden={name ? undefined : true}
      data-state={shown}
      className={`ah-live-mark ${className}`.trim()}
      style={style}
    >
      <g ref={set('zoom')}>
        <g ref={set('pose')}>
          <g strokeWidth="2.5" strokeLinecap="round" style={{ stroke: FILL }}>
            {first.sats.map((s, i) => (
              <line key={i} ref={set('spokes', i)} x1={CX} y1={CY} x2={CX} y2={CY} opacity="0" />
            ))}
          </g>
          {first.sats.map((s, i) => {
            const p = satXY(s);
            return (
              <circle key={i} ref={set('sats', i)} cx={p.x} cy={p.y} r={s.r} opacity={s.o} style={{ fill: FILL }} />
            );
          })}
          <circle ref={set('core')} cx={first.core.x} cy={first.core.y} r={first.core.r} opacity={first.core.o} style={{ fill: FILL }} />
        </g>
        {/* A scene draws itself in here (scenes/), the poses are hidden meanwhile. */}
        <g ref={set('scene')} />
      </g>
    </svg>
  );
}
