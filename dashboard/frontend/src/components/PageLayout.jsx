import { useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { ChevronLeft, Info } from 'lucide-react';
import { useI18n } from '../i18n';
import { PhoneTitleSlot } from './phoneTitleSlot';

// ─── Shared page chrome ───────────────────────────────────────────────────────
// Every page in the dashboard is built from the same two pieces so that widths,
// paddings and headings line up wherever you navigate:
//
//   <PageContainer>                     ← one gutter + one content width
//     <PageHeader icon={..} title=".."  ← icon tile, title, info hint, actions
//                 description=".." actions={<..>} />
//     ...page body...
//   </PageContainer>
//
// The description is not printed under the title: it hides behind an ⓘ button
// next to it and opens as a popover on click, so every page starts with the
// same single-line heading and the explainer stays one click away.
//
// The Layout's <main> deliberately has no padding — the container owns it, so
// full-bleed pages (Chat, FlowEditor, Studio) can opt out with width="full"
// instead of fighting the parent with negative margins.

const WIDTHS = {
  // The default: boards, card grids and tables get the whole viewport. These
  // pages are denser the wider they are — a cap would only waste screen.
  default: 'max-w-none',
  // Reading / form pages: a single column that stays comfortable to scan.
  narrow: 'max-w-3xl',
  // Edge-to-edge app surfaces that manage their own gutters and scrolling.
  full: 'max-w-none',
};

// A phone gets a tighter gutter: 24px a side is an eighth of its width, and
// the top bar over the page already holds its title (sm:phone: is a phone on
// its side, which counts as sm). No bottom padding: a page's content runs to
// the bottom edge of the window.
const PADDING = {
  default: 'px-4 pt-3 sm:px-6 sm:pt-6 sm:phone:pt-3',
  narrow: 'px-4 pt-3 sm:px-6 sm:pt-6 sm:phone:pt-3',
  full: '',
};

export const PageContainer = ({
  width = 'default',
  fill = false,
  className = '',
  children,
}) => (
  <div
    className={[
      'mx-auto w-full',
      WIDTHS[width] ?? WIDTHS.default,
      PADDING[width] ?? PADDING.default,
      fill && 'h-full min-h-0 flex flex-col',
      className,
    ].filter(Boolean).join(' ')}
  >
    {children}
  </div>
);

// ⓘ next to a heading: click to reveal the page's description, click away (or
// press Escape) to dismiss. Used by both PageHeader and AppBar so the hint
// behaves identically on tall and compact pages.
//
// The popover is drawn into <body>, placed from the button's position: a hint
// in a narrow column with clipped overflow (a flow page's side panel) would
// otherwise be cut at the column's edge and slide under the panel beside it.
const HINT_WIDTH = 320;
const HINT_GAP = 8;
const VIEWPORT_MARGIN = 12;

function hintPosition(anchor, align) {
  const r = anchor.getBoundingClientRect();
  const vw = window.innerWidth;
  const width = Math.min(HINT_WIDTH, vw - VIEWPORT_MARGIN * 2);
  const preferred = align === 'right' ? r.right - width : r.left;
  const left = Math.max(VIEWPORT_MARGIN, Math.min(preferred, vw - width - VIEWPORT_MARGIN));
  return { top: r.bottom + HINT_GAP, left, width };
}

export const InfoHint = ({ text, label, align = 'left', className = '' }) => {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  const popRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const place = () => { if (ref.current) setPos(hintPosition(ref.current, align)); };
    place();
    const onPointer = (e) => {
      if (ref.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    // Follow the button when the page or a panel under it scrolls or resizes.
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open, align]);

  if (!text) return null;

  return (
    <span ref={ref} className={`relative inline-flex shrink-0 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-label={label}
        aria-expanded={open}
        title={open ? undefined : label}
        className={`inline-flex h-6 w-6 items-center justify-center rounded-full transition-colors ${
          open
            ? 'bg-indigo-50 text-indigo-600'
            : 'text-gray-400 hover:bg-gray-100 hover:text-indigo-600'
        }`}
      >
        <Info className="h-4 w-4" />
      </button>
      {open && pos && createPortal(
        <div
          ref={popRef}
          role="tooltip"
          style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width }}
          className="z-[1000] max-h-[60vh] overflow-y-auto rounded-xl border border-gray-200 bg-white p-3 text-sm font-normal normal-case tracking-normal leading-relaxed text-gray-600 shadow-lg"
        >
          {text}
        </div>,
        document.body,
      )}
    </span>
  );
};

// The header's first line, drawn into the phone's top bar: the way back, the
// page's icon, the title and its ⓘ, and the page's actions on the right.
const BarTitle = ({ slot, icon: Icon, backTo, back, title, description, barBadges, actions, t }) => createPortal(
  <>
    {backTo && (
      <Link
        to={backTo}
        title={back}
        aria-label={back}
        className="-ml-1 inline-flex shrink-0 items-center rounded-lg p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-indigo-600"
      >
        <ChevronLeft className="h-5 w-5" />
      </Link>
    )}
    {Icon && <Icon className="h-4 w-4 shrink-0 text-indigo-600" />}
    <h1 className="min-w-0 truncate text-base font-bold text-gray-900">{title}</h1>
    {/* -ml-1: the ⓘ's own padding is its gap from the title. */}
    <InfoHint text={description} label={t('common.aboutPage')} className="-ml-1" />
    {barBadges && <div className="flex shrink-0 items-center gap-1">{barBadges}</div>}
    {/* A button with an icon keeps the icon alone up here (its text set to
        nothing), a text-only one its text; short of room they give way first
        (shrink-[100]) and scroll, the title after them. */}
    {actions && (
      <div className="ml-auto flex min-w-0 shrink-[100] items-center gap-1.5 overflow-x-auto pl-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden [&>*]:shrink-0 [&_:is(button,a):has(svg)]:gap-0 [&_:is(button,a):has(svg)]:text-[0px] [&_:is(button,a):has(svg)]:px-2 [&_:is(button,a)_svg]:m-0">
        {actions}
      </div>
    )}
  </>,
  slot,
);

export const PageHeader = ({
  icon: Icon,
  title,
  description,
  actions,
  badges,
  // The few badges that say what state the thing is in (a project's type and
  // status): beside the title in a phone's top bar, before `badges` elsewhere.
  barBadges,
  backTo,
  backLabel,
  className = '',
  children,
}) => {
  const { t } = useI18n();
  const back = backLabel ?? t('common.back');
  // On a phone the first line (the way back, the title, the actions) goes up
  // into the top bar; the page keeps its badges, and no header at all when it
  // has none.
  const slot = useContext(PhoneTitleSlot);
  if (slot) {
    const barTitle = <BarTitle slot={slot} icon={Icon} backTo={backTo} back={back} title={title} description={description} barBadges={barBadges} actions={actions} t={t} />;
    if (!badges && !children) return barTitle;
    return (
      <header className={`${/(^|\s)mb-/.test(className) ? '' : 'mb-2'} shrink-0 ${className}`}>
        {barTitle}
        {badges && <div className="flex min-w-0 items-center gap-2 flex-wrap">{badges}</div>}
        {children}
      </header>
    );
  }
  return (
  // The default bottom margin steps aside when the caller sets its own, so a
  // header dropped into a `gap-*` flex column does not stack two gaps.
  <header className={`${/(^|\s)mb-/.test(className) ? '' : 'mb-4'} shrink-0 ${className}`}>
    {/* Now that the description hides behind the ⓘ, the heading is a single
        line: the icon tile and the hint centre on it instead of hanging from
        the top the way they did next to a two-line title block. The way back
        rides on that same line — it used to own a row of its own above the
        title, which cost a line of every detail page to one short link. */}
    <div className="flex items-center justify-between gap-4 flex-wrap">
      <div className="flex items-center gap-3 min-w-0">
        {backTo && (
          <Link
            to={backTo}
            className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-gray-500 hover:text-indigo-600 transition-colors"
          >
            <ChevronLeft className="w-4 h-4 shrink-0" />
            {/* The label is the first thing to go when the row is tight: the
                chevron alone still reads as "back", and the title is what the
                width is for. */}
            <span className="hidden sm:inline">{back}</span>
          </Link>
        )}
        {/* On a phone the icon tile and the slash before it give their
            width to the title and its badges. */}
        {backTo && <span className="hidden sm:inline shrink-0 text-gray-300">/</span>}
        {Icon && (
          <span className="hidden sm:flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-indigo-100 bg-indigo-50 text-indigo-600">
            <Icon className="h-5 w-5" />
          </span>
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {/* A plain string title is clipped to one line; a rendered node
                (an inline editor, say) is left to size itself. */}
            <h1 className={`text-2xl font-bold tracking-tight text-gray-900 min-w-0 ${
              typeof title === 'string' ? 'truncate' : 'flex-1'
            }`}>{title}</h1>
            {/* The row centres its items, which leaves the small ⓘ sitting on
                the title's optical middle. A 1.5px nudge drops it onto the
                baseline so it reads as resting on the bottom of the letters. */}
            <InfoHint
              text={description}
              label={t('common.aboutPage')}
              className="-ml-1 translate-y-[1.5px]"
            />
            {barBadges}
            {badges}
          </div>
        </div>
      </div>
      {/* max-w-full: when the actions drop under the title on a narrow
          screen, they wrap within it instead of running off the side. */}
      {actions && (
        <div className="flex items-center gap-2 flex-wrap shrink-0 max-w-full">{actions}</div>
      )}
    </div>
    {children}
  </header>
  );
};

// A header action: icon and label on a wide screen, the icon alone on a phone
// (the `phone:` variant, either way up), where the label is its tooltip and
// its accessible name, so a row of actions stays one short line.
// `primary` is the page's main action (filled), `outline` a secondary one.
const ICON_BUTTON_TONES = {
  primary: 'bg-indigo-600 text-white border-indigo-600 hover:bg-indigo-700',
  outline: 'border-indigo-200 text-indigo-700 bg-white hover:bg-indigo-50',
  plain: 'border-gray-300 text-gray-600 bg-white hover:text-indigo-700 hover:border-indigo-300',
};

export const HeaderIconButton = ({ icon: Icon, label, tone = 'plain', className = '', ...rest }) => (
  <button
    type="button"
    title={label}
    aria-label={label}
    className={`inline-flex h-9 shrink-0 items-center justify-center gap-1.5 px-3 phone:w-9 phone:px-0 rounded-lg border text-sm font-medium transition-colors ${
      ICON_BUTTON_TONES[tone] ?? ICON_BUTTON_TONES.plain
    } ${className}`}
    {...rest}
  >
    <Icon className="w-4 h-4 shrink-0" />
    <span className="phone:hidden whitespace-nowrap">{label}</span>
  </button>
);

// Full-bleed app surfaces (flow canvas, studio, a single view) have no room for
// the tall <PageHeader>. They use this compact bar instead — same gutter, same
// back-link and icon treatment, so navigating between the two feels continuous.
export const AppBar = ({
  icon: Icon,
  title,
  subtitle,
  backTo,
  backLabel,
  badges,
  // Controls that belong on the title line, between it and the actions (the
  // flow run page's Canvas / Chat / Artifacts switch). `titleClassName` sizes
  // the title part, so a page can line them up over a column of its own.
  center,
  titleClassName = 'flex-1',
  // A long title wraps onto a second line instead of being cut off.
  titleWrap = false,
  actions,
}) => {
  const { t } = useI18n();
  const back = backLabel ?? t('common.back');
  // On a phone the first line (the way back, the title, the actions) goes up
  // into the top bar, as PageHeader's does; this bar keeps the badges and the
  // switch, and is not drawn when it has neither.
  const slot = useContext(PhoneTitleSlot);
  if (slot) {
    const barTitle = <BarTitle slot={slot} icon={Icon} backTo={backTo} back={back} title={title} description={subtitle} actions={actions} t={t} />;
    if (!badges && !center) return barTitle;
    return (
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-gray-200 bg-white px-3">
        {barTitle}
        {badges && <div className="flex min-w-0 items-center gap-2">{badges}</div>}
        {center && <div className="flex min-w-0 shrink items-center overflow-x-auto">{center}</div>}
      </div>
    );
  }
  return (
  <div className="flex h-14 shrink-0 items-center gap-3 border-b border-gray-200 bg-white px-3 sm:px-6">
    <div className={`flex min-w-0 items-center gap-2 ${titleClassName}`}>
      {backTo && (
        <>
          <Link
            to={backTo}
            className="inline-flex items-center gap-1 text-xs font-semibold text-gray-400 transition hover:text-indigo-600"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> {back}
          </Link>
          <span className="text-gray-300">/</span>
        </>
      )}
      {Icon && <Icon className="h-4 w-4 shrink-0 text-indigo-600" />}
      <h1
        className={`text-base font-bold text-gray-900 ${titleWrap ? 'line-clamp-2 break-words leading-tight' : 'truncate'}`}
        title={titleWrap && typeof title === 'string' ? title : undefined}
      >{title}</h1>
      <InfoHint text={subtitle} label={t('common.aboutPage')} />
      {badges}
    </div>
    {center && <div className="flex shrink-0 items-center">{center}</div>}
    {actions && <div className="ml-auto flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
  </div>
  );
};

export default PageContainer;
