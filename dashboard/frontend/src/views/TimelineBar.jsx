import React from 'react';
import { Play, Pause, SkipForward, RotateCcw } from 'lucide-react';
import { useI18n } from '../i18n';

// Transport chrome — rendered automatically for any view whose spec declares a
// `timeline` block. Binds play/pause/step/speed to a runtime clock (useRuntime).
// A scrub slider shows elapsed time; for a bounded range it seeks within it.

const SPEEDS = [0.25, 0.5, 1, 2, 4];

export default function TimelineBar({ t, playing, onToggle, onStep, onReset, speed, onSpeed, range }) {
  // `t` is the elapsed clock value from the runtime — alias the translator.
  const { t: tr } = useI18n();
  const [t0, t1] = Array.isArray(range) && range.length === 2 ? range : [0, 0];
  const bounded = t1 > t0;
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
      <button onClick={onToggle} title={playing ? tr('viewTimelineBar.pause') : tr('viewTimelineBar.play')}
        className="p-1.5 rounded-md bg-indigo-600 text-white hover:bg-indigo-700">
        {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
      </button>
      <button onClick={onStep} title={tr('viewTimelineBar.step')} className="p-1.5 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
        <SkipForward className="w-4 h-4" />
      </button>
      <button onClick={onReset} title={tr('viewTimelineBar.reset')} className="p-1.5 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
        <RotateCcw className="w-4 h-4" />
      </button>
      <input
        type="range"
        min={bounded ? t0 : 0}
        max={bounded ? t1 : Math.max(10, Math.ceil(t))}
        step="0.01"
        value={Math.min(bounded ? t1 : Math.max(10, Math.ceil(t)), t)}
        readOnly
        className="flex-1 accent-indigo-600"
      />
      <span className="text-xs font-mono text-gray-500 tabular-nums w-14 text-right">t={t.toFixed(2)}</span>
      <select value={speed} onChange={(e) => onSpeed(Number(e.target.value))}
        className="text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-1 py-0.5">
        {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
    </div>
  );
}
