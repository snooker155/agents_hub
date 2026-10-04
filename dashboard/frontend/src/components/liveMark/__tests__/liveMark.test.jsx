import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import LiveMark from '../LiveMark';
import useSteadyState from '../useSteadyState';
import { CX, CY, LOGO_SATS, LOGO_SPOKES, POSES, STATES, mixPose, poseFor, spokeLines } from '../poses';
import { stateForTool, stateForTurn } from '../activity';
import { SCENE_STATES, isScene, mountScene } from '../scenes';
import { MessageBubble } from '../../chat/MessageBubble';

const xy = (s) => [CX + s.d * Math.cos(s.a), CY + s.d * Math.sin(s.a)];

describe('live mark poses', () => {
  it('rests on the logo: core r 7, satellites r 4.5 at the three corners', () => {
    const p = poseFor('idle', 0);
    expect(p.core).toEqual({ x: 24, y: 24, r: 7, o: 1 });
    const pts = p.sats.map(xy);
    [[24, 6], [40, 34], [8, 34]].forEach(([x, y], i) => {
      expect(pts[i][0]).toBeCloseTo(x, 0);
      expect(pts[i][1]).toBeCloseTo(y, 0);
    });
    expect(p.sats.every((s) => s.r === 4.5 && s.o === 0.75)).toBe(true);
    expect(p.spokes).toEqual([0.55, 0.55, 0.55]);
    // the spokes are the logo's own, which are not quite on the line to the satellites
    expect(spokeLines(p)).toEqual(LOGO_SPOKES);
  });

  it('gives every state a finite pose of the same shape at any time', () => {
    for (const state of STATES) {
      for (const t of [0, 0.37, 1.9, 13.2]) {
        const p = POSES[state](t);
        expect(p.sats).toHaveLength(3);
        expect(p.spokes).toHaveLength(3);
        const nums = [p.core.x, p.core.y, p.core.r, p.core.o,
          ...p.spokes, ...p.sats.flatMap((s) => [s.a, s.d, s.r, s.o])];
        expect(nums.every(Number.isFinite)).toBe(true);
      }
    }
  });

  it('blends from one pose to the other, the endpoints exact', () => {
    const a = poseFor('idle', 0);
    const b = poseFor('read', 0.4);
    expect(mixPose(a, b, 0)).toBe(a);
    expect(mixPose(a, b, 1)).toBe(b);
    const mid = mixPose(a, b, 0.5);
    expect(mid.core.y).toBeCloseTo((a.core.y + b.core.y) / 2);
  });

  it('turns a satellite the short way round', () => {
    const a = poseFor('idle', 0);
    const b = { ...a, sats: a.sats.map((s) => ({ ...s, a: s.a + 2 * Math.PI + 0.2 })) };
    const mid = mixPose(a, b, 0.5);
    expect(mid.sats[0].a - a.sats[0].a).toBeCloseTo(0.1);
  });
});

describe('what the mark shows for a step', () => {
  it.each([
    ['web_search', 'search-web'], ['search_memory', 'search-memory'], ['notion_search', 'search-kb'],
    ['search_docs', 'search-kb'], ['search_text', 'search-files'], ['list_workspace_files', 'search-files'],
    ['db_query', 'search-db'], ['search_errors', 'search-errors'], ['instance_logs', 'search-errors'],
    ['list_runs', 'search-history'], ['get_team_run_tool', 'search-history'],
    ['fetch_url', 'search-doc'], ['read_file', 'search-doc'], ['browser_read', 'search-doc'],
    ['write_file', 'write-file'], ['create_file', 'write-file'], ['save_workspace_file', 'write-file'],
    ['apply_unified_diff', 'write-code'], ['create_view', 'make-view'], ['slides_add', 'make-view'],
    ['mesh_extrude', 'make-3d'], ['mesh_history', 'make-3d'], ['view_serve', 'make-html'],
    ['add_graph_node', 'make-flow'], ['run_flow_tool', 'run-flow'], ['run_team_tool', 'run-team'],
    ['create_agent_tool', 'make-agent'], ['delegate_task_tool', 'delegate'], ['run_agent_tool', 'delegate'],
    ['run_code', 'code'], ['run_shell', 'code'], ['system_run_tests', 'code'],
    ['get_task', 'read'], ['list_flows_tool', 'read'], ['view_get', 'read'],
    ['stop_team_run_tool', 'working'], ['ask_user', 'working'], ['', 'working'],
  ])('%s → %s', (tool, state) => {
    expect(stateForTool(tool)).toBe(state);
  });

  it('puts a waiting approval first, then the tool, the thought and the text', () => {
    expect(stateForTurn({ waiting: true, tool: 'web_search', thinking: true })).toBe('wait');
    expect(stateForTurn({ tool: 'write_file', thinking: true, text: true })).toBe('write-file');
    expect(stateForTurn({ thinking: true, text: true })).toBe('think');
    expect(stateForTurn({ text: true })).toBe('speak');
    expect(stateForTurn({})).toBe('working');
  });
});

