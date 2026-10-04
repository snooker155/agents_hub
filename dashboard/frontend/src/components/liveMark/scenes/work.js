/*
 * Agent-at-work scenes, drawn after the "Orbit Loader" artifact: the mark
 * rebuilds itself into what the agent is making or running, works on it for
 * as long as the step lasts, finishes it and folds back.
 *
 * A scene's frame(st, t, w) draws the artifact's clip at time t (it unfolds
 * from 0.5 s, is folded again by 6.9 s; bell(t, a, b, c, d) rises over a..b
 * and falls over c..d), with w = { live, work, out }: a clock that never
 * stops, the seconds spent working, and the seconds since the step ended
 * (negative while it lasts). mountWork() plays the clip up to the scene's
 * hold point, keeps it there while the work goes on (what moves meanwhile
 * comes from `live` and `work`: bars that follow fresh data, code typed and
 * retyped, a flow run round after round), lets the scene settle what the
 * work left half done when the step ends, then plays the finale and the fold
 * from the scene's outFrom point.
 */
/* eslint-disable no-unused-vars */

var NS = 'http://www.w3.org/2000/svg';
var C = 24, T = 7.6;
// public/logo.svg to the decimal (as LOGO_SATS and LOGO_SPOKES in poses.js),
// so every scene starts from and ends on the very mark LiveMark draws
var SAT = [[24, 6], [40, 34], [8, 34]];   // top, lower right, lower left
var SPOKE = [[24, 16.5, 24, 10.5], [29.6, 28.2, 35.6, 31.6], [18.4, 28.2, 12.4, 31.6]];   // inner end, outer end
var RS = SAT.map(function (p) { return Math.hypot(p[0] - C, p[1] - C); });
var DIR = SAT.map(function (p, i) { return [(p[0] - C) / RS[i], (p[1] - C) / RS[i]]; });
var ANG = SAT.map(function (p) { return Math.atan2(p[1] - C, p[0] - C) * 180 / Math.PI; });
function set(e, a) { for (var k in a) e.setAttribute(k, typeof a[k] === 'number' ? Math.round(a[k] * 1000) / 1000 : a[k]); return e; }
function mk(parent, tag, a) { var e = document.createElementNS(NS, tag); if (a) set(e, a); parent.appendChild(e); return e; }
function clamp(u) { return u < 0 ? 0 : u > 1 ? 1 : u; }
function ph(t, a, b) { return clamp((t - a) / (b - a)); }
function ease(u) { u = clamp(u); return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2; }
function smooth(u) { u = clamp(u); return u * u * (3 - 2 * u); }
function backOut(u) { u = clamp(u); var c = 1.7; return 1 + (c + 1) * Math.pow(u - 1, 3) + c * Math.pow(u - 1, 2); }
function bell(t, a, b, c, d) { return ease(ph(t, a, b)) - ease(ph(t, c, d)); }
function lerp(a, b, u) { return a + (b - a) * u; }
function lerp2(p, q, u) { return [lerp(p[0], q[0], u), lerp(p[1], q[1], u)]; }
function fx(n) { return Math.round(n * 100) / 100; }
function tr(x, y, s) { return 'translate(' + fx(x) + ' ' + fx(y) + ')' + (s === undefined ? '' : ' scale(' + fx(Math.max(0, s)) + ')'); }
function polar(a, r) { var q = a * Math.PI / 180; return [C + Math.cos(q) * r, C + Math.sin(q) * r]; }
// a point a fraction f of the way along a polyline
function along(pts, f) {
  var L = 0, seg = [];
  for (var i = 1; i < pts.length; i++) { var l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); seg.push(l); L += l; }
  var x = clamp(f) * L;
  for (i = 0; i < seg.length; i++) { if (x <= seg[i] || i === seg.length - 1) return lerp2(pts[i], pts[i + 1], clamp(x / seg[i])); x -= seg[i]; }
}
// the plain mark: spokes, core, satellites
function Logo(g) {
  return {
    spokes: [0, 1, 2].map(function () { return mk(g, 'line', { class: 'srch-handle', 'stroke-width': 2.5 }); }),
    core: mk(g, 'circle', { class: 'srch-core' }),
    sats: [0, 1, 2].map(function () { return mk(g, 'circle', { class: 'srch-sat' }); })
  };
}
// spokes stay where the mark has them and fade by so[i]
function drawLogo(lg, c, cr, co, sats, so) {
  set(lg.core, { cx: c[0], cy: c[1], r: Math.max(0, cr), opacity: co });
  sats.forEach(function (q, i) { set(lg.sats[i], { cx: q.p[0], cy: q.p[1], r: Math.max(0, q.r), opacity: q.o }); });
  SPOKE.forEach(function (q, i) { set(lg.spokes[i], { x1: q[0], y1: q[1], x2: q[2], y2: q[3], opacity: 0.55 * so[i] }); });
}
function fade(m) { return [1 - m, 1 - m, 1 - m]; }
function mod(a, n) { return ((a % n) + n) % n; }
// a rise and fall over 0..1
function bump(u) { return Math.sin(Math.PI * clamp(u)); }
// how much of the working motion shows: none in the intro, eased in once
// the work starts, eased out once the step ends
function amp(w) { return smooth(w.work / 0.3) * (w.out < 0 ? 1 : 1 - smooth(w.out / 0.3)); }
var EXIT_SPEED = 1.8;

var W = {};

/* view: the core becomes a donut chart, the satellites drop onto an axis
   and grow into bars, a trend line draws in; the card opens from the centre */
W.view = {
  hold: 4.0,
  build: function (g) {
    var st = {};
    st.frame = mk(g, 'rect', { class: 'wk-frame', rx: 2.5 });
    st.title = mk(g, 'rect', { class: 'srch-mark', x: 8.5, y: 11, width: 10, height: 1.6, rx: 0.8 });
    st.axis = mk(g, 'line', { class: 'srch-line', x1: 8, y1: 35, x2: 27, y2: 35 });
    st.track = mk(g, 'circle', { class: 'wk-track', cx: 34.5, cy: 17, r: 4.2 });
    st.arc = mk(g, 'circle', { class: 'wk-arc', cx: 34.5, cy: 17, r: 4.2, transform: 'rotate(-90 34.5 17)' });
    st.spark = mk(g, 'path', { class: 'srch-hot', 'stroke-width': 0.9, d: 'M29.5 35L32.5 30.5L35.5 32L38.5 27L41 28.5', pathLength: 1, 'stroke-dasharray': '1 1' });
    st.bars = [0, 1, 2].map(function () { return mk(g, 'rect', { class: 'srch-sat', width: 4, rx: 1 }); });
    st.logo = Logo(g);
    return st;
  },
  frame: function (st, t, w) {
    var m = bell(t, 0.5, 1.4, 5.7, 6.6), b = bell(t, 1.3, 2.1, 5.1, 5.7), d = bell(t, 1.8, 2.9, 4.9, 5.5), l = bell(t, 2.4, 3.3, 4.8, 5.3);
    var fw = 38 * m, fh = 32 * m;
    set(st.frame, { x: C - fw / 2, y: C - fh / 2, width: fw, height: fh, opacity: m });
    set(st.title, { opacity: smooth(ph(m, 0.6, 1)) });
    set(st.axis, { opacity: b });
    var BX = [11.5, 17.5, 23.5], H = [10, 17, 7], map = [1, 2, 0];   // satellite i grows bar map[i]
    var sats = [0, 1, 2].map(function (i) {
      var j = map[i], hh = 4 + (H[j] + 1.6 * Math.sin(w.live * 2.3 + j * 1.7) * b - 4) * b;
      set(st.bars[j], { x: BX[j] - 2, y: 35 - hh, height: hh, opacity: b > 0.02 ? 1 : 0 });
      return { p: lerp2(SAT[i], [BX[j], 33], m), r: lerp(4.5, 2, m), o: b > 0.02 ? 0 : lerp(0.75, 1, m) };
    });
    drawLogo(st.logo, lerp2([C, C], [34.5, 17], m), lerp(7, 4.2, m), 1 - smooth(d * 1.5), sats, fade(m));
    set(st.track, { opacity: Math.min(1, d * 3) });
    set(st.arc, { 'stroke-dasharray': fx(26.39 * (0.68 + 0.06 * Math.sin(w.live * 1.3)) * d) + ' 30', opacity: Math.min(1, d * 3) });
    set(st.spark, { 'stroke-dashoffset': fx(1 - l), opacity: l > 0.01 ? 1 : 0 });
  }
};

