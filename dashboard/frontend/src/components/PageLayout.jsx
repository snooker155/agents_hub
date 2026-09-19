import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, Info } from 'lucide-react';
import { useI18n } from '../i18n';

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

const PADDING = {
  default: 'px-6 py-6',
  narrow: 'px-6 py-6',
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
      fill ? 'h-full min-h-0 flex flex-col' : '',
      className,
    ].filter(Boolean).join(' ')}
  >
    {children}
  </div>
);

// ⓘ next to a heading: click to reveal the page's description, click away (or
// press Escape) to dismiss. Used by both PageHeader and AppBar so the hint
// behaves identically on tall and compact pages.
export const InfoHint = ({ text, label, align = 'left', className = '' }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

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
      {open && (
        <div
          role="tooltip"
          className={`absolute top-full z-50 mt-2 w-80 max-w-[calc(100vw-3rem)] rounded-xl border border-gray-200 bg-white p-3 text-sm leading-relaxed text-gray-600 shadow-lg ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {text}
        </div>
      )}
    </span>
  );
};

export const PageHeader = ({
  icon: Icon,
  title,
  description,
  actions,
  badges,
  backTo,
  backLabel,
  className = '',
  children,
}) => {
  const { t } = useI18n();
  const back = backLabel ?? t('common.back');
  return (
  // The default bottom margin steps aside when the caller sets its own, so a
  // header dropped into a `gap-*` flex column does not stack two gaps.
  <header className={`${/(^|\s)mb-/.test(className) ? '' : 'mb-6'} shrink-0 ${className}`}>
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
        {backTo && <span className="shrink-0 text-gray-300">/</span>}
        {Icon && (
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-indigo-100 bg-indigo-50 text-indigo-600">
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
              className="translate-y-[1.5px]"
            />
            {badges}
          </div>
        </div>
      </div>
      {actions && (
        <div className="flex items-center gap-2 flex-wrap shrink-0">{actions}</div>
      )}
    </div>
    {children}
  </header>
  );
};

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
  actions,
}) => {
  const { t } = useI18n();
  const back = backLabel ?? t('common.back');
  return (
  <div className="flex h-14 shrink-0 items-center gap-3 border-b border-gray-200 bg-white px-6">
    <div className="flex min-w-0 flex-1 items-center gap-2">
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
      <h1 className="truncate text-base font-bold text-gray-900">{title}</h1>
      <InfoHint text={subtitle} label={t('common.aboutPage')} />
      {badges}
    </div>
    {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
  </div>
  );
};

export default PageContainer;