describe('scenes after the Orbit Loader artifact', () => {
  const NS = 'http://www.w3.org/2000/svg';
  const host = () => {
    const svg = document.createElementNS(NS, 'svg');
    const g = document.createElementNS(NS, 'g');
    svg.appendChild(g);
    document.body.appendChild(svg);
    return g;
  };
  const DT = 1 / 30;
  // run the scene for `secs` wanting `next`; stops early once it is done
  const run = (api, secs, next) => {
    let r = 'running';
    for (let t = 0; t < secs && r !== 'done'; t += DT) r = api.update(DT, next);
    return r;
  };
  const broken = (g) => [...g.querySelectorAll('*')].flatMap((el) => [...el.attributes])
    .filter((a) => /NaN|Infinity|undefined/.test(a.value)).map((a) => `${a.ownerElement.tagName}.${a.name}`);
  // The core is a circle, or in the page scene a 14 × 14 square rounded by 7.
  const coreOf = (g) => [...g.querySelectorAll('.srch-core')]
    .filter((el) => Number(el.getAttribute('opacity') ?? 1) > 0.99)
    .map((el) => {
      const n = (k) => Number(el.getAttribute(k));
      return el.tagName === 'rect'
        ? { x: n('x') + n('width') / 2, y: n('y') + n('height') / 2, r: n('width') / 2, rx: n('rx') }
        : { x: n('cx'), y: n('cy'), r: n('r'), rx: n('r') };
    })
    .sort((a, b) => b.r - a.r)[0];
  const shown = (el) => Number(el.getAttribute('opacity') ?? 1) > 0.5;
  // satellites are circles, or in the flow scenes 9 × 9 squares rounded by 4.5
  const satsOf = (g) => [...g.querySelectorAll('.srch-sat')].filter(shown).map((el) => {
    const n = (k) => Number(el.getAttribute(k));
    let tx = 0, ty = 0;
    const m = /translate\(([-\d.]+) ([-\d.]+)\)/.exec(el.parentNode.getAttribute?.('transform') || '');
    if (m && el.tagName === 'rect') { tx = Number(m[1]); ty = Number(m[2]); }
    return el.tagName === 'rect'
      ? { x: tx + n('x') + n('width') / 2, y: ty + n('y') + n('height') / 2, r: n('width') / 2 }
      : { x: n('cx'), y: n('cy'), r: n('r') };
  }).filter((q) => q.r > 0.5);
  const spokesOf = (g) => [...g.querySelectorAll('line.srch-handle')].filter(shown)
    .map((el) => ['x1', 'y1', 'x2', 'y2'].map((k) => Number(el.getAttribute(k))));
  const near = (a, b) => Math.abs(a - b) < 0.05;
  // exactly the logo: the same core, satellites and spokes, to the decimal
  const isMark = (g) => {
    const core = coreOf(g);
    expect(core).toBeTruthy();
    expect(core.r).toBeCloseTo(7, 1);
    expect(core.rx).toBeCloseTo(7, 1);
    expect(core.x).toBeCloseTo(24, 1);
    expect(core.y).toBeCloseTo(24, 1);
    const sats = satsOf(g);
    LOGO_SATS.forEach(([x, y]) => {
      expect(sats.some((q) => near(q.x, x) && near(q.y, y) && near(q.r, 4.5)), `satellite at ${x},${y}`).toBe(true);
    });
    const spokes = spokesOf(g);
    LOGO_SPOKES.forEach((l) => {
      expect(spokes.some((q) => q.every((v, i) => near(v, l[i]))), `spoke ${l}`).toBe(true);
    });
  };

  it('knows 22 scenes, and none of them is a pose', () => {
    expect(SCENE_STATES).toHaveLength(22);
    expect(SCENE_STATES.every(isScene)).toBe(true);
    expect(isScene('think')).toBe(false);
  });

  it.each(SCENE_STATES)('%s starts as the mark, works for as long as it is wanted, then finishes and is the mark again', (state) => {
    const g = host();
    const api = mountScene(state, g);
    isMark(g);
    // twenty seconds of work and it has not folded by itself
    expect(run(api, 20, state)).toBe('running');
    expect(api.phase).toMatch(/^(scan|work)$/);
    expect(broken(g)).toEqual([]);
    // the step ends: the finale and the fold take a few seconds, not a clip's length
    expect(run(api, 6, null)).toBe('done');
    isMark(g);
    expect(broken(g)).toEqual([]);
    api.destroy();
    expect(g.childNodes).toHaveLength(0);
  });

  it('goes from one search to the next without the mark: the lens never leaves', () => {
    const g = host();
    const api = mountScene('search-web', g);
    run(api, 3, 'search-web');
    const ring = g.querySelector('.srch-ring');
    let least = 1;
    for (const next of ['search-files', 'search-code', 'search-mail']) {
      for (let t = 0; t < 4; t += DT) {
        expect(api.update(DT, next)).toBe('running');
        least = Math.min(least, Number(ring.getAttribute('opacity')));
      }
    }
    expect(least).toBeGreaterThan(0.99);
    expect(api.phase).toBe('scan');
    // the old fields are gone once faded: one field under the lens again
    expect(g.querySelectorAll('.srch-zoom > g')).toHaveLength(1);
  });

  // a <use> copy loses the page's styles in Safari and Firefox and comes out black
  it.each(SCENE_STATES)('%s draws everything for real, without <use>', (state) => {
    const g = host();
    const api = mountScene(state, g);
    run(api, 3, state);
    expect(g.querySelectorAll('use')).toHaveLength(0);
  });

  it('magnifies a copy of the field that matches the field', () => {
    const g = host();
    run(mountScene('search-code', g), 3, 'search-code');
    const under = g.querySelector('g[mask] > g');
    const lens = g.querySelector('.srch-zoom > g');
    const shape = (el) => [...el.querySelectorAll('*')].map((n) => `${n.tagName}.${n.getAttribute('class')}`).join(',');
    expect(shape(lens)).toBe(shape(under));
  });

  it('does not take writing code for a search of code', () => {
    const api = mountScene('search-code', host());
    expect(api.accepts('search-web')).toBe(true);
    expect(api.accepts('write-code')).toBe(false);
    expect(mountScene('write-code', host()).accepts('search-code')).toBe(false);
  });
});

