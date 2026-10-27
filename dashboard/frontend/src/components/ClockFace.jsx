import { useRef } from 'react';

// A clock dial for picking an hour or a minute, as DateInput's time picker.
// Hours sit on two rings, 1–12 outside and 13–23 with 00 inside, so a
// 24-hour time is one click. Minutes sit every five on one ring; a click or a
// drag anywhere on the dial takes the nearest single minute.

const pad = (n) => String(n).padStart(2, '0');
// Minutes two digits; hours as a clock writes them, midnight as 00.
const labelOf = (mode, n) => (mode === 'minutes' || n === 0 ? pad(n) : String(n));

const SIZE = 208;
const C = SIZE / 2;
const OUTER = 82;
const INNER = 54;
const DOT = 28;

const at = (angleDeg, radius) => {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return { x: C + radius * Math.cos(a), y: C + radius * Math.sin(a) };
};

const HOURS = [
  ...Array.from({ length: 12 }, (_, i) => ({ value: i + 1, angle: (i + 1) * 30, radius: OUTER })),
  ...Array.from({ length: 12 }, (_, i) => ({ value: i === 0 ? 0 : i + 12, angle: i * 30, radius: INNER })),
];
const MINUTES = Array.from({ length: 12 }, (_, i) => ({ value: i * 5, angle: i * 30, radius: OUTER }));

/** Where a value sits on the dial: its angle and ring. */
function placeOf(mode, value) {
  if (value == null) return null;
  if (mode === 'minutes') return { angle: value * 6, radius: OUTER };
  return HOURS.find((h) => h.value === value) || null;
}

/** The value under a point of the dial. */
function valueAt(mode, x, y) {
  const dx = x - C;
  const dy = y - C;
  const angle = (Math.atan2(dy, dx) * 180) / Math.PI + 90;
  const deg = (angle + 360) % 360;
  if (mode === 'minutes') return Math.round(deg / 6) % 60;
  const h12 = Math.round(deg / 30) % 12;
  const inner = Math.hypot(dx, dy) < (OUTER + INNER) / 2;
  if (inner) return h12 === 0 ? 0 : h12 + 12;
  return h12 === 0 ? 12 : h12;
}

/**
 * @param {'hours'|'minutes'} mode
 * @param {number|null} value   the picked hour (0–23) or minute (0–59).
 * @param {function} onChange   each value the pointer passes over.
 * @param {function} onDone     the value the pointer was let go on.
 * @param {string} label        the dial's accessible name.
 */
export default function ClockFace({ mode, value, onChange, onDone, label }) {
  const dialRef = useRef(null);
  const dragging = useRef(false);
  const marks = mode === 'minutes' ? MINUTES : HOURS;
  const place = placeOf(mode, value);
  const hand = place ? at(place.angle, place.radius) : null;

  const pointAt = (e) => {
    const rect = dialRef.current.getBoundingClientRect();
    return valueAt(mode, e.clientX - rect.left, e.clientY - rect.top);
  };
  const onPointerDown = (e) => {
    if (e.button != null && e.button !== 0) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    onChange(pointAt(e));
  };
  const onPointerMove = (e) => {
    if (dragging.current) onChange(pointAt(e));
  };
  const onPointerUp = (e) => {
    if (!dragging.current) return;
    dragging.current = false;
    onDone(pointAt(e));
  };

  return (
    <div
      ref={dialRef}
      role="listbox"
      aria-label={label}
      data-testid={`clock-${mode}`}
      className="relative mx-auto rounded-full bg-gray-100 select-none touch-none cursor-pointer"
      style={{ width: SIZE, height: SIZE }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => { dragging.current = false; }}
    >
      {/* currentColor from a text class: the theme (src/theme.css) maps the
          text and background colours, not stroke-* and fill-*. */}
      <svg className="absolute inset-0 pointer-events-none text-indigo-600" width={SIZE} height={SIZE} aria-hidden="true">
        {hand && (
          <>
            <line x1={C} y1={C} x2={hand.x} y2={hand.y} stroke="currentColor" strokeWidth="2" />
            <circle cx={C} cy={C} r="3" fill="currentColor" />
            <circle cx={hand.x} cy={hand.y} r={DOT / 2} fill="currentColor" />
          </>
        )}
      </svg>
      {marks.map((m) => {
        const p = at(m.angle, m.radius);
        const on = value === m.value;
        const inner = m.radius === INNER;
        return (
          <button
            key={m.value}
            type="button"
            role="option"
            aria-selected={on}
            aria-label={labelOf(mode, m.value)}
            // The dial takes the pointer; the buttons are there for the keyboard.
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => { onChange(m.value); onDone(m.value); }}
            className={`absolute flex items-center justify-center rounded-full ${inner ? 'text-[11px]' : 'text-sm'} ${
              on ? 'text-white font-semibold' : inner ? 'text-gray-500 hover:bg-gray-200' : 'text-gray-700 hover:bg-gray-200'
            }`}
            style={{ width: DOT, height: DOT, left: p.x - DOT / 2, top: p.y - DOT / 2 }}
          >
            {labelOf(mode, m.value)}
          </button>
        );
      })}
    </div>
  );
}