/* 3D: the mark turns in space, its four points become vertices of an
   icosahedron, the rest come out of the centre, edges draw one by one,
   faces take the light; two full turns and it folds back */
var GEN_SPIN = 1.1;
W.gen3d = {
  hold: 4.35,
  // turning for as long as it works; once the step ends, on to the next
  // whole turn by the time it has folded, starting at the speed it had
  turn: function (st, t, w) {
    var th = GEN_SPIN * w.live;
    if (w.out < 0) return th;
    if (st.th0 == null) {
      st.thD = (6.4 - this.hold) / EXIT_SPEED; st.th0 = th;
      st.thT = 2 * Math.PI * Math.ceil((th + GEN_SPIN * st.thD * 0.5) / (2 * Math.PI));
    }
    var s = ph(t, this.hold, 6.4), m0 = GEN_SPIN * st.thD;
    return (2 * s * s * s - 3 * s * s + 1) * st.th0 + (s * s * s - 2 * s * s + s) * m0 + (3 * s * s - 2 * s * s * s) * st.thT;
  },
  build: function (g) {
    var PHI = (1 + Math.sqrt(5)) / 2, n = Math.sqrt(1 + PHI * PHI), RAD = 16, el = 2 / n * RAD;
    var V3 = [[0, 1, PHI], [0, -1, PHI], [0, 1, -PHI], [0, -1, -PHI], [1, PHI, 0], [-1, PHI, 0], [1, -PHI, 0], [-1, -PHI, 0], [PHI, 0, 1], [PHI, 0, -1], [-PHI, 0, 1], [-PHI, 0, -1]]
      .map(function (v) { return v.map(function (c) { return c / n * RAD; }); });
    function d3(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]); }
    var E = [], F = [], i, j, k;
    for (i = 0; i < 12; i++) for (j = i + 1; j < 12; j++) if (Math.abs(d3(V3[i], V3[j]) - el) < 1e-6) E.push([i, j]);
    function adj(a, b) { return E.some(function (e) { return (e[0] === a && e[1] === b) || (e[0] === b && e[1] === a); }); }
    for (i = 0; i < 12; i++) for (j = i + 1; j < 12; j++) for (k = j + 1; k < 12; k++) if (adj(i, j) && adj(j, k) && adj(i, k)) F.push([i, j, k]);
    var st = { V: V3, E: E, F: F, g: g, coreV: 1, satV: [0, 9, 10] };
    st.rest = []; for (i = 0; i < 12; i++) if (i !== st.coreV && st.satV.indexOf(i) < 0) st.rest.push(i);
    st.faces = F.map(function () { return mk(g, 'path', { class: 'wk-face' }); });
    st.edges = E.map(function () { return mk(g, 'line', { class: 'wk-edge' }); });
    st.spokes = [0, 1, 2].map(function () { return mk(g, 'line', { class: 'srch-handle', 'stroke-width': 2.5 }); });
    st.core = mk(g, 'circle', { class: 'srch-core' });
    st.dots = V3.map(function () { return mk(g, 'circle', { class: 'srch-sat' }); });
    var ax = [0.35, 1, 0.25], al = Math.hypot(ax[0], ax[1], ax[2]);
    st.axis = ax.map(function (c) { return c / al; });
    st.light = (function () { var L = [-0.4, -0.6, 0.7], l = Math.hypot(L[0], L[1], L[2]); return L.map(function (c) { return c / l; }); })();
    return st;
  },
  frame: function (st, t, w) {
    var th = this.turn(st, t, w), a = st.axis, c = Math.cos(th), s = Math.sin(th), u = 1 - c;
    var M = [[u * a[0] * a[0] + c, u * a[0] * a[1] - s * a[2], u * a[0] * a[2] + s * a[1]], [u * a[0] * a[1] + s * a[2], u * a[1] * a[1] + c, u * a[1] * a[2] - s * a[0]], [u * a[0] * a[2] - s * a[1], u * a[1] * a[2] + s * a[0], u * a[2] * a[2] + c]];
    function rot(v) { return [M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2], M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2], M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2]]; }
    function proj(v) { var k = 90 / (90 - v[2]); return { x: C + v[0] * k, y: C + v[1] * k, z: v[2], k: k }; }
    function mix(p, q, f) { return [lerp(p[0], q[0], f), lerp(p[1], q[1], f), lerp(p[2], q[2], f)]; }
    var m = bell(t, 1.0, 2.0, 5.4, 6.4);
    var logo3 = SAT.map(function (p) { return [p[0] - C, p[1] - C, 0]; });
    var P = [], R = [];
    var coreP = mix([0, 0, 0], st.V[st.coreV], m);
    P[st.coreV] = coreP; R[st.coreV] = 1.6 * m;
    st.satV.forEach(function (vi, i) { P[vi] = mix(logo3[i], st.V[vi], m); R[vi] = lerp(4.5, 1.6, m); });
    st.rest.forEach(function (vi, k) { var e = bell(t, 1.5 + k * 0.12, 1.95 + k * 0.12, 5.0 - k * 0.04, 5.5 - k * 0.04); P[vi] = mix([0, 0, 0], st.V[vi], e); R[vi] = 1.6 * e; });
    var Q = P.map(function (v) { return proj(rot(v)); });
    // faces: only those turned towards the viewer, shaded by the light
    var fo = bell(t, 3.6, 4.2, 4.5, 5.0), order = [];
    st.F.forEach(function (f, i) {
      var cen = rot([(st.V[f[0]][0] + st.V[f[1]][0] + st.V[f[2]][0]) / 3, (st.V[f[0]][1] + st.V[f[1]][1] + st.V[f[2]][1]) / 3, (st.V[f[0]][2] + st.V[f[1]][2] + st.V[f[2]][2]) / 3]);
      var l = Math.hypot(cen[0], cen[1], cen[2]), nz = cen[2] / l;
      var shade = 0.2 + 0.55 * Math.max(0, (cen[0] * st.light[0] + cen[1] * st.light[1] + cen[2] * st.light[2]) / l);
      set(st.faces[i], { d: 'M' + f.map(function (vi) { return fx(Q[vi].x) + ' ' + fx(Q[vi].y); }).join('L') + 'Z', 'fill-opacity': nz > 0 ? shade * fo : 0 });
      order.push({ el: st.faces[i], z: cen[2] });
    });
    order.sort(function (x, y) { return x.z - y.z; }).forEach(function (o) { st.g.appendChild(o.el); });
    st.E.forEach(function (e, i) {
      var p = bell(t, 2.5 + i * 0.035, 2.85 + i * 0.035, 4.6, 5.2), A = Q[e[0]], B = Q[e[1]];
      set(st.edges[i], { x1: A.x, y1: A.y, x2: lerp(A.x, B.x, p), y2: lerp(A.y, B.y, p), opacity: p > 0.01 ? 0.35 + 0.65 * clamp((A.z + B.z) / 64 + 0.5) : 0 });
      st.g.appendChild(st.edges[i]);
    });
    var cp = proj(rot(coreP));
    SPOKE.forEach(function (q, i) {
      // from the logo's own spoke, flat, to the edge it becomes in space
      var a3 = rot(mix([q[0] - C, q[1] - C, 0], mix(coreP, P[st.satV[i]], 7.5 / 18), m));
      var b3 = rot(mix([q[2] - C, q[3] - C, 0], mix(coreP, P[st.satV[i]], 13.5 / 18), m)), A = proj(a3), B = proj(b3);
      set(st.spokes[i], { x1: A.x, y1: A.y, x2: B.x, y2: B.y, opacity: 0.55 * (1 - m) });
      st.g.appendChild(st.spokes[i]);
    });
    set(st.core, { cx: cp.x, cy: cp.y, r: lerp(7, 1.6, m) * cp.k, opacity: 1 - m });
    st.g.appendChild(st.core);
    Q.map(function (q, i) { return { q: q, i: i }; }).sort(function (x, y) { return x.q.z - y.q.z; }).forEach(function (o) {
      var i = o.i, isSat = st.satV.indexOf(i) >= 0, op = i === st.coreV ? m : isSat ? lerp(0.75, 1, m) : 1;
      set(st.dots[i], { cx: o.q.x, cy: o.q.y, r: R[i] * o.q.k, opacity: op * (0.7 + 0.3 * clamp(o.q.z / 16 + 0.5)) });
      st.g.appendChild(st.dots[i]);
    });
  }
};

