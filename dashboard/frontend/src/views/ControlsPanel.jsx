import React, { useEffect, useRef, useState } from 'react';
import { Play, Pause, ChevronRight, ChevronDown } from 'lucide-react';

// Agent-authored control panel. Renders the view's `controls` (a keyed map in
// the live op shape; a list is also accepted) uniformly, and reports a change as
// an update op at the control's `bind` path via onOp — the host persists it as a
// user op so agent and user co-edit one history. Nothing here is view-specific:
// one JSON control contract → interactive controls on any kind.
//
// Types: slider/range, toggle, multi-toggle, select, color, text — plus
// `button` (fires its message back to the agent via onAction), `play`
// (animates the bound param min→max; intermediate values apply locally and only
// the final value persists, so an animation never floods the op log), and
// `folder` (collapsible grouping: child controls set `folder: <folderId>`).

function asControls(controls) {
  if (Array.isArray(controls)) return controls.filter(Boolean);
  if (controls && typeof controls === 'object') {
    return Object.entries(controls).map(([id, c]) => ({ id, ...(c || {}) }));
  }
  return [];
}

function getAt(doc, path) {
  if (!path) return undefined;
  let node = doc;
  for (const seg of String(path).split('.')) {
    if (node && typeof node === 'object' && seg in node) node = node[seg];
    else return undefined;
  }
  return node;
}

function ControlRow({ view, c, onOp, onAction }) {
  const bound = getAt(view, c.bind);
  const value = bound === undefined ? c.value : bound;
  const emit = (v, persist = true) => {
    if (!c.bind || !onOp) return;
    onOp({ op: 'update', path: c.bind, value: v }, persist);
  };
  return (
    <div className="text-sm">
      {c.type !== 'button' && (
        <label className="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">
          {c.label || c.id}
          {(c.type === 'slider' || c.type === 'range' || c.type === 'play') && value !== undefined && (
            <span className="ml-1 text-gray-400 font-mono">{typeof value === 'number' ? +value.toFixed(3) : value}</span>
          )}
        </label>
      )}
      <ControlInput c={c} value={value} onChange={emit} onAction={onAction} />
    </div>
  );
}

export default function ControlsPanel({ view, onOp, onAction }) {
  const controls = asControls(view?.controls);
  const [closed, setClosed] = useState({});
  if (!controls.length) return null;

  const folders = controls.filter((c) => c.type === 'folder');
  const inFolder = (fid) => controls.filter((c) => c.type !== 'folder' && c.folder === fid);
  const topLevel = controls.filter((c) => c.type !== 'folder'
    && (!c.folder || !folders.some((f) => f.id === c.folder)));

  return (
    <div className="space-y-3">
      {topLevel.map((c) => <ControlRow key={c.id} view={view} c={c} onOp={onOp} onAction={onAction} />)}
      {folders.map((f) => {
        const open = !closed[f.id];
        const Chevron = open ? ChevronDown : ChevronRight;
        return (
          <div key={f.id}>
            <button
              type="button"
              onClick={() => setClosed((s) => ({ ...s, [f.id]: open }))}
              className="w-full flex items-center gap-1 text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide py-1"
            >
              <Chevron className="w-3.5 h-3.5" /> {f.label || f.id}
            </button>
            {open && (
              <div className="space-y-3 pl-2 border-l border-gray-200 dark:border-gray-700 ml-1.5">
                {inFolder(f.id).map((c) => <ControlRow key={c.id} view={view} c={c} onOp={onOp} onAction={onAction} />)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Animates value from min to max over `duration` seconds while playing;
// intermediate frames apply locally (persist=false), the value on pause/finish
// persists as one user op.
function PlayControl({ c, value, onChange }) {
  const [playing, setPlaying] = useState(false);
  const raf = useRef(null);
  const valRef = useRef(typeof value === 'number' ? value : (c.min ?? 0));
  valRef.current = typeof value === 'number' ? value : valRef.current;

  useEffect(() => {
    if (!playing) return undefined;
    const min = c.min ?? 0; const max = c.max ?? 1;
    const rate = (max - min) / Math.max(0.1, c.duration ?? 5);
    let last = performance.now();
    const tick = (now) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      let v = valRef.current + rate * dt;
      if (v >= max) v = c.loop === false ? max : min;
      valRef.current = v;
      onChange(+v.toFixed(4), false);
      if (c.loop === false && v >= max) { setPlaying(false); onChange(max, true); return; }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  const stop = () => { setPlaying(false); onChange(+valRef.current.toFixed(4), true); };
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => (playing ? stop() : setPlaying(true))}
        className="p-1.5 rounded-md bg-indigo-600 text-white hover:bg-indigo-700"
        title={playing ? 'Pause' : 'Animate'}
      >
        {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
      </button>
      <input
        type="range"
        min={c.min ?? 0}
        max={c.max ?? 1}
        step={c.step ?? 0.01}
        value={typeof value === 'number' ? value : (c.min ?? 0)}
        onChange={(e) => { valRef.current = Number(e.target.value); onChange(valRef.current, true); }}
        className="flex-1 accent-indigo-600"
      />
    </div>
  );
}

function ControlInput({ c, value, onChange, onAction }) {
  const cls = 'w-full rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 text-sm';
  switch (c.type) {
    case 'slider':
    case 'range':
      return (
        <input
          type="range"
          min={c.min ?? 0}
          max={c.max ?? 100}
          step={c.step ?? 1}
          value={value ?? c.min ?? 0}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-full accent-indigo-600"
        />
      );
    case 'play':
      return <PlayControl c={c} value={value} onChange={onChange} />;
    case 'toggle':
      return (
        <button
          type="button"
          onClick={() => onChange(!value)}
          className={`px-3 py-1 rounded-md text-xs font-medium border ${value
            ? 'bg-indigo-600 text-white border-indigo-600'
            : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700'}`}
        >
          {value ? 'On' : 'Off'}
        </button>
      );
    case 'multi-toggle': {
      const selected = Array.isArray(value) ? value : [];
      return (
        <div className="flex flex-wrap gap-1">
          {(c.options || []).map((opt) => {
            const val = typeof opt === 'object' ? opt.value : opt;
            const label = typeof opt === 'object' ? opt.label : opt;
            const on = selected.includes(val);
            return (
              <button
                key={val}
                type="button"
                onClick={() => onChange(on ? selected.filter((v) => v !== val) : [...selected, val])}
                className={`px-2 py-0.5 rounded-md text-xs font-medium border ${on
                  ? 'bg-indigo-600 text-white border-indigo-600'
                  : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700'}`}
              >
                {label}
              </button>
            );
          })}
        </div>
      );
    }
    case 'select':
      return (
        <select className={cls} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
          {(c.options || []).map((opt) => {
            const val = typeof opt === 'object' ? opt.value : opt;
            const label = typeof opt === 'object' ? opt.label : opt;
            return <option key={val} value={val}>{label}</option>;
          })}
        </select>
      );
    case 'color':
      return <input type="color" value={value ?? '#2a4fbd'} onChange={(e) => onChange(e.target.value)} className="h-8 w-16 rounded" />;
    case 'button':
      return (
        <button
          type="button"
          onClick={() => onAction && onAction(c.message || c.label || c.id)}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700"
        >
          {c.label || c.id}
        </button>
      );
    case 'text':
    default:
      return <input type="text" className={cls} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />;
  }
}
