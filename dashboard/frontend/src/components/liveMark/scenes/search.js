/*
 * The search scene: the mark turns into a magnifier and looks for something
 * until the answer comes. Drawn after the "Orbit Loader" artifact: the core
 * opens into the lens, the lower right spoke grows into the handle and its
 * satellite becomes the handle's end, the other two fade with the mark.
 *
 * What the lens looks at is a field: memory, the web, files, a page, code, a
 * knowledge base, a table, tools, mail, run history, error charts. A field
 * is built twice: under the glass, where it is cut away by a mask so the real
 * background shows through, and in the lens, magnified and clipped to it;
 * the glass itself has no fill.
 *
 * Unlike the artifact's 7.6 s clips, nothing here runs on a fixed clock:
 *  - scan: the lens goes over the field for as long as the search lasts, on
 *    a closed path, and every reaction of the field is cyclic (dimmed files
 *    recover, scrolls wrap round), so it can go on indefinitely;
 *  - find: when the step ends the lens glides to the find and the field
 *    shows it (a marker, a pulse, the opened letter);
 *  - then either the next search takes over (the field fades into the next
 *    one and the lens leaves the find to scan it, the mark never comes back)
 *    or the lens returns to the centre and folds back into the mark.
 *
 * Each field: build(g, defs, id) → st; scan(st, c) → lens position at scan
 * time c; target(st, k) → where the find is; draw(st, k); optional
 * onFind(st, k) to fix what the find depends on at the moment it starts.
 * k = { c, L, m, fnd, since, findC }: scan clock, lens position, how far
 * the mark has become a lens, how far the find has landed (0..1), seconds
 * since it landed (negative before), and the scan clock when it started.
 */

var NS = 'http://www.w3.org/2000/svg';
var C = 24, RL = 8.5, MAG = 1.5;
// public/logo.svg to the decimal (as LOGO_SATS and LOGO_SPOKES in poses.js),
// so the lens opens out of the very mark LiveMark draws and folds back into it
var SAT = [[24, 6], [40, 34], [8, 34]];   // top, lower right (the handle), lower left
var SPOKE = [[24, 16.5, 24, 10.5], [29.6, 28.2, 35.6, 31.6], [18.4, 28.2, 12.4, 31.6]];   // inner end, outer end
var RS = SAT.map(function (p) { return Math.hypot(p[0] - C, p[1] - C); });
var DIR = SAT.map(function (p, i) { return [(p[0] - C) / RS[i], (p[1] - C) / RS[i]]; });
var uid = 0;
function set(e, a) { for (var k in a) e.setAttribute(k, typeof a[k] === 'number' ? Math.round(a[k] * 1000) / 1000 : a[k]); return e; }
function mk(parent, tag, a) { var e = document.createElementNS(NS, tag); if (a) set(e, a); parent.appendChild(e); return e; }
function clamp(u) { return u < 0 ? 0 : u > 1 ? 1 : u; }
function ph(t, a, b) { return clamp((t - a) / (b - a)); }
function ease(u) { u = clamp(u); return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2; }
function smooth(u) { u = clamp(u); return u * u * (3 - 2 * u); }
function lerp(a, b, u) { return a + (b - a) * u; }
function lerp2(p, q, u) { return [lerp(p[0], q[0], u), lerp(p[1], q[1], u)]; }
function rnd(seed) { var x = seed; return function () { x = (x * 16807) % 2147483647; return (x - 1) / 2147483646; }; }
function fx(n) { return Math.round(n * 100) / 100; }
function mod(a, n) { return ((a % n) + n) % n; }
// a closed Catmull-Rom loop through the points; u wraps, one lap per unit
function loop(P, u) {
  var n = P.length, x = mod(u, 1) * n, i = Math.floor(x), f = x - i;
  var a = P[(i - 1 + n) % n], b = P[i % n], c = P[(i + 1) % n], d = P[(i + 2) % n];
  function k(a, b, c, d) { return 0.5 * (2 * b + (c - a) * f + (2 * a - 5 * b + 4 * c - d) * f * f + (3 * b - a - 3 * c + d) * f * f * f); }
  return [k(a[0], b[0], c[0], d[0]), k(a[1], b[1], c[1], d[1])];
}
// rest on each stop for `rest` of the hop, then glide to the next; wraps round
function hopLoop(P, c, hop, rest) {
  var n = P.length, x = c / hop, i = Math.floor(x), f = x - i;
  return lerp2(P[mod(i, n)], P[mod(i + 1, n)], ease((f - rest) / (1 - rest)));
}
// seconds since the lens last arrived at stop j of a hopLoop (Infinity before the first visit)
function sinceVisit(j, c, n, hop) {
  var t0 = j * hop;
  return c < t0 ? Infinity : mod(c - t0, n * hop);
}
function pulses(list, p, since, fnd, r0, grow, period) {
  list.forEach(function (el, i) {
    var q = since / period - i * 0.5;
    if (q < 0 || fnd <= 0) { set(el, { opacity: 0 }); return; }
    q = q % 1; set(el, { cx: p[0], cy: p[1], r: r0 + grow * q, opacity: (1 - q) * fnd });
  });
}

var F = {};

/* memory: an associative cloud; nodes near the lens wake up, the found
   one sends a wave along its links */