/* html: the core stretches into a browser window, the satellites become
   the dots in its title bar, the page drops in block by block */
W.html = {
  hold: 4.5,
  settle: function () { return [0.3]; },
  build: function (g) {
    var st = {};
    st.win = mk(g, 'rect', { class: 'wk-frame' });
    st.coreRect = mk(g, 'rect', { class: 'srch-core' });
    st.bar = mk(g, 'line', { class: 'srch-line', x1: 5, y1: 13, x2: 43, y2: 13, 'stroke-width': 0.5 });
    st.addr = mk(g, 'rect', { class: 'srch-band', x: 18, y: 9.4, height: 2.2, rx: 1.1, width: 0 });
    st.tag = mk(g, 'text', { class: 'wk-tag', x: 19.3, y: 11.25 }); st.tag.textContent = '</>';
    var b = [];
    var g0 = mk(g, 'g'); mk(g0, 'rect', { class: 'srch-mark', x: 8, y: 15.4, width: 8, height: 1.6, rx: 0.8 }); mk(g0, 'rect', { class: 'srch-band', x: 28, y: 15.4, width: 12, height: 1.6, rx: 0.8 }); b.push(g0);
    var g1 = mk(g, 'g'); mk(g1, 'rect', { class: 'wk-hero', x: 8, y: 19, width: 32, height: 7.5, rx: 1.2 }); mk(g1, 'line', { class: 'srch-fline srch-fline--hit', x1: 11, y1: 21.8, x2: 27, y2: 21.8, 'stroke-width': 1.1 }); mk(g1, 'line', { class: 'srch-fline srch-fline--hit', x1: 11, y1: 24, x2: 21, y2: 24 }); b.push(g1);
    var g2 = mk(g, 'g'); mk(g2, 'rect', { class: 'srch-band', x: 8, y: 28.6, width: 13, height: 9.4, rx: 1 }); mk(g2, 'path', { class: 'srch-glyph', d: 'M9.5 36.6l3.5 -4l2.5 2.5l2 -2l2.5 3.5' }); mk(g2, 'circle', { class: 'srch-dot', cx: 17.5, cy: 31.2, r: 1 }); b.push(g2);
    var g3 = mk(g, 'g'); [[29.6, 40], [32, 37], [34.4, 39]].forEach(function (l) { mk(g3, 'line', { class: 'srch-fline', x1: 23.5, y1: l[0], x2: l[1], y2: l[0] }); }); b.push(g3);
    var g4 = mk(g, 'g'); mk(g4, 'rect', { class: 'wk-accent', x: 23.5, y: 35.8, width: 8, height: 2.4, rx: 1.2 }); b.push(g4);
    st.blocks = b;
    st.logo = Logo(g);
    return st;
  },
  frame: function (st, t, w) {
    var m = bell(t, 0.5, 1.4, 5.7, 6.6), ww = lerp(14, 38, m), wh = lerp(14, 32, m), rx = lerp(7, 2.5, m);
    var geo = { x: C - ww / 2, y: C - wh / 2, width: ww, height: wh, rx: rx };
    set(st.win, geo); set(st.win, { opacity: m });
    set(st.coreRect, geo); set(st.coreRect, { opacity: 1 - m });
    set(st.bar, { opacity: smooth(ph(m, 0.7, 1)) });
    var a = bell(t, 1.3, 1.8, 5.3, 5.7);
    set(st.addr, { width: 22 * a }); set(st.tag, { opacity: a });
    // while it works the blocks re-render one after another
    var a0 = amp(w), cyc = mod(w.work, 2.6);
    st.blocks.forEach(function (bl, i) {
      var q = bell(t, 1.7 + i * 0.4, 2.1 + i * 0.4, 5.0 - i * 0.1, 5.35 - i * 0.1), b = a0 * bump((cyc - i * 0.3) / 0.5);
      set(bl, { opacity: q * (1 - 0.35 * b), transform: tr(0, -2.5 * (1 - q) - 1.5 * b) });
    });
    var DOT = [[11.5, 10.5], [14.5, 10.5], [8.5, 10.5]];
    drawLogo(st.logo, [C, C], 0, 0, [0, 1, 2].map(function (i) { return { p: lerp2(SAT[i], DOT[i], m), r: lerp(4.5, 0.9, m), o: lerp(0.75, 1, m) }; }), fade(m));
  }
};

/* code: the satellites move to the margin as line numbers, the core
   shrinks into the caret, and the caret types the code */
