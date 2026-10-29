/**
 * The welcome's opening: the hub's mark putting itself together. Two soft
 * waves leave the centre one after the other, the core springs up, and once
 * the second wave is out the three satellites ride out of it along their spokes to their places, each spoke
 * drawn behind its satellite, then one glow runs round the whole mark and it
 * settles into a slow breath.
 *
 * Drawn on the exact logo coordinates (public/logo.svg, liveMark/poses.js
 * LOGO_SATS, LOGO_SPOKES), so the settled mark is the logo itself. The motion
 * is CSS only (firstRun.css, `mark-reveal-*`); with reduced motion the mark is
 * simply there. `onDone` is called once the mark has settled.
 */
import { useEffect } from 'react';
import { LOGO_SATS, LOGO_SPOKES } from '../liveMark/poses';

const FILL = 'var(--brand-500, #3f66d8)';
const GLOW = 'var(--brand-400, #6389ea)';
// When the mark has settled, in ms (the last satellite's landing).
export const MARK_REVEAL_MS = 2150;
// The waves, and when each satellite sets out with its spoke: after the
// second wave has gone out, 840 ms each (firstRun.css).
const WAVES_AT = [80, 480];
const SAT_AT = (i) => 1000 + i * 144;

export default function MarkReveal({ size = 272, onDone, label = 'Agents Hub' }) {
  useEffect(() => {
    if (!onDone) return undefined;
    const reduced = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const id = setTimeout(onDone, reduced ? 0 : MARK_REVEAL_MS);
    return () => clearTimeout(id);
  }, [onDone]);

  return (
    <svg
      // The logo spans 3.5..44.5 by 1.5..38.5: a square round its middle, with
      // room for the breath and the halo.
      viewBox="-2 -6 52 52"
      width={size}
      height={size}
      overflow="visible"
      // Never wider than a phone leaves room for.
      style={{ maxWidth: '72vw', height: 'auto' }}
      fill="none"
      role="img"
      aria-label={label}
      className="mark-reveal"
      data-testid="mark-reveal"
    >
      <defs>
        <radialGradient id="mark-reveal-halo">
          <stop offset="0%" stopColor={GLOW} stopOpacity="0.45" />
          <stop offset="100%" stopColor={GLOW} stopOpacity="0" />
        </radialGradient>
      </defs>
      <g>
        <circle className="mark-reveal-halo" cx="24" cy="21" r="24" fill="url(#mark-reveal-halo)" />
        {WAVES_AT.map((at) => (
          <circle key={at} className="mark-reveal-wave" cx="24" cy="24" r="9" stroke={GLOW} strokeWidth="0.6"
            style={{ animationDelay: `${at}ms` }} />
        ))}
        <g className="mark-reveal-breath">
          <g stroke={FILL} strokeWidth="2.5" strokeLinecap="round" strokeOpacity="0.55">
            {LOGO_SPOKES.map(([x1, y1, x2, y2], i) => (
              <line key={i} className="mark-reveal-spoke" x1={x1} y1={y1} x2={x2} y2={y2}
                pathLength="1" style={{ animationDelay: `${SAT_AT(i)}ms` }} />
            ))}
          </g>
          {LOGO_SATS.map(([x, y], i) => (
            // Starts under the core, (--dx, --dy) back from its place, and rides out along the spoke.
            <circle key={i} className="mark-reveal-sat" cx={x} cy={y} r="4.5" fill={FILL} fillOpacity="0.75"
              style={{
                animationDelay: `${SAT_AT(i)}ms`, transformOrigin: `${x}px ${y}px`,
                '--dx': `${24 - x}px`, '--dy': `${24 - y}px`,
              }} />
          ))}
          <circle className="mark-reveal-core" cx="24" cy="24" r="7" fill={FILL} />
          <circle className="mark-reveal-shine" cx="24" cy="24" r="7" fill="white" />
        </g>
      </g>
    </svg>
  );
}