F.memory = {
  build: function (g) {
    var N = [[12, 11], [22, 8], [34, 10], [42, 19], [6, 21], [18, 18], [30, 19], [38, 29], [13, 29], [23, 32], [33, 38], [19, 41], [7, 37], [42, 41], [27, 26]];
    var TG = 10, E = [], seen = {};
    N.forEach(function (p, i) {
      N.map(function (q, j) { return { j: j, d: Math.hypot(p[0] - q[0], p[1] - q[1]) }; })
        .filter(function (o) { return o.j !== i; })
        .sort(function (a, b) { return a.d - b.d; })
        .slice(0, 2)
        .forEach(function (o) { var key = Math.min(i, o.j) + ':' + Math.max(i, o.j); if (!seen[key]) { seen[key] = 1; E.push([i, o.j]); } });
    });
    var depth = N.map(function () { return 99; }); depth[TG] = 0;
    for (var pass = 0; pass < 5; pass++) E.forEach(function (e) { depth[e[0]] = Math.min(depth[e[0]], depth[e[1]] + 1); depth[e[1]] = Math.min(depth[e[1]], depth[e[0]] + 1); });
    var st = { N: N, E: E, depth: depth, TG: TG };
    E.forEach(function (e) { mk(g, 'line', { class: 'srch-line', x1: N[e[0]][0], y1: N[e[0]][1], x2: N[e[1]][0], y2: N[e[1]][1] }); });
    st.hot = E.map(function (e) { return mk(g, 'line', { class: 'srch-hot', 'stroke-width': 0.9, opacity: 0, x1: N[e[0]][0], y1: N[e[0]][1], x2: N[e[1]][0], y2: N[e[1]][1] }); });
    st.pulse = [0, 1].map(function () { return mk(g, 'circle', { class: 'srch-hot', 'stroke-width': 0.6, r: 0, opacity: 0 }); });
    st.dots = N.map(function (p) { return mk(g, 'circle', { class: 'srch-dot', cx: p[0], cy: p[1], r: 1.3 }); });
    st.hit = mk(g, 'circle', { class: 'srch-hit', cx: N[TG][0], cy: N[TG][1], r: 0 });
    st.path = [[14, 14], [33, 13], [38, 27], [24, 33], [11, 29]];
    return st;
  },
  scan: function (st, c) { return loop(st.path, c / 6.5); },
  target: function (st) { return st.N[st.TG]; },
  draw: function (st, k) {
    st.dots.forEach(function (d, i) {
      var p = st.N[i];
      var near = clamp(1 - (Math.hypot(p[0] - k.L[0], p[1] - k.L[1]) - 3) / 9) * k.m;
      var tw = 0.55 + 0.25 * Math.sin(k.c * 1.9 + i * 2.3);
      var lit = st.depth[i] <= 2 ? smooth((k.since - st.depth[i] * 0.25) / 0.3) : 0;
      set(d, { 'fill-opacity': Math.min(1, tw + 0.45 * near + 0.45 * lit), r: 1.3 + 0.5 * near + 0.35 * lit });
    });
    st.hot.forEach(function (h, j) {
      var e = st.E[j], dd = Math.max(st.depth[e[0]], st.depth[e[1]]);
      set(h, { opacity: dd <= 2 ? smooth((k.since - (dd - 1) * 0.25) / 0.3) : 0 });
    });
    pulses(st.pulse, st.N[st.TG], k.since, k.fnd, 2.4, 6, 1.1);
    set(st.hit, { r: 2.4 * k.fnd });
  }
};

/* web: a wire globe turns under the lens for as long as the search lasts,
   then slows down and stops; the find is the site that ends up in front */
var WEB_SPIN = 0.8, WEB_STOP = 1.4;
F.web = {
  build: function (g) {
    var R = 15, st = { R: R, TG: 0 };
    mk(g, 'circle', { class: 'srch-line', cx: C, cy: C, r: R, 'stroke-width': 0.9 });
    [0, 0.52, -0.52].forEach(function (la) { var w = R * Math.cos(la), y = C - R * Math.sin(la); mk(g, 'line', { class: 'srch-line', x1: C - w, y1: y, x2: C + w, y2: y }); });
    st.mer = [0, 1, 2].map(function () { return mk(g, 'ellipse', { class: 'srch-line', cx: C, cy: C, rx: 0, ry: R }); });
    st.sites = [[-0.5, 0.3], [0.85, -0.25], [2.0, 0.55], [2.9, -0.5], [4.1, 0.1], [5.25, -0.4], [1.5, 0.75], [3.6, -0.15]];
    st.arcs = [0, 1].map(function () { return mk(g, 'path', { class: 'srch-hot', 'stroke-width': 0.7, 'stroke-dasharray': '1.2 1', opacity: 0 }); });
    st.pulse = [0, 1].map(function () { return mk(g, 'circle', { class: 'srch-hot', 'stroke-width': 0.6, r: 0, opacity: 0 }); });
    st.dots = st.sites.map(function () { return mk(g, 'circle', { class: 'srch-dot', r: 1.2 }); });
    st.hit = mk(g, 'circle', { class: 'srch-hit', r: 0 });
    st.path = [[32, 16], [15, 27], [31, 31], [17, 15]];
    return st;
  },
  // even turning while it searches, a steady slow-down to a stop once found
  rot: function (k) {
    if (k.findC == null) return -WEB_SPIN * k.c;
    var u = Math.min(k.c - k.findC, WEB_STOP);
    return -WEB_SPIN * (k.findC + u - u * u / (2 * WEB_STOP));
  },
  site: function (st, i, rot) { var s = st.sites[i], a = s[0] + rot, c = Math.cos(s[1]); return [C + st.R * c * Math.sin(a), C - st.R * Math.sin(s[1]), c * Math.cos(a)]; },
  onFind: function (st, k) {
    var end = -WEB_SPIN * (k.c + WEB_STOP / 2), best = -2, self = this;
    st.sites.forEach(function (s, i) { var z = self.site(st, i, end)[2]; if (Math.abs(s[1]) < 0.6 && z > best) { best = z; st.TG = i; } });
  },
  scan: function (st, c) { return loop(st.path, c / 5); },
  target: function (st, k) { var p = this.site(st, st.TG, this.rot(k)); return [p[0], p[1]]; },
  draw: function (st, k) {
    var self = this, rot = this.rot(k);
    st.mer.forEach(function (e, i) { set(e, { rx: st.R * Math.abs(Math.sin(rot + 0.3 + i * Math.PI / 3)) }); });
    var P = st.sites.map(function (s, i) { return self.site(st, i, rot); });
    st.dots.forEach(function (d, i) { set(d, { cx: P[i][0], cy: P[i][1], opacity: clamp(P[i][2] * 4) }); });
    var p = P[st.TG];
    set(st.hit, { cx: p[0], cy: p[1], r: 2 * k.fnd });
    pulses(st.pulse, p, k.since, k.fnd, 2, 7, 1.2);
    [(st.TG + 1) % 8, (st.TG + 5) % 8].forEach(function (j, i) {
      var q = P[j], mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2, dx = mx - C, dy = my - C, l = Math.hypot(dx, dy) || 1;
      set(st.arcs[i], {
        d: 'M' + fx(p[0]) + ' ' + fx(p[1]) + 'Q' + fx(mx + dx / l * 7) + ' ' + fx(my + dy / l * 7) + ' ' + fx(q[0]) + ' ' + fx(q[1]),
        opacity: smooth((k.since - 0.3 - i * 0.25) / 0.4) * clamp(q[2] * 4)
      });
    });
  }
};

/* files: a grid of files; the lens hops from file to file, a checked one
   dims and recovers before its next turn, the found one fills */