W.code = {
  hold: 4.8,
  // how much is typed: the whole listing over the intro; while it works the
  // last lines are erased and typed again; once the step ends whatever is
  // missing is typed at three times the pace
  progress: function (st, t, w) {
    var rate = st.total / 3.2;
    if (w.work <= 0 && w.out < 0) return st.total * ph(t, 1.6, 4.8);
    var p0 = st.lines[5].a, erase = 0.5, type = (st.total - p0) / rate, cyc = 0.2 + erase + 0.25 + type + 0.6;
    var f = mod(w.work, cyc), P = st.total;
    if (f >= 0.2 && f < 0.2 + erase) P = lerp(st.total, p0, (f - 0.2) / erase);
    else if (f >= 0.2 + erase && f < 0.45 + erase) P = p0;
    else if (f >= 0.45 + erase && f < 0.45 + erase + type) P = p0 + rate * (f - 0.45 - erase);
    return w.out < 0 ? P : Math.min(st.total, P + 3 * rate * w.out);
  },
  settle: function (st, w) {
    var P = this.progress(st, this.hold, { live: w.live, work: w.work, out: -1 });
    return [(st.total - P) / (st.total / 3.2 * 3)];
  },
  build: function (g) {
    var st = { toks: [], lines: [] }, pos = 0;
    st.frame = mk(g, 'rect', { class: 'wk-frame', x: 5, y: 7, width: 38, height: 34, rx: 2 });
    st.gut = mk(g, 'line', { class: 'srch-line', x1: 11, y1: 7, x2: 11, y2: 41, 'stroke-width': 0.5 });
    var LINES = [[0, [['kw', 3], ['id', 5]], '{'], [1, [['kw', 2.5], ['id', 4], ['str', 6]]], [1, [['id', 3.5], ['id', 5.5]]], [1, [['kw', 2], ['id', 3.5]], '{'], [2, [['id', 6], ['str', 4.5]]], [2, [['kw', 3], ['id', 3]]], [1, [], '}'], [1, [['kw', 3.5], ['id', 4]]], [0, [], '}']];
    LINES.forEach(function (ln, i) {
      var y = 11 + i * 3.6, x = 13 + ln[0] * 2.6, start = pos, x0 = x;
      ln[1].forEach(function (tk) {
        st.toks.push({ el: mk(g, 'line', { class: 'srch-tok srch-tok--' + tk[0], x1: x, y1: y, x2: x, y2: y, opacity: 0 }), x: x, len: tk[1], y: y, a: pos });
        pos += tk[1] + 1.3; x += tk[1] + 1.3;
      });
      if (ln[2]) {
        var b = mk(g, 'text', { class: 'srch-brace', x: x, y: y + 1.1, opacity: 0 }); b.textContent = ln[2];
        st.toks.push({ brace: b, x: x, len: 1.8, y: y, a: pos }); pos += 1.8; x += 1.8;
      }
      st.lines.push({ y: y, a: start, x0: x0, end: x, b: pos });
      pos += 3;   // a newline takes a moment
    });
    st.total = pos;
    st.nums = st.lines.map(function (ln, i) { return i < 3 ? null : mk(g, 'circle', { class: 'srch-dot', cx: 8, cy: ln.y, r: 0.6 }); });
    st.caret = mk(g, 'rect', { class: 'srch-hit', width: 0.8, height: 3.2 });
    st.logo = Logo(g);
    return st;
  },
  caret: function (st, P) {
    var li = 0;
    st.lines.forEach(function (ln, i) { if (ln.a <= P) li = i; });
    var ln = st.lines[li];
    return [P >= ln.b ? ln.end : ln.x0 + Math.max(0, P - ln.a), ln.y];
  },
  frame: function (st, t, w) {
    var mIn = ease(ph(t, 0.5, 1.3)), mOut = ease(ph(t, 5.7, 6.6)), m = mIn - mOut;
    var P = this.progress(st, t, w), cf = 1 - smooth(ph(t, 5.0, 5.5));
    set(st.frame, { opacity: m }); set(st.gut, { opacity: m });
    st.toks.forEach(function (tk) {
      var shown = clamp((P - tk.a) / tk.len) * tk.len;
      if (tk.brace) set(tk.brace, { opacity: (shown >= tk.len * 0.5 ? 1 : 0) * cf });
      else set(tk.el, { x2: tk.x + shown, opacity: shown > 0 ? cf : 0 });
    });
    st.nums.forEach(function (n, i) { if (n) set(n, { opacity: (P >= st.lines[i].a ? 0.8 : 0) * cf }); });
    var start = [st.lines[0].x0, st.lines[0].y], end = this.caret(st, st.total), cp = t < 3 ? this.caret(st, P) : this.caret(st, P);
    var idle = t < 1.6 || t > 4.8, blink = idle ? (Math.floor(t * 2.6) % 2 ? 0.2 : 1) : 1;
    set(st.caret, { x: cp[0] + 0.3, y: cp[1] - 1.6, opacity: m * blink * (t > 5.6 ? 0 : 1) });
    var coreAt = t < 3 ? lerp2([C, C], start, mIn) : lerp2(end, [C, C], mOut);
    var NUM = [[8, st.lines[0].y], [8, st.lines[1].y], [8, st.lines[2].y]];
    drawLogo(st.logo, coreAt, lerp(7, 1.6, m), 1 - m, [0, 1, 2].map(function (i) { return { p: lerp2(SAT[i], NUM[i], m), r: lerp(4.5, 0.6, m), o: lerp(0.75, 0.8, m) }; }), fade(m));
  }
};

/* file: the core unfolds into a sheet, the top satellite becomes a pen
   and writes the lines, a saved mark appears in the corner */
W.file = {
  hold: 4.5,
  // as in code: the page over the intro, the last lines rewritten while it
  // works, the rest written at three times the pace once the step ends
  progress: function (st, t, w) {
    var rate = st.total / 3.0;
    if (w.work <= 0 && w.out < 0) return st.total * ph(t, 1.5, 4.5);
    var p0 = st.lines[4].a, erase = 0.45, type = (st.total - p0) / rate, cyc = 0.3 + erase + 0.25 + type + 0.6;
    var f = mod(w.work, cyc), P = st.total;
    if (f >= 0.3 && f < 0.3 + erase) P = lerp(st.total, p0, (f - 0.3) / erase);
    else if (f >= 0.3 + erase && f < 0.55 + erase) P = p0;
    else if (f >= 0.55 + erase && f < 0.55 + erase + type) P = p0 + rate * (f - 0.55 - erase);
    return w.out < 0 ? P : Math.min(st.total, P + 3 * rate * w.out);
  },
  settle: function (st, w) {
    var P = this.progress(st, this.hold, { live: w.live, work: w.work, out: -1 });
    return [(st.total - P) / (st.total / 3.0 * 3)];
  },
  build: function (g) {
    var st = { lines: [] }, pos = 0;
    st.page = mk(g, 'g');
    mk(st.page, 'path', { class: 'srch-file', d: 'M13 6h17l5 5v31h-22z' });
    mk(st.page, 'path', { class: 'srch-line', d: 'M30 6v5h5', 'stroke-width': 0.6 });
    [[16.5, 26.5], [16.5, 31.5], [16.5, 30], [16.5, 31.5], [16.5, 28], [16.5, 31], [16.5, 24]].forEach(function (w, i) {
      var y = 15 + i * 3.6;
      st.lines.push({ x1: w[0], len: w[1] - w[0], y: y, a: pos, el: mk(g, 'line', { class: i ? 'srch-word' : 'srch-tok srch-tok--id', x1: w[0], y1: y, x2: w[0], y2: y, opacity: 0 }) });
      pos += w[1] - w[0] + 3;
    });
    st.total = pos;
    st.badge = mk(g, 'g', { transform: tr(32.5, 38.5, 0) });
    mk(st.badge, 'circle', { class: 'wk-accent', r: 3 });
    mk(st.badge, 'path', { class: 'wk-check', d: 'M-1.3 0.1L-0.3 1.1L1.4 -0.9' });
    st.logo = Logo(g);
    return st;
  },
  pen: function (st, P, t) {
    var li = 0;
    st.lines.forEach(function (ln, i) { if (ln.a <= P) li = i; });
    var ln = st.lines[li], x = ln.x1 + Math.min(ln.len, Math.max(0, P - ln.a));
    return [x, ln.y - 1.2 + 0.35 * Math.sin(t * 31)];
  },
  frame: function (st, t, w) {
    var mIn = ease(ph(t, 0.5, 1.3)), mOut = ease(ph(t, 5.8, 6.6)), m = mIn - mOut;
    var P = this.progress(st, t, w), cf = 1 - smooth(ph(t, 5.2, 5.7));
    set(st.page, { transform: 'translate(24 24) scale(' + fx(lerp(0.3, 1, m)) + ') translate(-24 -24)', opacity: m });
    st.lines.forEach(function (ln) { var s = Math.min(ln.len, Math.max(0, P - ln.a)); set(ln.el, { x2: ln.x1 + s, opacity: s > 0 ? cf : 0 }); });
    var bq = backOut(ph(t, 4.6, 5.0)) * (1 - smooth(ph(t, 5.3, 5.6)));
    set(st.badge, { transform: tr(32.5, 38.5, bq) });
    var start = this.pen(st, 0, w.live), end = this.pen(st, st.total, w.live), pen;
    if (t < 1.5) pen = { p: lerp2(SAT[0], start, mIn), r: lerp(4.5, 1.1, mIn) };
    else if (t < 4.5 || P < st.total - 0.01) pen = { p: this.pen(st, P, w.live), r: 1.1 };
    else pen = { p: lerp2([end[0], end[1] - 0.35 * Math.sin(w.live * 31)], SAT[0], mOut), r: lerp(1.1, 4.5, mOut) };
    pen.o = lerp(0.75, 1, m);
    var lr = { p: lerp2(SAT[1], [32.5, 38.5], m), r: 4.5 * (1 - m), o: 0.75 };
    var ll = { p: SAT[2], r: 4.5 * (1 - m), o: 0.75 };
    drawLogo(st.logo, [C, C], lerp(7, 9, m), 1 - m, [pen, lr, ll], fade(m));
  }
};

/* scenario: start, a step, two branches, finish. The same layout serves
   the build and the run. */