describe('useSteadyState', () => {
  afterEach(() => { vi.useRealTimers(); });

  it('holds a state for the minimum and then shows only the latest one', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ v }) => useSteadyState(v, 700), { initialProps: { v: 'search' } });
    rerender({ v: 'read' });
    rerender({ v: 'write' });
    expect(result.current).toBe('search');
    act(() => { vi.advanceTimersByTime(650); });
    expect(result.current).toBe('search');
    act(() => { vi.advanceTimersByTime(60); });
    expect(result.current).toBe('write');
  });

  it('switches at once when the state has been on show long enough', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ v }) => useSteadyState(v, 700), { initialProps: { v: 'search' } });
    act(() => { vi.advanceTimersByTime(2000); });
    rerender({ v: 'code' });
    act(() => { vi.advanceTimersByTime(0); });
    expect(result.current).toBe('code');
  });
});

const wrap = (ui) => render(<I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>);

describe('LiveMark', () => {
  it('draws the core, three satellites and their spokes, labelled with the state', () => {
    const { container } = wrap(<LiveMark state="idle" />);
    const svg = container.querySelector('svg');
    expect(svg.getAttribute('data-state')).toBe('idle');
    expect(svg.getAttribute('aria-label')).toBe('Idle');
    expect(container.querySelectorAll('circle')).toHaveLength(4);
    expect(container.querySelectorAll('line')).toHaveLength(3);
    expect(container.querySelector('line').getAttribute('opacity')).toBe('0.550');
  });

  it('uses the crop of logo.svg for frame="logo"', () => {
    const { container } = wrap(<LiveMark frame="logo" size={43} label="" />);
    const svg = container.querySelector('svg');
    expect(svg.getAttribute('viewBox')).toBe('2.5 0.5 43 39');
    expect(Number(svg.getAttribute('height'))).toBeCloseTo(39);
    expect(svg.getAttribute('aria-hidden')).toBe('true');
  });
});

describe('the chat avatar while a turn runs', () => {
  it('is the live mark on the running tool, and the bot once the turn ends', () => {
    const msg = {
      id: 'a1', role: 'agent', content: '',
      timeline: [{ type: 'tool', tool: 'web_search', running: true }],
    };
    const { container, rerender } = wrap(<MessageBubble msg={msg} isStreaming agentName="Researcher" />);
    expect(container.querySelector('svg[data-state]').getAttribute('data-state')).toBe('search-web');
    rerender(<I18nProvider><MemoryRouter><MessageBubble msg={{ ...msg, content: 'Done.' }} agentName="Researcher" /></MemoryRouter></I18nProvider>);
    expect(container.querySelector('svg[data-state]')).toBeNull();
  });
});