var FILE_HOP = 0.8, FILE_REST = 0.45;
F.files = {
  build: function (g) {
    var st = { files: [] }, xs = [10.25, 24, 37.75], ys = [9, 24, 39];
    var body = 'M-4.25 -5.5h5.9l2.6 2.6v8.4h-8.5z', rows = [[-1.4, 2.6], [0.7, 1.4], [2.8, 2.2]];
    for (var r = 0; r < 3; r++) for (var c = 0; c < 3; c++) {
      var fg = mk(g, 'g', { transform: 'translate(' + xs[c] + ' ' + ys[r] + ')' });
      mk(fg, 'path', { class: 'srch-file', d: body });
      mk(fg, 'path', { class: 'srch-line', d: 'M1.65 -5.5v2.6h2.6', 'stroke-width': 0.6 });
      rows.forEach(function (l) { mk(fg, 'line', { class: 'srch-fline', x1: -2.6, y1: l[0], x2: l[1], y2: l[0] }); });
      var hit = mk(fg, 'g', { opacity: 0 });
      mk(hit, 'path', { class: 'srch-file srch-file--hit', d: body });
      rows.forEach(function (l) { mk(hit, 'line', { class: 'srch-fline srch-fline--hit', x1: -2.6, y1: l[0], x2: l[1], y2: l[0] }); });
      st.files.push({ g: fg, hit: hit, x: xs[c], y: ys[r] });
    }
    st.TG = 7;
    st.order = [4, 0, 1, 2, 5, 8, 6, 3];
    st.path = st.order.map(function (i) { return [st.files[i].x, st.files[i].y]; });
    return st;
  },
  scan: function (st, c) { return hopLoop(st.path, c, FILE_HOP, FILE_REST); },
  target: function (st) { var f = st.files[st.TG]; return [f.x, f.y]; },
  draw: function (st, k) {
    var n = st.order.length;
    st.files.forEach(function (fl, i) {
      var j = st.order.indexOf(i), dim = 0, sc = 1;
      if (j >= 0) {
        var age = sinceVisit(j, k.c, n, FILE_HOP);
        if (age < Infinity) dim = smooth(ph(age, FILE_REST * FILE_HOP, FILE_REST * FILE_HOP + 0.3)) * (1 - smooth(ph(age, 3.2, 4.4)));
        dim *= 1 - k.fnd;
      }
      if (i === st.TG) { sc = 1 + 0.12 * k.fnd; set(fl.hit, { opacity: k.fnd }); }
      set(fl.g, { opacity: 1 - 0.4 * dim, transform: 'translate(' + fl.x + ' ' + fl.y + ') scale(' + fx(sc) + ')' });
    });
  }
};

/* doc: a page read line by line, slowly left to right and quickly back,
   from the top again after the last line; the found word gets a marker,
   then the other occurrences */
var DOC_LINE = 1.7, DOC_READ = 1.3;
F.doc = {
  build: function (g) {
    var st = {}, R = rnd(7), WL = 4.6, words = {};
    mk(g, 'path', { class: 'srch-file srch-sheet', d: 'M9 4h25l5 5v35h-30z' });
    mk(g, 'path', { class: 'srch-line', d: 'M34 4v5h5', 'stroke-width': 0.6 });
    var marks = mk(g, 'g');
    // the word being looked for, and two more places where it occurs
    var keys = ['6:1', '1:2', '8:0'];
    for (var i = 0; i < 9; i++) {
      var y = 11 + i * 3.6, x = 13, end = i === 8 ? 27 : 35, w = 0;
      for (;;) {
        var len = keys.indexOf(i + ':' + w) >= 0 ? WL : 1.6 + R() * 4.2;
        if (x + len > end) break;
        words[i + ':' + w] = { x1: x, x2: x + len, y: y };
        mk(g, 'line', { class: 'srch-word', x1: x, y1: y, x2: x + len, y2: y });
        x += len + 1.5; w++;
      }
    }
    st.hits = keys.map(function (key) {
      var wd = words[key];
      return {
        wd: wd,
        mark: mk(marks, 'rect', { class: 'srch-mark', x: wd.x1 - 0.9, y: wd.y - 1.4, height: 2.8, rx: 0.6, width: 0 }),
        line: mk(g, 'line', { class: 'srch-word srch-word--hit', x1: wd.x1, y1: wd.y, x2: wd.x2, y2: wd.y, opacity: 0 })
      };
    });
    return st;
  },
  scan: function (st, c) {
    var i = Math.floor(c / DOC_LINE), f = c - i * DOC_LINE, y0 = 11 + mod(i, 9) * 3.6, y1 = 11 + mod(i + 1, 9) * 3.6;
    if (f < DOC_READ) { var u = f / DOC_READ; return [lerp(15, 33, lerp(u, ease(u), 0.3)), y0]; }
    var v = ease((f - DOC_READ) / (DOC_LINE - DOC_READ));
    return [lerp(33, 15, v), lerp(y0, y1, v)];
  },
  target: function (st) { var wd = st.hits[0].wd; return [(wd.x1 + wd.x2) / 2, wd.y]; },
  draw: function (st, k) {
    st.hits.forEach(function (h, n) {
      var q = n === 0 ? k.fnd : smooth((k.since - 0.35 - n * 0.3) / 0.3);
      set(h.mark, { width: (h.wd.x2 - h.wd.x1 + 1.8) * q, opacity: n === 0 ? 0.8 : 0.45 });
      set(h.line, { opacity: n === 0 ? q : 0 });
    });
  }
};

/* code: an editor scrolls under the lens like grep, round and round; once
   found it runs on to the match and stops there */