var FLOW = {
  N: [[24, 7], [24, 18], [13, 30], [35, 30], [24, 41]],
  E: [[[24, 9.8], [24, 15.25]], [[24, 20.75], [24, 23.5], [13, 23.5], [13, 27.25]], [[24, 20.75], [24, 23.5], [35, 23.5], [35, 27.25]], [[13, 32.75], [13, 36], [24, 36], [24, 38.2]], [[35, 32.75], [35, 36], [24, 36], [24, 38.2]]],
  STEP: [1, 3, 2]   // satellite i becomes node STEP[i]
};
function flowBuild(g, run) {
  var st = { edges: [], steps: [] };
  FLOW.E.forEach(function (pts) {
    var last = pts[pts.length - 1], prev = pts[pts.length - 2], ang = Math.atan2(last[1] - prev[1], last[0] - prev[0]) * 180 / Math.PI;
    st.edges.push({
      pts: pts,
      el: mk(g, 'path', { class: 'wk-edge-path', d: 'M' + pts.map(function (p) { return p.join(' '); }).join('L'), pathLength: 1, 'stroke-dasharray': '1 1', 'stroke-dashoffset': 1 }),
      head: mk(g, 'path', { class: 'wk-arrow', d: 'M0.2 0L-1.5 -1L-1.5 1Z', transform: 'translate(' + last[0] + ' ' + last[1] + ') rotate(' + fx(ang) + ')', opacity: 0 })
    });
  });
  st.ghost = mk(g, 'g', { transform: tr(24, 41), opacity: 0 });
  mk(st.ghost, 'circle', { class: 'wk-dash', r: 2.8, 'stroke-dasharray': '1.2 1' });
  mk(st.ghost, 'path', { class: 'srch-hot', d: 'M-1.2 0h2.4M0 -1.2v2.4', 'stroke-width': 0.6 });
  st.end = mk(g, 'g', { transform: tr(24, 41, 0) });
  mk(st.end, 'circle', { class: 'wk-end', r: 2.4 });
  mk(st.end, 'circle', { class: 'srch-core', r: 1.1 });
  st.endPulse = mk(g, 'circle', { class: 'srch-hot', cx: 24, cy: 41, r: 0, 'stroke-width': 0.6, opacity: 0 });
  st.logo = Logo(g);
  // steps are drawn over the satellites: a rect that starts as a circle
  [0, 1, 2].forEach(function () {
    var sg = mk(g, 'g');
    var r = mk(sg, 'rect', { class: 'srch-sat' });
    var ring = run ? mk(sg, 'rect', { class: 'wk-ring', pathLength: 1, 'stroke-dasharray': '0 1', opacity: 0 }) : null;
    var ls = mk(sg, 'g', { opacity: 0 });
    mk(ls, 'line', { class: 'srch-fline srch-fline--hit', x1: -3.4, y1: -0.8, x2: 2.4, y2: -0.8 });
    mk(ls, 'line', { class: 'srch-fline srch-fline--hit', x1: -3.4, y1: 1, x2: 0.6, y2: 1 });
    var ck = run ? mk(sg, 'path', { class: 'wk-check', d: 'M-1.6 0L-0.4 1.2L1.8 -1.1', opacity: 0 }) : null;
    st.steps.push({ g: sg, r: r, ring: ring, ls: ls, ck: ck });
  });
  st.tok = run ? mk(g, 'circle', { class: 'srch-hit', r: 1.3, opacity: 0 }) : null;
  return st;
}
function flowDraw(st, t, m, q, coreR) {
  drawLogo(st.logo, lerp2([C, C], FLOW.N[0], m), lerp(7, 2.8, m), 1, [0, 1, 2].map(function () { return { p: [0, 0], r: 0, o: 0 }; }), fade(m));
  st.steps.forEach(function (sp, i) {
    var p = lerp2(SAT[i], FLOW.N[FLOW.STEP[i]], m), w = lerp(9, 12, q), h = lerp(9, 5.5, q), rx = lerp(4.5, 1.4, q);
    set(sp.g, { transform: tr(p[0], p[1]) });
    set(sp.r, { x: -w / 2, y: -h / 2, width: w, height: h, rx: rx, opacity: lerp(0.75, 1, m) });
    if (sp.ring) set(sp.ring, { x: -w / 2 - 1.2, y: -h / 2 - 1.2, width: w + 2.4, height: h + 2.4, rx: rx + 1.2 });
  });
}
function edgeDraw(e, p, op) {
  set(e.el, { 'stroke-dashoffset': fx(1 - p), opacity: op === undefined ? 1 : op });
  set(e.head, { opacity: smooth((p - 0.85) / 0.15) * (op === undefined ? 1 : op) });
}

W.flowmake = {
  hold: 4.9,
  settle: function () { return [0.3]; },
  build: function (g) { return flowBuild(g, false); },
  frame: function (st, t, w) {
    var m = bell(t, 0.5, 1.4, 5.7, 6.6), q = bell(t, 1.1, 1.8, 5.3, 5.9);
    flowDraw(st, t, m, q);
    [0, 1, 2].forEach(function (i) { edgeDraw(st.edges[i], bell(t, 1.9 + i * 0.35, 2.4 + i * 0.35, 5.0, 5.4)); });
    [3, 4].forEach(function (i, j) { edgeDraw(st.edges[i], bell(t, 3.8 + j * 0.3, 4.3 + j * 0.3, 5.0, 5.4)); });
    set(st.ghost, { opacity: bell(t, 2.9, 3.2, 3.45, 3.7) });
    set(st.end, { transform: tr(24, 41, backOut(ph(t, 3.4, 3.8)) * (1 - ease(ph(t, 5.2, 5.6)))) });
    // while it works the steps fill in their settings one after another
    var a0 = amp(w), cyc = mod(w.work, 2.2);
    st.steps.forEach(function (sp, i) { set(sp.ls, { opacity: bell(t, 4.3 + i * 0.15, 4.6 + i * 0.15, 5.0, 5.3) * (1 - 0.6 * a0 * bump((cyc - i * 0.35) / 0.6)) }); });
  }
};

W.flowrun = {
  hold: 1.8,
  // where the run stands: the clip in the intro and the outro; round after
  // round while it works (1.8 to 5.7 s of the clip, the checks clear at the
  // end of each); once the step ends the round is finished at twice the pace
  round: function (st, t, w) {
    if (t > this.hold || (w.work <= 0 && w.out < 0)) return t;
    var u = 1.8 + mod(w.work, 3.9);
    return w.out < 0 ? u : Math.min(4.7, u + 2 * w.out);
  },
  settle: function (st, w) {
    var u = 1.8 + mod(w.work, 3.9);
    return u >= 4.7 ? [0, Math.min(u, 5.3)] : [(4.7 - u) / 2, 4.7];
  },
  build: function (g) { return flowBuild(g, true); },
  frame: function (st, t, w) {
    var m = bell(t, 0.5, 1.3, 6.0, 6.8), q = bell(t, 0.9, 1.4, 5.6, 6.1), on = bell(t, 1.2, 1.7, 5.4, 5.9);
    flowDraw(st, t, m, q);
    t = this.round(st, t, w);
    var sk = smooth(ph(t, 2.9, 3.2)) * (1 - smooth(ph(t, 5.3, 5.7)));
    st.edges.forEach(function (e, i) { edgeDraw(e, on, i === 1 || i === 3 ? 1 - 0.7 * sk : 1); });
    set(st.end, { transform: tr(24, 41, on * (1 + 0.25 * Math.sin(Math.PI * ph(t, 4.7, 5.1)))) });
    // the token: start, step, right branch, finish
    var legs = [[0, 1.8, 2.2], [2, 3.0, 3.5], [4, 4.3, 4.7]], tok = null;
    legs.forEach(function (lg) { if (t >= lg[1] && t <= lg[2]) tok = along(st.edges[lg[0]].pts, ease(ph(t, lg[1], lg[2]))); });
    if (tok) set(st.tok, { cx: tok[0], cy: tok[1], opacity: 1 }); else set(st.tok, { opacity: 0 });
    var work = [[2.2, 2.9], [3.5, 4.2], null];   // steps follow the satellites (FLOW.STEP): 0 is the first node, 1 the right branch, 2 the skipped left one
    st.steps.forEach(function (sp, i) {
      var w = work[i];
      set(sp.ls, { opacity: on * (w ? 1 - smooth(ph(t, w[1], w[1] + 0.15)) : 1) });
      set(sp.g, { opacity: i === 2 ? 1 - 0.65 * sk : 1 });
      if (!w) { set(sp.ring, { opacity: 0 }); set(sp.ck, { opacity: 0 }); return; }
      var p = ph(t, w[0], w[1]), done = smooth(ph(t, w[1], w[1] + 0.2)) * (1 - smooth(ph(t, 5.3, 5.7)));
      set(sp.ring, { 'stroke-dasharray': fx(p) + ' 1', opacity: (t > w[0] ? 1 : 0) * (1 - smooth(ph(t, w[1], w[1] + 0.2))) });
      set(sp.ck, { opacity: done });
    });
    var pq = ph(t, 4.7, 5.4);
    set(st.endPulse, { r: 2.4 + 5 * pq, opacity: pq > 0 && pq < 1 ? 1 - pq : 0 });
  }
};

