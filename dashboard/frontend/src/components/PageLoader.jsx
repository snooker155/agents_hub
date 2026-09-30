import { useI18n } from '../i18n';

/*
 * The one loading indicator: a centred orbit animation in the brand colour,
 * with an optional line of text under it. `size` picks the footprint: "lg"
 * for a whole page (the route fallback and a page whose data is not there
 * yet), "md" for a card or a tab, "sm" for a small panel. The keyframes are
 * in src/index.css (ah-orbit, ah-core). Colours come from the theme tokens,
 * so the loader follows the brand palette.
 */
// "lg" takes the height of the viewport's content area (flex-1 inside a flex
// column, else a viewport-based minimum), so the animation sits in the middle
// of the page rather than at the top of it.
// One animation everywhere, identical for a page, a card and a panel: the
// ring, the dots and the core never change size, only the space around them
// does (a page fills its area, a card keeps a minimum height, a panel just
// pads).
const RING = 64;
const DOT = 10;
const SIZES = {
  lg: { box: 'flex-1 min-h-[60vh] py-12', ring: RING, dot: DOT, text: 'text-sm' },
  md: { box: 'min-h-[40vh] py-10', ring: RING, dot: DOT, text: 'text-sm' },
  sm: { box: 'py-8', ring: RING, dot: DOT, text: 'text-sm' },
};

export default function PageLoader({ label, size = 'md', className = '', showLabel = true }) {
  const { t } = useI18n();
  const s = SIZES[size] || SIZES.md;
  const text = label === undefined ? t('errorBoundary.loading') : label;
  return (
    <div role="status" aria-live="polite" aria-busy="true"
      className={`flex flex-col items-center justify-center w-full self-stretch ${s.box} ${className}`}>
      <div className="ah-loader" style={{ width: s.ring, height: s.ring, '--ah-dot': `${s.dot}px` }}>
        <span className="ah-loader__ring" />
        <span className="ah-loader__orbit"><i /></span>
        <span className="ah-loader__orbit ah-loader__orbit--2"><i /></span>
        <span className="ah-loader__core" />
      </div>
      {showLabel && text ? <p className={`mt-4 ${s.text} text-gray-500`}>{text}</p> : null}
    </div>
  );
}