var CODE_LH = 3.4, CODE_N = 27, CODE_M = 20, CODE_SPEED = 5.5, CODE_H = CODE_N * CODE_LH;
F.code = {
  build: function (g, defs, id) {
    var st = { toks: [], flags: [] };
    mk(g, 'rect', { class: 'srch-file srch-sheet', x: 4, y: 5, width: 40, height: 38, rx: 2 });
    mk(g, 'line', { class: 'srch-line', x1: 10.5, y1: 5, x2: 10.5, y2: 43, 'stroke-width': 0.5 });
    var clip = mk(defs, 'clipPath', { id: id + '-code' });
    mk(clip, 'rect', { x: 4, y: 5.4, width: 40, height: 37.2 });
    var view = mk(g, 'g', { 'clip-path': 'url(#' + id + '-code)' });
    st.band = mk(view, 'rect', { class: 'srch-band', x: 4, y: C - CODE_LH / 2, width: 40, height: CODE_LH, opacity: 0 });
    st.match = mk(view, 'rect', { class: 'srch-mark', x: 10.5, y: C - CODE_LH / 2, width: 33.5, height: CODE_LH, opacity: 0 });
    // the listing, and the same listing again right under it, so the scroll
    // wraps round (drawn twice rather than <use>d: a <use> copy loses the
    // page's styles in Safari and Firefox)
    st.roll = mk(view, 'g');
    [0, CODE_H].forEach(function (off) { listing(mk(st.roll, 'g', { transform: 'translate(0 ' + off + ')' })); });
    return st;

    function listing(scroll) {
    var R = rnd(11), depth = 0, kinds = ['kw', 'id', 'id', 'str'];
    for (var i = 0; i < CODE_N; i++) {
      var y = 9 + i * CODE_LH, x, toks = [], brace = null;
      mk(scroll, 'line', { class: 'srch-line', x1: 6.2, y1: y, x2: i + 1 >= 10 ? 8.8 : 7.6, y2: y, 'stroke-width': 0.6 });
      var roll = R();
      if (i === CODE_M) toks = [['kw', 3], ['id', 5.5], ['str', 6]];
      else if (depth > 0 && roll < 0.22) { depth--; brace = '}'; }
      else if (depth < 3 && roll < 0.5 && i < CODE_N - 4) { toks = [['kw', 2.5 + R() * 2], ['id', 3 + R() * 4]]; brace = '{'; }
      else if (roll > 0.92) toks = [];
      else { var n = 2 + Math.floor(R() * 2); for (var t = 0; t < n; t++) toks.push([kinds[Math.floor(R() * 4)], 2 + R() * 5]); }
      x = 12.5 + depth * 2.6;
      toks.forEach(function (tk, j) {
        var len = Math.min(tk[1], 42 - x); if (len < 1) return;
        mk(scroll, 'line', { class: 'srch-tok srch-tok--' + tk[0], x1: x, y1: y, x2: x + len, y2: y });
        if (i === CODE_M && j === 1) { st.toks.push(mk(scroll, 'line', { class: 'srch-tok srch-tok--hit', x1: x, y1: y, x2: x + len, y2: y, opacity: 0 })); st.mx = x + len / 2; }
        x += len + 1.3;
      });
      if (brace) { var b = mk(scroll, 'text', { class: 'srch-brace', x: x, y: y + 1.1 }); b.textContent = brace; }
      if (brace === '{') depth++;
    }
    st.flags.push(mk(scroll, 'circle', { class: 'srch-hit', cx: 7.4, cy: 9 + CODE_M * CODE_LH, r: 1.2, opacity: 0 }));
    }
  },
  // how far the listing has scrolled: steadily while searching, then on to
  // the next time the match comes level with the lens
  offset: function (st, k) {
    var o = CODE_SPEED * (k.findC == null ? k.c : k.findC);
    if (k.findC == null) return o;
    if (st.to == null) {
      var at = 9 + CODE_M * CODE_LH - C;
      st.from = o; st.to = at + Math.ceil((o + 6 - at) / CODE_H) * CODE_H;
    }
    return lerp(st.from, st.to, ease(ph(k.since, -0.55, 0.3)));
  },
  scan: function (st, c) { return [C + 7 * Math.sin(c * 1.1), C]; },
  target: function (st) { return [st.mx, C]; },
  draw: function (st, k) {
    set(st.roll, { transform: 'translate(0 ' + fx(-mod(this.offset(st, k), CODE_H)) + ')' });
    set(st.band, { opacity: 0.7 * k.m * (1 - k.fnd) });
    set(st.match, { opacity: 0.5 * k.fnd });
    st.toks.concat(st.flags).forEach(function (el) { set(el, { opacity: k.fnd }); });
  }
};

/* knowledge base: chunks lie in clouds by meaning around a query point;
   while it searches the query sends out short probes, once found a ring
   grows from it and lights the nearest chunks as it reaches them */
F.kb = {
  build: function (g) {
    var P = [[10, 12], [14, 10], [12, 16], [17, 14], [8, 17], [33, 9], [37, 12], [40, 8], [35, 15], [41, 15], [10, 32], [14, 36], [9, 38], [16, 31], [32.2, 28.5], [28.3, 32.2], [33.4, 32.6], [27, 27.6], [36, 35], [25, 36], [38, 27]];
    var Q = [30, 30];
    var near = P.map(function (p, i) { return { i: i, d: Math.hypot(p[0] - Q[0], p[1] - Q[1]) }; }).sort(function (a, b) { return a.d - b.d; }).slice(0, 3);
    var st = { P: P, Q: Q, near: near };
    st.links = near.map(function () { return mk(g, 'line', { class: 'srch-hot', 'stroke-width': 0.6, x1: Q[0], y1: Q[1], x2: Q[0], y2: Q[1], opacity: 0 }); });
    st.probe = mk(g, 'circle', { class: 'srch-hot', 'stroke-width': 0.4, cx: Q[0], cy: Q[1], r: 0, opacity: 0 });
    st.ring = mk(g, 'circle', { class: 'srch-hot', 'stroke-width': 0.4, 'stroke-dasharray': '0.8 0.8', cx: Q[0], cy: Q[1], r: 0, opacity: 0 });
    st.dots = P.map(function (p) { return mk(g, 'circle', { class: 'srch-dot', cx: p[0], cy: p[1], r: 1, 'fill-opacity': 0.6 }); });
    st.q = mk(g, 'path', { class: 'srch-hit', d: 'M0 -1.7L1.7 0L0 1.7L-1.7 0Z', transform: 'translate(30 30) scale(0)' });
    st.path = [[13, 13], [37, 12], [38, 24], [14, 34]];
    return st;
  },
  scan: function (st, c) { return loop(st.path, c / 6); },
  target: function (st) { return st.Q; },
  draw: function (st, k) {
    var qa = smooth(ph(k.c, 0.1, 0.5)), reach = st.near[2].d + 0.8;
    set(st.q, { transform: 'translate(' + st.Q[0] + ' ' + st.Q[1] + ') scale(' + fx(qa * (1 + 0.25 * Math.sin(k.c * 6) * (1 - k.fnd))) + ')' });
    var pr = mod(k.c, 1.8) / 1.8;
    set(st.probe, { r: 0.55 * reach * pr, opacity: 0.6 * (1 - pr) * qa * (1 - k.fnd) });
    var rr = reach * smooth(ph(k.since, -0.2, 0.4));
    set(st.ring, { r: rr, opacity: k.since > -0.2 ? 1 - 0.6 * smooth(ph(k.since, 0.4, 0.8)) : 0 });
    var lit = {};
    st.near.forEach(function (n, j) {
      var q = k.since > -0.2 ? smooth((rr - n.d) / 1.2) : 0, p = st.P[n.i];
      lit[n.i] = q;
      set(st.links[j], { x2: lerp(st.Q[0], p[0], q), y2: lerp(st.Q[1], p[1], q), opacity: q });
    });
    st.dots.forEach(function (d, i) { var q = lit[i] || 0; set(d, { r: 1 + 0.7 * q, 'fill-opacity': 0.6 + 0.4 * q, class: q > 0.5 ? 'srch-hit' : 'srch-dot' }); });
  }
};