/* team: the core is the lead, members sit on a circle inside the team's
   border. Satellites take three seats, two more agents join. */
var SEAT = [-90, -18, 54, 126, 198], SATSEAT = [0, 2, 3];   // seats the satellites take
var FROM = SEAT.map(function (a, i) { var k = SATSEAT.indexOf(i); return k < 0 ? null : ANG[k]; });
function teamBuild(g, run) {
  var st = {};
  st.border = mk(g, 'circle', { class: 'wk-dash', cx: C, cy: C, r: 20.5, 'stroke-dasharray': '1.6 1.4', opacity: 0 });
  st.spokes = SEAT.map(function () { return mk(g, 'line', { class: 'srch-handle', 'stroke-width': 2 }); });
  st.badge = mk(g, 'circle', { class: 'srch-hot', cx: C, cy: C, r: 0, 'stroke-width': 0.6, opacity: 0 });
  st.lead = mk(g, 'circle', { class: 'srch-core', cx: C, cy: C });
  st.rings = SEAT.map(function () { return mk(g, 'circle', { class: 'wk-ring', r: 5, pathLength: 1, 'stroke-dasharray': '0 1', opacity: 0 }); });
  st.members = SEAT.map(function () { return mk(g, 'circle', { class: 'srch-sat' }); });
  st.pk = run ? SEAT.map(function () { return mk(g, 'circle', { class: 'srch-hit', r: 1, opacity: 0 }); }) : null;
  st.msg = run ? [0, 1].map(function () { return mk(g, 'circle', { class: 'srch-dot', r: 0.9, opacity: 0 }); }) : null;
  return st;
}
// seat i: where it is, how big, how visible, how far its spoke is drawn
function teamDraw(st, m, seats) {
  var lr = lerp(7, 5.5, m);
  set(st.lead, { r: lr });
  seats.forEach(function (s, i) {
    var p = polar(s.a, s.r);
    set(st.members[i], { cx: p[0], cy: p[1], r: Math.max(0, s.size), opacity: s.o });
    var a = lerp(7.5, lr + 1, m), b = lerp(13.5, s.r - s.size - 1, m), e = a + (b - a) * s.sp;
    var p1 = polar(s.a, a), p2 = polar(s.a, e), k = SATSEAT.indexOf(i);
    // a satellite's spoke starts as the logo's own
    if (k >= 0) { var q = SPOKE[k]; p1 = lerp2([q[0], q[1]], p1, m); p2 = lerp2([q[2], q[3]], p2, m); }
    set(st.spokes[i], { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], opacity: 0.55 * s.so * Math.min(1, s.sp * 3), 'stroke-width': lerp(2.5, 1.6, m) });
    set(st.rings[i], { cx: p[0], cy: p[1] });
  });
}
function satSeat(i, m) { return { a: lerp(FROM[i], SEAT[i], m), r: lerp(RS[SATSEAT.indexOf(i)], 15, m), size: lerp(4.5, 3.4, m), o: lerp(0.75, 1, m), sp: 1, so: 1 }; }

W.teammake = {
  hold: 4.3,
  settle: function () { return [0.3]; },
  build: function (g) { return teamBuild(g, false); },
  frame: function (st, t, w) {
    var m = bell(t, 0.5, 1.4, 5.8, 6.7);
    var seats = SEAT.map(function (a, i) {
      if (FROM[i] !== null) return satSeat(i, m);
      var k = i === 1 ? 0 : 1, e = bell(t, 1.8 + k * 0.8, 2.6 + k * 0.8, 5.0 - k * 0.2, 5.6 - k * 0.2), sp = bell(t, 2.5 + k * 0.8, 2.9 + k * 0.8, 4.9 - k * 0.2, 5.2 - k * 0.2);
      return { a: a, r: lerp(32, 15, e), size: 3.4 * Math.min(1, e * 1.5), o: e, sp: sp, so: 1 };
    });
    // while it works the members sway on their seats
    var a0 = amp(w);
    seats.forEach(function (s, i) { s.r += a0 * 0.8 * Math.sin(w.live * 1.6 + i * 1.3); });
    teamDraw(st, m, seats);
    var b = bell(t, 1.2, 1.8, 5.5, 6.0), solid = bell(t, 3.8, 4.3, 5.2, 5.5);
    set(st.border, { opacity: b, 'stroke-dasharray': '1.6 ' + fx(1.4 * (1 - solid) + 0.001) });
    var pq = ph(t, 4.3, 5.0);
    set(st.badge, { r: 6 + 4 * pq, opacity: pq > 0 && pq < 1 ? 1 - pq : 0 });
  }
};

