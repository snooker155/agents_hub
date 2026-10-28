/**
 * Reasoning effort pickers (providers/reasoning_profile.py). What a value may
 * be depends on the model, so the choices come from its profile's `efforts`;
 * the empty choice inherits, and its label says what it resolves to.
 */
import { useState } from 'react';
import { useI18n } from '../i18n';
import { effortLabel } from './effort';

/** A select: '' inherits (`inheritLabel`), else one of `efforts`. A stored
 * value the model does not list stays visible, so it is never lost silently. */
export function EffortSelect({ efforts = [], value, onChange, inheritLabel, disabled, className = '', ariaLabel }) {
  const { t } = useI18n();
  const options = value && !efforts.includes(value) ? [...efforts, value] : efforts;
  return (
    <select
      value={value || ''}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      aria-label={ariaLabel || t('effort.label')}
      className={`border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none disabled:opacity-50 disabled:bg-gray-50 ${className}`}
    >
      <option value="">{inheritLabel || t('effort.inherit')}</option>
      {options.map((e) => <option key={e} value={e}>{effortLabel(t, e)}</option>)}
    </select>
  );
}

/** A slider over the model's efforts (the header's picker). Its leftmost stop
 * is the model's own default (`value` null); the chosen value is named in the
 * title line, next to `title`. Dragging only moves it, the choice is sent when
 * the slider is let go, so one drag is one save. */
export function EffortSlider({ efforts = [], value, onChange, title, hint, defaultLabel }) {
  const { t } = useI18n();
  const stops = [null, ...efforts];
  const committed = Math.max(0, stops.indexOf(value || null));
  const [drag, setDrag] = useState(null);
  const pos = drag ?? committed;
  const commit = () => {
    if (drag === null) return;
    setDrag(null);
    if (drag !== committed) onChange(stops[drag]);
  };
  const shown = stops[pos] ? effortLabel(t, stops[pos]) : defaultLabel;
  return (
    // w-0 + min-w-full: as wide as the menu around it, never widening it;
    // the labels wrap onto two lines instead, as the title does.
    <div className="w-0 min-w-full">
      <div className="flex items-start justify-between gap-2" title={hint}>
        <span className="min-w-0 text-[10px] font-semibold uppercase tracking-wider leading-tight text-gray-400">{title}</span>
        <span className={`min-w-0 max-w-[55%] text-right text-xs font-medium leading-tight ${stops[pos] ? 'text-indigo-600' : 'text-gray-500'}`}>{shown}</span>
      </div>
      <input
        type="range"
        min={0}
        max={stops.length - 1}
        step={1}
        value={pos}
        onChange={(e) => setDrag(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
        aria-label={title}
        aria-valuetext={shown}
        className="w-full accent-indigo-600 mt-1"
      />
      <div className="flex items-start justify-between gap-2 text-[9px] leading-tight text-gray-400 -mt-0.5">
        <span className="min-w-0 max-w-[45%]">{t('effort.headerAuto')}</span>
        <span className="min-w-0 max-w-[45%] text-right">{effortLabel(t, efforts[efforts.length - 1])}</span>
      </div>
    </div>
  );
}