/* database: a table filtered by one column; the lens runs down it again and
   again, matching rows light up as it passes and settle on the way back up;
   once found every match lights up and the rest grow faint */
var DB_DOWN = 2.6, DB_BACK = 0.6;
F.db = {
  build: function (g) {
    var st = { rows: [] }, cols = [[7, 6], [15.5, 5], [24, 6.5], [33.5, 7]], R = rnd(3), HIT = [2, 5, 7];
    mk(g, 'rect', { class: 'srch-file srch-sheet', x: 5, y: 5, width: 38, height: 39, rx: 1.5 });
    mk(g, 'rect', { class: 'srch-mark', x: 5, y: 5, width: 38, height: 5, rx: 1.5, 'fill-opacity': 0.7 });
    cols.forEach(function (c) { mk(g, 'line', { class: 'srch-fline srch-fline--hit', x1: c[0], y1: 7.5, x2: c[0] + c[1] * 0.75, y2: 7.5 }); });
    // a funnel on the filtered column
    mk(g, 'path', { class: 'srch-hit', d: 'M20.2 6.3h2.8l-1.05 1.3v1.2l-0.7 0.4v-1.6z' });
    for (var i = 0; i < 9; i++) {
      var y = 11 + i * 3.6, yc = y + 1.8, hit = HIT.indexOf(i) >= 0;
      var band = mk(g, 'rect', { class: 'srch-band', x: 5.3, y: y + 0.2, width: 37.4, height: 3.2, opacity: 0 });
      if (i) mk(g, 'line', { class: 'srch-line', x1: 5, y1: y, x2: 43, y2: y, 'stroke-width': 0.3 });
      var cells = mk(g, 'g');
      cols.forEach(function (c, j) {
        var w = j === 1 ? (hit ? 4.4 : 1.6 + R() * 2.2) : c[1] * (0.45 + R() * 0.5);
        mk(cells, 'line', { class: 'srch-fline', x1: c[0], y1: yc, x2: c[0] + w, y2: yc });
      });
      var key = hit ? mk(g, 'line', { class: 'srch-tok srch-tok--hit', x1: cols[1][0], y1: yc, x2: cols[1][0] + 4.4, y2: yc, opacity: 0 }) : null;
      st.rows.push({ yc: yc, hit: hit, band: band, cells: cells, key: key });
    }
    st.x = cols[1][0] + 2.2;
    st.TG = 2;
    return st;
  },
  scan: function (st, c) {
    var f = mod(c, DB_DOWN + DB_BACK), y0 = st.rows[0].yc, y1 = st.rows[8].yc;
    if (f < DB_DOWN) { var u = f / DB_DOWN; return [st.x, lerp(y0, y1, lerp(u, ease(u), 0.3))]; }
    return [st.x, lerp(y1, y0, ease((f - DB_DOWN) / DB_BACK))];
  },
  target: function (st) { return [st.x, st.rows[st.TG].yc]; },
  draw: function (st, k) {
    var f = mod(k.c, DB_DOWN + DB_BACK), y = this.scan(st, k.c)[1], back = f < DB_DOWN ? 0 : ease((f - DB_DOWN) / DB_BACK);
    st.rows.forEach(function (r) {
      var past = smooth((y - r.yc + 0.5) / 1.5) * (1 - back) * (1 - k.fnd);
      if (r.hit) { set(r.band, { opacity: 0.6 * past + 0.9 * k.fnd }); set(r.key, { opacity: Math.max(past, k.fnd) }); }
      else set(r.cells, { opacity: 1 - 0.4 * past - 0.55 * k.fnd });
    });
  }
};

/* tools: a catalogue of tool tiles; the lens checks one tile after another,
   once found the ones that do not fit melt away and the pick fills */