W.teamrun = {
  hold: 2.3,
  outFrom: 3.0,
  settle: function () { return [0.35]; },
  build: function (g) { return teamBuild(g, true); },
  frame: function (st, t, w) {
    var m = bell(t, 0.5, 1.3, 5.9, 6.7), join = bell(t, 0.9, 1.5, 5.6, 6.1);
    var DUR = [1.2, 1.6, 1.0, 1.8, 1.4], T0 = 2.3;
    var seats = SEAT.map(function (a, i) {
      if (FROM[i] !== null) return satSeat(i, m);
      return { a: a, r: 15, size: 3.4 * join, o: join, sp: join, so: 1 };
    });
    teamDraw(st, m, seats);
    set(st.border, { opacity: bell(t, 1.0, 1.5, 5.6, 6.0), 'stroke-dasharray': '1.6 0.001' });
    var last = 0;
    // while it works: each member fills its ring, sends the result to the
    // lead and gets the next task; once the step ends the rings run into
    // where the clip has them at outFrom
    if (t <= this.hold && (w.work > 0 || w.out >= 0)) {
      var r0 = 6.5, r1 = 15 - 3.4 - 0.6, k = w.out < 0 ? 0 : smooth(w.out / 0.35);
      SEAT.forEach(function (a, i) {
        var P = DUR[i] + 0.9, f = mod(w.work, P), p = ph(f, 0, DUR[i]), pos = null;
        var target = ph(3.0, T0, T0 + DUR[i]);
        set(st.rings[i], { 'stroke-dasharray': fx(lerp(p, target, k)) + ' 1', opacity: lerp(1 - smooth(ph(f, DUR[i], DUR[i] + 0.2)), 1, k) });
        if (f >= DUR[i] && f < DUR[i] + 0.4) pos = polar(a, lerp(r1, r0, ease(ph(f, DUR[i], DUR[i] + 0.4))));
        else if (f >= DUR[i] + 0.45 && f < DUR[i] + 0.85) pos = polar(a, lerp(r0, r1, ease(ph(f, DUR[i] + 0.45, DUR[i] + 0.85))));
        if (pos) set(st.pk[i], { cx: pos[0], cy: pos[1], opacity: 1 - k }); else set(st.pk[i], { opacity: 0 });
      });
      [[1, 2], [4, 0]].forEach(function (mv, j) {
        var f = mod(w.work - 0.6 - j * 1.1, 2.4);
        if (f >= 0.5) { set(st.msg[j], { opacity: 0 }); return; }
        var p = lerp2(polar(SEAT[mv[0]], 15), polar(SEAT[mv[1]], 15), ease(f / 0.5));
        set(st.msg[j], { cx: p[0], cy: p[1], opacity: 1 - k });
      });
      set(st.badge, { opacity: 0 });
      return;
    }
    SEAT.forEach(function (a, i) {
      var end = T0 + DUR[i], back = [end + 0.1, end + 0.5], out = ph(t, 1.8, 2.3);
      var p = ph(t, T0, end), ringOn = t > T0 && t < back[1] + 0.2 ? 1 : 0;
      set(st.rings[i], { 'stroke-dasharray': fx(p) + ' 1', opacity: ringOn * (1 - smooth(ph(t, back[0], back[1]))) });
      // the task goes out to everyone at once, each result comes back when ready
      var r0 = 6.5, r1 = 15 - 3.4 - 0.6, pos = null;
      if (t >= 1.8 && t <= 2.3) pos = polar(a, lerp(r0, r1, ease(out)));
      else if (t >= back[0] && t <= back[1]) pos = polar(a, lerp(r1, r0, ease(ph(t, back[0], back[1]))));
      if (pos) set(st.pk[i], { cx: pos[0], cy: pos[1], opacity: 1 }); else set(st.pk[i], { opacity: 0 });
      last = Math.max(last, back[1]);
    });
    // neighbours talk while they work
    [[1, 2, 2.8, 3.3], [4, 0, 3.1, 3.6]].forEach(function (mv, j) {
      var on = t >= mv[2] && t <= mv[3];
      if (!on) { set(st.msg[j], { opacity: 0 }); return; }
      var p = lerp2(polar(SEAT[mv[0]], 15), polar(SEAT[mv[1]], 15), ease(ph(t, mv[2], mv[3])));
      set(st.msg[j], { cx: p[0], cy: p[1], opacity: 1 });
    });
    var pq = ph(t, last, last + 0.7);
    set(st.badge, { r: 6 + 6 * pq, opacity: pq > 0 && pq < 1 ? 1 - pq : 0 });
  }
};

/* new agent: the mark steps aside, a blueprint of a new one appears next
   to it, parcels run along the link and each one fills a part of it */
W.agentmake = {
  hold: 4.3,
  outFrom: 4.8,
  settle: function () { return [0.3]; },
  build: function (g) {
    var st = {}, NC = [35, 24], S = 0.55;
    st.NC = NC; st.S = S;
    st.link = mk(g, 'line', { class: 'wk-dash', y1: C, y2: C, 'stroke-dasharray': '1.2 1', opacity: 0 });
    st.hub = mk(g, 'g');
    st.logo = Logo(st.hub);
    st.bp = mk(g, 'g', { opacity: 0 });
    var sp = DIR.map(function (d) { return [NC[0] + d[0] * 7.5 * S, NC[1] + d[1] * 7.5 * S, NC[0] + d[0] * 13.5 * S, NC[1] + d[1] * 13.5 * S]; });
    sp.forEach(function (s) { mk(st.bp, 'line', { class: 'wk-dash', x1: s[0], y1: s[1], x2: s[2], y2: s[3], 'stroke-dasharray': '1 0.8' }); });
    mk(st.bp, 'circle', { class: 'wk-dash', cx: NC[0], cy: NC[1], r: 7 * S, 'stroke-dasharray': '1.2 1' });
    DIR.forEach(function (d) { mk(st.bp, 'circle', { class: 'wk-dash', cx: NC[0] + d[0] * 18 * S, cy: NC[1] + d[1] * 18 * S, r: 4.5 * S, 'stroke-dasharray': '1 0.8' }); });
    st.fill = mk(g, 'g');
    st.fSpokes = sp.map(function (s) { return mk(st.fill, 'line', { class: 'srch-handle', x1: s[0], y1: s[1], x2: s[0], y2: s[1], 'stroke-width': 2.5 * S, opacity: 0.55 }); });
    st.sp = sp;
    st.fCore = mk(st.fill, 'circle', { class: 'srch-core', cx: NC[0], cy: NC[1], r: 0 });
    st.fSats = DIR.map(function (d) { return mk(st.fill, 'circle', { class: 'srch-sat', cx: NC[0] + d[0] * 18 * S, cy: NC[1] + d[1] * 18 * S, r: 0, opacity: 0.75 }); });
    st.pk = [0, 1, 2, 3, 4].map(function () { return mk(g, 'circle', { class: 'srch-hit', r: 0.9, opacity: 0 }); });
    st.pulse = mk(g, 'circle', { class: 'srch-hot', cx: NC[0], cy: NC[1], r: 0, 'stroke-width': 0.6, opacity: 0 });
    return st;
  },
  frame: function (st, t, w) {
    var mv = bell(t, 0.5, 1.3, 5.6, 6.5), hx = lerp(C, 13, mv), hs = lerp(1, st.S, mv);
    set(st.hub, { transform: 'translate(' + fx(hx) + ' 24) scale(' + fx(hs) + ') translate(-24 -24)' });
    drawLogo(st.logo, [C, C], 7, 1, SAT.map(function (p) { return { p: p, r: 4.5, o: 0.75 }; }), [1, 1, 1]);
    var bpo = bell(t, 1.2, 1.7, 5.0, 5.4);
    set(st.bp, { opacity: bpo });
    var x0 = hx + 7 * hs + 0.8, x1 = st.NC[0] - 7 * st.S - 0.8, solid = bell(t, 4.0, 4.4, 5.0, 5.4);
    set(st.link, { x1: x0, x2: x1, opacity: bpo, 'stroke-dasharray': '1.2 ' + fx(1 * (1 - solid) + 0.001) });
    // five parcels: the core, the spokes, then each satellite
    var ARR = [2.3, 2.7, 3.1, 3.5, 3.9];
    ARR.forEach(function (a, i) {
      var go = a - 0.4;
      if (t >= go && t <= a) { set(st.pk[i], { cx: lerp(x0, x1, ease(ph(t, go, a))), cy: C, opacity: 1 }); } else set(st.pk[i], { opacity: 0 });
    });
    var gone = 1 - ease(ph(t, 5.0, 5.4));
    set(st.fCore, { r: 7 * st.S * backOut(ph(t, ARR[0], ARR[0] + 0.35)) * gone });
    var sq = ease(ph(t, ARR[1], ARR[1] + 0.35)) * gone;
    st.fSpokes.forEach(function (l, i) { var s = st.sp[i]; set(l, { x2: lerp(s[0], s[2], sq), y2: lerp(s[1], s[3], sq), opacity: sq > 0.01 ? 0.55 : 0 }); });
    st.fSats.forEach(function (c, i) { set(c, { r: 4.5 * st.S * backOut(ph(t, ARR[2 + i], ARR[2 + i] + 0.35)) * gone }); });
    var pq = ph(t, 4.1, 4.8);
    // while it works more parcels run along the link and the new agent
    // pulses with each of them
    if (t <= this.hold && (w.work > 0 || w.out >= 0)) {
      var a0 = amp(w), f = mod(w.work, 0.9), g0 = mod(w.work, 1.6) / 0.7;
      set(st.pk[0], f < 0.45 ? { cx: lerp(x0, x1, ease(f / 0.45)), cy: C, opacity: a0 } : { opacity: 0 });
      set(st.pulse, { r: 4 + 7 * Math.min(1, g0), opacity: g0 < 1 ? (1 - g0) * a0 : 0 });
      return;
    }
    set(st.pulse, { r: 4 + 7 * pq, opacity: pq > 0 && pq < 1 ? 1 - pq : 0 });
  }
};

/* delegate: the core packs a task and sends it down one spoke; that agent
   grows helpers and works inside a progress ring, then returns a result */
W.delegate = {
  hold: 2.6,
  outFrom: 3.9,
  settle: function () { return [0.35]; },
  build: function (g) {
    var st = {};
    st.ring = mk(g, 'circle', { class: 'wk-ring', cx: SAT[1][0], cy: SAT[1][1], r: 7.2, pathLength: 1, 'stroke-dasharray': '0 1', transform: 'rotate(-90 ' + SAT[1][0] + ' ' + SAT[1][1] + ')', opacity: 0 });
    st.hl = [0, 1].map(function () { return mk(g, 'line', { class: 'srch-handle', 'stroke-width': 0.9, opacity: 0.55 }); });
    st.helpers = [0, 1].map(function () { return mk(g, 'circle', { class: 'srch-dot', r: 0 }); });
    st.logo = Logo(g);
    st.task = mk(g, 'g', { transform: tr(C, C, 0) });
    mk(st.task, 'rect', { class: 'wk-accent', x: -1.6, y: -1.6, width: 3.2, height: 3.2, rx: 0.7 });
    mk(st.task, 'path', { class: 'wk-check', d: 'M-0.9 -0.5h1.8M-0.9 0.6h1.1', 'stroke-width': 0.6 });
    st.res = mk(g, 'g', { transform: tr(SAT[1][0], SAT[1][1], 0) });
    mk(st.res, 'circle', { class: 'wk-accent', r: 1.9 });
    mk(st.res, 'path', { class: 'wk-check', d: 'M-0.9 0L-0.25 0.7L1 -0.6', 'stroke-width': 0.6 });
    st.pulse = mk(g, 'circle', { class: 'srch-hot', cx: C, cy: C, r: 0, 'stroke-width': 0.6, opacity: 0 });
    return st;
  },
  frame: function (st, t, w) {
    var d = DIR[1], S = SAT[1], quiet = bell(t, 1.0, 1.4, 5.4, 5.9), work = bell(t, 1.9, 2.3, 4.3, 4.7);
    var pr = ph(t, 2.1, 4.2), flash = Math.sin(Math.PI * ph(t, 4.2, 4.5));
    drawLogo(st.logo, [C, C], 7 * (1 + 0.12 * Math.sin(Math.PI * ph(t, 5.2, 5.7))), 1, [
      { p: SAT[0], r: 4.5, o: 0.75 * (1 - 0.6 * quiet) },
      { p: S, r: lerp(4.5, 3.4, work) + 0.8 * flash, o: 0.75 + 0.25 * work },
      { p: SAT[2], r: 4.5, o: 0.75 * (1 - 0.6 * quiet) }
    ], [1 - 0.6 * quiet, 1, 1 - 0.6 * quiet]);
    // while it works the ring spins as a part of a circle; once the step
    // ends it turns back to the top and fills to where the clip has it
    var spin = -90, dash = pr, S1 = SAT[1];
    if (t <= this.hold && (w.work > 0 || w.out >= 0)) {
      var a1 = -90 + 360 * 0.8 * w.work, k = w.out < 0 ? 0 : smooth(w.out / 0.35);
      spin = lerp(a1, -90 + 360 * Math.ceil((a1 + 90) / 360), k);
      dash = lerp(ph(this.hold, 2.1, 4.2), ph(this.outFrom, 2.1, 4.2), k);
    }
    set(st.ring, { 'stroke-dasharray': fx(dash) + ' 1', opacity: bell(t, 1.9, 2.1, 4.4, 4.8), transform: 'rotate(' + fx(spin) + ' ' + S1[0] + ' ' + S1[1] + ')' });
    st.helpers.forEach(function (h, i) {
      var a = w.live * 2.6 + i * Math.PI, R = 5.2 * work, x = S[0] + Math.cos(a) * R, y = S[1] + Math.sin(a) * R;
      set(h, { cx: x, cy: y, r: 1.1 * work });
      set(st.hl[i], { x1: S[0] + Math.cos(a) * 3.6, y1: S[1] + Math.sin(a) * 3.6, x2: S[0] + Math.cos(a) * Math.max(3.6, R - 1.4), y2: S[1] + Math.sin(a) * Math.max(3.6, R - 1.4), opacity: work > 0.3 ? 0.55 * work : 0 });
    });
    // the task: pops out of the core and runs down the spoke
    var tp = null, ts = 0;
    if (t >= 1.0 && t < 1.3) { tp = [C, C]; ts = backOut(ph(t, 1.0, 1.3)); }
    else if (t >= 1.3 && t <= 1.9) { var u = ease(ph(t, 1.3, 1.9)); tp = [C + d[0] * lerp(0, 18 - 4.5, u), C + d[1] * lerp(0, 18 - 4.5, u)]; ts = 1 - 0.3 * smooth(ph(t, 1.75, 1.9)); }
    set(st.task, { transform: tp ? tr(tp[0], tp[1], ts) : tr(C, C, 0) });
    // the result comes back with a tick
    var rp = null;
    if (t >= 4.5 && t <= 5.2) { var v = ease(ph(t, 4.6, 5.2)); rp = [S[0] - d[0] * lerp(0, 18 - 7, v), S[1] - d[1] * lerp(0, 18 - 7, v)]; }
    set(st.res, { transform: rp ? tr(rp[0], rp[1], backOut(ph(t, 4.5, 4.7)) * (1 - smooth(ph(t, 5.1, 5.2)))) : tr(S[0], S[1], 0) });
    var pq = ph(t, 5.2, 5.9);
    set(st.pulse, { r: 7 + 6 * pq, opacity: pq > 0 && pq < 1 ? 1 - pq : 0 });
  }
};


/**
 * Mount the work scene `kind` into `g`. update(dt, next) moves it on by dt
 * seconds: while `next` is this kind it works; otherwise it settles, plays
 * the finale and folds, and returns 'done' once it is the mark again.
 */
export function mountWork(g, kind) {
  var sc = W[kind];
  if (!sc) return null;
  var st = sc.build(g);
  var s = { phase: 'intro', t: 0.45, live: 0, work: 0, out: -1, wait: 0, from: sc.hold };
  function w() { return { live: s.live, work: s.work, out: s.out }; }
  function leave() {
    var r = sc.settle ? sc.settle(st, w()) : null;
    s.out = 0;
    s.wait = r ? r[0] : 0;
    s.from = r && r[1] != null ? r[1] : (sc.outFrom != null ? sc.outFrom : sc.hold);
    s.phase = 'settle';
  }
  function update(dt, next) {
    var leaving = next !== kind;
    s.live += dt;
    if (s.phase === 'intro') {
      // a step that ends before the scene has formed hurries it along
      s.t = Math.min(sc.hold, s.t + dt * (leaving ? 3 : 1));
      if (s.t >= sc.hold) { if (leaving) leave(); else s.phase = 'work'; }
    } else if (s.phase === 'work') {
      s.work += dt;
      if (leaving) leave();
    } else if (s.phase === 'settle') {
      s.out += dt;
      if (s.out >= s.wait) { s.phase = 'outro'; s.t = s.from; }
    } else {
      s.out += dt;
      s.t += dt * EXIT_SPEED;
      if (s.t >= 6.9) { sc.frame(st, 6.9, w()); return 'done'; }
    }
    sc.frame(st, s.t, w());
    return 'running';
  }
  sc.frame(st, s.t, w());
  return {
    accepts: function (name) { return name === kind; },
    update: update,
    get phase() { return s.phase; },
    destroy: function () { while (g.firstChild) g.removeChild(g.firstChild); },
  };
}

export const WORK_KINDS = Object.keys(W);