var TOOL_HOP = 0.7, TOOL_REST = 0.4;
F.tools = {
  build: function (g) {
    var G = [
      'M-2.4 0a2.4 2.4 0 1 0 4.8 0a2.4 2.4 0 1 0 -4.8 0M-2.4 0h4.8M0 -2.4a1 2.4 0 0 0 0 4.8a1 2.4 0 0 0 0 -4.8',
      'M-2.3 -1.6a2.3 0.9 0 1 0 4.6 0a2.3 0.9 0 1 0 -4.6 0v3.2a2.3 0.9 0 0 0 4.6 0v-3.2',
      'M-2.6 -1.8h5.2v3.6h-5.2zM-2.6 -1.8L0 0.4L2.6 -1.8',
      'M-1 -2L-3 0L-1 2M1 -2L3 0L1 2',
      'M-2.4 -2h4.8v4.4h-4.8zM-2.4 -0.6h4.8M-1.2 -2.8v1.4M1.2 -2.8v1.4',
      'M-2 -2.6h2.6l1.4 1.4v3.8h-4zM0.6 -2.6v1.4h1.4',
      'M-2.4 -1.4L-0.6 0L-2.4 1.4M0.4 1.6h2.2',
      'M-2.4 -2.4v4.8h5M-1 1v-1.4M0.6 1v-2.6M2 1v-1',
      'M-1 -2.6v1.6M1 -2.6v1.6M-2 -1h4v1.4a2 2 0 0 1 -4 0zM0 2.4v0.8',
      'M-2.6 -2h5.2v3.2h-3.2l-1.4 1.4v-1.4h-0.6z',
      'M-2.6 -2h5.2v4h-5.2zM-2.6 1.6l1.8 -1.8l1.4 1.2l1.2 -1l1.8 1.6',
      'M-2.4 -1.2a1.8 1.8 0 1 0 3.6 0a1.8 1.8 0 1 0 -3.6 0M0.7 0.5l1.8 1.8'
    ];
    var xs = [9.5, 19.5, 29.5, 39.5], ys = [11, 24, 37], st = { tiles: [], cand: [2, 9, 6], TG: 6 };
    G.forEach(function (d, i) {
      var x = xs[i % 4], y = ys[Math.floor(i / 4)];
      var tg = mk(g, 'g', { transform: 'translate(' + x + ' ' + y + ')' });
      var body = mk(tg, 'rect', { class: 'srch-file', x: -4, y: -4, width: 8, height: 8, rx: 2 });
      var gl = mk(tg, 'path', { class: 'srch-glyph', d: d });
      st.tiles.push({ g: tg, body: body, gl: gl, x: x, y: y });
    });
    st.path = [5, 1, 3, 2, 9, 10, 11, 7, 4, 0, 8].map(function (i) { return [st.tiles[i].x, st.tiles[i].y]; });
    return st;
  },
  scan: function (st, c) { return hopLoop(st.path, c, TOOL_HOP, TOOL_REST); },
  target: function (st) { var t = st.tiles[st.TG]; return [t.x, t.y]; },
  draw: function (st, k) {
    var cut = smooth(ph(k.since, -0.45, 0.1));
    st.tiles.forEach(function (tl, i) {
      var keep = st.cand.indexOf(i) >= 0, hit = i === st.TG;
      var sc = keep ? 1 + (hit ? 0.14 * k.fnd : 0) : 1 - 0.15 * cut;
      set(tl.g, { opacity: keep ? 1 : 1 - 0.75 * cut, transform: 'translate(' + tl.x + ' ' + tl.y + ') scale(' + fx(sc) + ')' });
      if (hit) {
        set(tl.body, { class: k.fnd > 0.5 ? 'srch-file srch-file--hit' : 'srch-file' });
        set(tl.gl, { class: k.fnd > 0.5 ? 'srch-glyph srch-glyph--hit' : 'srch-glyph' });
      }
    });
  }
};

/* mail: an inbox list; the lens steps down the letters and starts at the
   top again, the found one is highlighted, its envelope opens and the
   letter rises out */
var MAIL_HOP = 0.6, MAIL_REST = 0.45;
F.mail = {
  build: function (g) {
    var st = { rows: [], TG: 4 }, R = rnd(5);
    mk(g, 'rect', { class: 'srch-file srch-sheet', x: 5, y: 5, width: 38, height: 38, rx: 1.5 });
    for (var i = 0; i < 7; i++) {
      var y = 6.5 + i * 5.2, yc = y + 2.3;
      var band = mk(g, 'rect', { class: 'srch-band', x: 5.4, y: y - 0.2, width: 37.2, height: 4.9, opacity: 0 });
      if (i) mk(g, 'line', { class: 'srch-line', x1: 7, y1: y - 0.5, x2: 41, y2: y - 0.5, 'stroke-width': 0.3 });
      var env = mk(g, 'g', { transform: 'translate(10.5 ' + yc + ')' });
      var letter = mk(env, 'rect', { class: 'srch-letter', x: -1.9, y: -1.2, width: 3.8, height: 2.8, rx: 0.3, opacity: 0 });
      mk(env, 'rect', { class: 'srch-file', x: -2.6, y: -1.8, width: 5.2, height: 3.6, rx: 0.4 });
      var flap = mk(env, 'path', { class: 'srch-glyph', d: 'M-2.6 -1.8L0 0.4L2.6 -1.8' });
      mk(g, 'line', { class: 'srch-tok srch-tok--id', x1: 15, y1: yc - 0.9, x2: 20 + R() * 5, y2: yc - 0.9 });
      mk(g, 'line', { class: 'srch-fline', x1: 15, y1: yc + 1.1, x2: 27 + R() * 13, y2: yc + 1.1 });
      st.rows.push({ y: yc, band: band, flap: flap, letter: letter });
    }
    st.path = [0, 1, 2, 3, 5, 6].map(function (i) { return [22, st.rows[i].y]; });
    return st;
  },
  scan: function (st, c) { return hopLoop(st.path, c, MAIL_HOP, MAIL_REST); },
  target: function (st) { return [22, st.rows[st.TG].y]; },
  draw: function (st, k) {
    var r = st.rows[st.TG], open = smooth(ph(k.since, 0.05, 0.4));
    set(r.band, { opacity: 0.9 * k.fnd });
    set(r.flap, { d: 'M-2.6 -1.8L0 ' + fx(lerp(0.4, -3.8, open)) + 'L2.6 -1.8' });
    set(r.letter, { y: lerp(-1.2, -3.8, open), opacity: open });
  }
};

/* history: past runs on lanes along a time axis; the timeline keeps rolling
   back into the past under the lens (one period of it and a copy, so it
   wraps round) and once found it rolls on to the run being recalled */
var HIST_W = 128, HIST_SPEED = 4.5;
F.history = {
  build: function (g, defs, id) {
    var st = { marks: [] }, lanes = [13, 21, 29], x0 = 44 - HIST_W;
    mk(g, 'rect', { class: 'srch-file srch-sheet', x: 4, y: 6, width: 40, height: 36, rx: 1.5 });
    mk(mk(defs, 'clipPath', { id: id + '-hist' }), 'rect', { x: 4, y: 6, width: 40, height: 36 });
    var view = mk(g, 'g', { 'clip-path': 'url(#' + id + '-hist)' });
    st.scroll = mk(view, 'g');
    // one period of the timeline and the same period again before it, drawn
    // twice rather than <use>d (a <use> copy loses the page's styles in
    // Safari and Firefox)
    [0, -HIST_W].forEach(function (off) { strip(mk(st.scroll, 'g', { transform: 'translate(' + off + ' 0)' })); });
    // now: where the timeline starts
    mk(view, 'line', { class: 'srch-hot', x1: 41, y1: 9, x2: 41, y2: 36, 'stroke-width': 0.5, 'stroke-dasharray': '1 1' });
    return st;

    function strip(sg) {
      var R = rnd(9), best = null;
      mk(sg, 'line', { class: 'srch-line', x1: x0, y1: 36, x2: 44, y2: 36 });
      for (var n = 0; n < HIST_W / 4; n++) { var x = 44 - n * 4; mk(sg, 'line', { class: 'srch-line', x1: x, y1: 36, x2: x, y2: n % 4 === 0 ? 38.6 : 37.4, 'stroke-width': 0.4 }); }
      lanes.forEach(function (ly, li) {
        var x = 39 - R() * 4;
        for (;;) {
          var len = 3 + R() * 7, cx = x - len / 2;
          if (x - len < x0 + 1.5) break;   // the period ends here; its copy picks up after a gap
          mk(sg, 'rect', { class: 'srch-cap', x: x - len, y: ly - 1.8, width: len, height: 3.6, rx: 1.8 });
          if (li === 1 && (!best || Math.abs(cx + 52) < Math.abs(best.cx + 52))) best = { x: x - len, len: len, cx: cx, y: ly };
          x -= len + 1.5 + R() * 4;
        }
      });
      st.marks.push(mk(sg, 'rect', { class: 'srch-file--hit', x: best.x - 0.3, y: best.y - 2.1, width: best.len + 0.6, height: 4.2, rx: 2.1, opacity: 0 }));
      st.marks.push(mk(sg, 'line', { class: 'srch-hot', x1: best.cx, y1: best.y + 2.4, x2: best.cx, y2: 36, 'stroke-width': 0.6, 'stroke-dasharray': '0.8 0.8', opacity: 0 }));
      st.best = best;
    }
  },
  offset: function (st, k) {
    var o = HIST_SPEED * (k.findC == null ? k.c : k.findC);
    if (k.findC == null) return o;
    if (st.to == null) {
      var at = C - st.best.cx;
      st.from = o; st.to = at + Math.ceil((o + 6 - at) / HIST_W) * HIST_W;
    }
    return lerp(st.from, st.to, ease(ph(k.since, -0.55, 0.3)));
  },
  scan: function (st, c) { return [C + 4 * Math.sin(c * 0.9), 21 + 3 * Math.sin(c * 1.7)]; },
  target: function (st) { return [C, st.best.y]; },
  draw: function (st, k) {
    set(st.scroll, { transform: 'translate(' + fx(mod(this.offset(st, k), HIST_W)) + ' 0)' });
    st.marks.forEach(function (el) { set(el, { opacity: k.fnd }); });
  }
};

/* errors: runs per hour with their failures stacked on top; the lens sweeps
   to and fro along the bars, once found it stops on the spike and a
   warning sign pops up */
F.errors = {
  build: function (g) {
    var st = { bars: [] }, R = rnd(13), SP = 8;
    mk(g, 'line', { class: 'srch-line', x1: 4, y1: 38, x2: 44, y2: 38 });
    for (var i = 0; i < 12; i++) {
      var x = 5 + i * 3.25, ok = 6 + R() * 12, bad = i === SP ? 11 : (R() < 0.4 ? 0.8 + R() * 1.6 : 0);
      if (i === SP) ok = 9;
      mk(g, 'rect', { class: 'srch-bar', x: x, y: 38 - ok, width: 2.3, height: ok, rx: 0.5 });
      if (bad) mk(g, 'rect', { class: 'srch-err', x: x, y: 38 - ok - bad - 0.4, width: 2.3, height: bad, rx: 0.5 });
      st.bars.push({ x: x + 1.15, top: 38 - ok - bad - 0.4, fy: 38 - ok - bad / 2 - 0.4 });
    }
    var sp = st.bars[SP];
    st.sp = sp;
    st.pulse = [0, 1].map(function () { return mk(g, 'circle', { class: 'srch-errline', r: 0, opacity: 0 }); });
    st.warn = mk(g, 'g', { transform: 'translate(' + sp.x + ' ' + (sp.top - 4) + ') scale(0)' });
    mk(st.warn, 'path', { class: 'srch-err', d: 'M0 -2.4L2.5 1.9L-2.5 1.9Z', stroke: 'none' });
    mk(st.warn, 'path', { d: 'M0 -0.8v1.2M0 1.15v0.05', stroke: '#fff', 'stroke-width': 0.6, 'stroke-linecap': 'round' });
    return st;
  },
  scan: function (st, c) { return [C + 16 * Math.sin(c * 0.85), 28 + 1.5 * Math.sin(c * 1.7)]; },
  target: function (st) { return [st.sp.x, st.sp.fy]; },
  draw: function (st, k) {
    var sp = st.sp, sc = k.fnd * (1 + 0.12 * Math.sin(k.c * 7));
    set(st.warn, { transform: 'translate(' + fx(sp.x) + ' ' + fx(sp.top - 4) + ') scale(' + fx(sc) + ')' });
    pulses(st.pulse, [sp.x, sp.fy], k.since, k.fnd, 3, 6, 1.1);
  }
};

export const SEARCH_KINDS = Object.keys(F);

// Phases, in seconds.
var INTRO = 1.4;        // the mark opens into the lens and sets off
var MIN_SCAN = 0.5;     // a search shows at least this much scanning before it can find
var GLIDE = 0.55;       // the lens on its way to the find
var FIND = 1.5;         // the find, from the moment the lens sets off to it
var SWAP = 0.9;         // one field fading into the next while the lens sets off again
var FOLD = 1.2;         // back to the centre and into the mark

/**
 * Mount the search scene into `g`, starting on field `kind`. update(dt,
 * next) moves it on by dt seconds towards `next`: the same kind keeps
 * scanning, another kind finds and swaps fields, null finds and folds.
 * It returns 'done' once folded back into the mark.
 */
export function mountSearch(g, kind) {
  if (!F[kind]) return null;
  var id = 'srch' + (++uid);
  var defs = mk(g, 'defs');
  var clipC = mk(mk(defs, 'clipPath', { id: id + '-lens' }), 'circle', { r: 0 });
  // the field shows through the glass as the page behind it, not as white
  var mask = mk(defs, 'mask', { id: id + '-glass', maskUnits: 'userSpaceOnUse', x: -20, y: -20, width: 88, height: 88 });
  mk(mask, 'rect', { x: -20, y: -20, width: 88, height: 88, fill: '#fff' });
  var holeC = mk(mask, 'circle', { r: 0, fill: '#000' });
  var wrap = mk(g, 'g', { mask: 'url(#' + id + '-glass)' });
  var spokes = DIR.map(function () { return mk(g, 'line', { class: 'srch-handle' }); });
  var zoom = mk(mk(g, 'g', { 'clip-path': 'url(#' + id + '-lens)' }), 'g', { class: 'srch-zoom' });
  var core = mk(g, 'circle', { class: 'srch-core' });
  var ring = mk(g, 'circle', { class: 'srch-ring' });
  g.appendChild(spokes[1]);   // the handle lies over the rim
  var sats = DIR.map(function () { return mk(g, 'circle', { class: 'srch-sat' }); });

  var fields = [];
  // A field is built twice: once under the glass and once, the same, in the
  // lens to be magnified. Not a <use> of the first: in Safari and Firefox a
  // <use> copy loses the page's styles and comes out black.
  function addField(name) {
    var fid = id + 'f' + (++uid);
    var def = F[name];
    var f = { kind: name, def: def, g: mk(wrap, 'g', { opacity: 0 }), zg: mk(zoom, 'g', { opacity: 0 }), c: 0, fo: 0, findC: null };
    f.st = def.build(f.g, defs, fid);
    f.zst = def.build(f.zg, defs, fid + 'z');
    fields.push(f);
    return f;
  }
  function dropField(f) {
    wrap.removeChild(f.g); zoom.removeChild(f.zg);
    fields.splice(fields.indexOf(f), 1);
  }

  var s = { phase: 'intro', t: 0, m: 0, L: [C, C], from: null };
  var cur = addField(kind);
  var old = null;

  function k(f, extra) {
    var since = f.findC == null ? -1e9 : f.c - f.findC - GLIDE;
    var o = { c: f.c, L: s.L, m: s.m, fnd: f.findC == null ? 0 : smooth(ph(since, -0.15, 0.1)), since: since, findC: f.findC };
    if (extra) for (var key in extra) o[key] = extra[key];
    return o;
  }
  function startFind() {
    s.phase = 'find'; s.t = 0; s.from = s.L;
    cur.findC = cur.c;
    if (cur.def.onFind) { cur.def.onFind(cur.st, k(cur)); cur.def.onFind(cur.zst, k(cur)); }
  }

  function update(dt, next) {
    s.t += dt;
    fields.forEach(function (f) { f.c += dt; });
    var leaving = next !== cur.kind;
    if (s.phase === 'intro') {
      s.m = ease(ph(s.t, 0.05, 0.85));
      cur.fo = smooth(ph(s.t, 0.3, 0.9));
      cur.c = Math.max(0, s.t - 0.5);
      s.L = lerp2([C, C], cur.def.scan(cur.st, cur.c), ease(ph(s.t, 0.5, INTRO)));
      if (s.t >= INTRO) { s.phase = 'scan'; s.t = 0; }
    } else if (s.phase === 'scan') {
      s.L = cur.def.scan(cur.st, cur.c);
      if (leaving && s.t >= MIN_SCAN) startFind();
    } else if (s.phase === 'swap') {
      old.fo = 1 - smooth(ph(s.t, 0, 0.5));
      cur.fo = smooth(ph(s.t, 0.15, 0.65));
      s.L = lerp2(s.from, cur.def.scan(cur.st, cur.c), ease(ph(s.t, 0.1, SWAP)));
      if (s.t >= SWAP) { dropField(old); old = null; s.phase = 'scan'; s.t = 0; }
    } else if (s.phase === 'fold') {
      cur.fo = 1 - smooth(ph(s.t, 0, 0.45));
      s.L = lerp2(s.from, [C, C], ease(ph(s.t, 0, 0.55)));
      s.m = 1 - ease(ph(s.t, 0.45, FOLD));
      if (s.t >= FOLD) { s.m = 0; draw(); return 'done'; }
    }
    if (s.phase === 'find') {
      s.L = lerp2(s.from, cur.def.target(cur.st, k(cur)), ease(ph(s.t, 0, GLIDE)));
      if (s.t >= FIND) {
        s.from = s.L; s.t = 0;
        if (next && F[next]) {
          // the next search (another one of the same kind too): its field
          // fades in under the lens, which sets off from the find
          old = cur; cur = addField(next); s.phase = 'swap';
        } else {
          s.phase = 'fold';
        }
      }
    }
    draw();
    return 'running';
  }

  function draw() {
    fields.forEach(function (f) {
      f.def.draw(f.st, k(f));
      f.def.draw(f.zst, k(f));
      set(f.g, { opacity: f.fo });
      set(f.zg, { opacity: f.fo });
    });
    var m = s.m, r = lerp(7, RL, m), x = s.L[0], y = s.L[1];
    set(holeC, { cx: x, cy: y, r: Math.max(0, r - 1), opacity: m });
    set(clipC, { cx: x, cy: y, r: Math.max(0, r - 1) });
    set(zoom, { opacity: m, transform: 'translate(' + fx(x) + ' ' + fx(y) + ') scale(' + MAG + ') translate(' + fx(-x) + ' ' + fx(-y) + ')' });
    set(core, { cx: x, cy: y, r: r, opacity: 1 - m });
    set(ring, { cx: x, cy: y, r: r, 'stroke-width': 2.2 * m, opacity: m });
    // the two spokes that do not become the handle fade with the mark
    [0, 2].forEach(function (i) {
      var q = SPOKE[i];
      set(spokes[i], { x1: x + q[0] - C, y1: y + q[1] - C, x2: x + q[2] - C, y2: y + q[3] - C, 'stroke-width': 2.5, opacity: 0.55 * (1 - m) });
    });
    // the lower right one grows from the logo's spoke into the handle
    var d = DIR[1], q1 = SPOKE[1], e = lerp(RS[1], RL + 9.6, m);
    set(spokes[1], {
      x1: x + lerp(q1[0] - C, d[0] * (RL + 0.6), m), y1: y + lerp(q1[1] - C, d[1] * (RL + 0.6), m),
      x2: x + lerp(q1[2] - C, d[0] * (RL + 9.4), m), y2: y + lerp(q1[3] - C, d[1] * (RL + 9.4), m),
      'stroke-width': lerp(2.5, 4.2, m), opacity: lerp(0.55, 1, m)
    });
    var lr = { p: [x + d[0] * e, y + d[1] * e], r: lerp(4.5, 2.5, m), o: lerp(0.75, 1, m) };
    var other = [0, 2].map(function (i) { return { p: lerp2(SAT[i], [C, C], 0.35 * m), r: 4.5 * (1 - m), o: 0.75 }; });
    [other[0], lr, other[1]].forEach(function (q, i) { set(sats[i], { cx: q.p[0], cy: q.p[1], r: Math.max(0, q.r), opacity: q.o }); });
  }

  draw();
  return {
    accepts: function (name) { return !!F[name]; },
    update: update,
    get phase() { return s.phase; },
    destroy: function () { while (g.firstChild) g.removeChild(g.firstChild); },
  };
}
