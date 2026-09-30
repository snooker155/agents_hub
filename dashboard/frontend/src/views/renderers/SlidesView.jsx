import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronLeft, ChevronRight, FileDown, LayoutGrid, Maximize2, Minimize2, Presentation, StickyNote,
} from 'lucide-react';
import { printHtml } from '../printView';
import { exportViewPptx, viewAssetUrl } from '../../api';
import { saveBlobAs } from '../../api/files';
import { useI18n } from '../../i18n';
import SlideStage from '../slides/SlideStage';
import SlideMarkdown from '../slides/SlideMarkdown';
import { STAGE_H, STAGE_W, deckTheme, orderedSlides } from '../slides/deck';

// Slides renderer: the deck on a 16:9 stage scaled to the frame, with
// thumbnails, speaker notes, a full-screen presentation mode and two exports:
// PDF (every slide as a 16:9 page through the browser's print dialog) and
// .pptx (built on the server by views/slides_pptx.py with the same layouts).
// Keys work while the viewer has focus (click it): arrows, PageUp/PageDown,
// space, Home/End, F for full screen, N for notes.

const THUMB_SCALE = 0.125;

function resolveImageFor(view) {
  return (ref) => {
    const src = String(ref || '').trim();
    if (!src) return '';
    if (/^(https?:|data:|\/)/i.test(src)) return src;
    const name = src.startsWith('asset://') ? src.slice('asset://'.length) : src;
    return view?.view_id ? viewAssetUrl(view.view_id, name) : name;
  };
}

function sectionNumbers(slides) {
  let n = 0;
  return slides.map((s) => (s.layout === 'section' ? (n += 1) : n));
}

function Scaled({ scale, children }) {
  return (
    <div style={{ width: STAGE_W * scale, height: STAGE_H * scale, overflow: 'hidden', flex: '0 0 auto' }}>
      <div style={{ width: STAGE_W, height: STAGE_H, transform: `scale(${scale})`, transformOrigin: 'top left' }}>
        {children}
      </div>
    </div>
  );
}

function useFitScale(ref) {
  const [scale, setScale] = useState(0.5);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (w > 0 && h > 0) setScale(Math.max(0.1, Math.min(w / STAGE_W, h / STAGE_H)));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return scale;
}

const PRINT_CSS = `
  @page { size: ${STAGE_W}px ${STAGE_H}px; margin: 0; }
  html, body { margin: 0 !important; padding: 0 !important; max-width: none !important; background: #fff; }
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .slide-page { width: ${STAGE_W}px; height: ${STAGE_H}px; overflow: hidden; position: relative;
                page-break-after: always; break-after: page; }
  .slide-page:last-child { page-break-after: auto; break-after: auto; }
  .slide-page th, .slide-page td { border-width: 1px; }
`;

export default function SlidesView({ view }) {
  const { t } = useI18n();
  const spec = view?.spec || {};
  const slides = useMemo(() => orderedSlides(spec.slides), [spec.slides]);
  const sections = useMemo(() => sectionNumbers(slides), [slides]);
  const resolveImage = useMemo(() => resolveImageFor(view), [view]);
  const [idx, setIdx] = useState(0);
  const [thumbs, setThumbs] = useState(false);
  const [notes, setNotes] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [printing, setPrinting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const rootRef = useRef(null);
  const frameRef = useRef(null);
  const printRef = useRef(null);
  const scale = useFitScale(frameRef);

  const n = slides.length;
  const cur = Math.min(idx, Math.max(0, n - 1));
  const hasNotes = slides.some((s) => (s.notes || '').trim());
  const go = useCallback((d) => setIdx((i) => Math.max(0, Math.min(n - 1, i + d))), [n]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === rootRef.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    const el = rootRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else el.requestFullscreen?.().then(() => el.focus()).catch(() => {});
  }, []);

  const onKeyDown = (e) => {
    const tag = (e.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target?.isContentEditable) return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const key = e.key;
    if (key === 'ArrowRight' || key === 'PageDown' || key === ' ') go(1);
    else if (key === 'ArrowLeft' || key === 'PageUp') go(-1);
    else if (key === 'Home') setIdx(0);
    else if (key === 'End') setIdx(n - 1);
    else if (key === 'f' || key === 'F') toggleFullscreen();
    else if (key === 'n' || key === 'N') setNotes((v) => !v);
    else return;
    e.preventDefault();
  };

  // PDF: render every slide off screen at stage size, then print that HTML.
  useEffect(() => {
    if (!printing) return undefined;
    const timer = setTimeout(() => {
      const html = printRef.current?.innerHTML || '';
      printHtml(view?.title || 'Slides', html, PRINT_CSS);
      setPrinting(false);
    }, 60);
    return () => clearTimeout(timer);
  }, [printing, view?.title]);

  const exportPptx = async () => {
    if (!view?.view_id) return;
    setBusy(true);
    setError('');
    try {
      const res = await exportViewPptx(view.view_id);
      const name = `${(view.title || 'slides').replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'slides'}.pptx`;
      saveBlobAs(res.data, name);
    } catch {
      setError(t('viewSlidesView.exportFailed'));
    } finally {
      setBusy(false);
    }
  };

  if (!n) return <div className="text-sm text-gray-500 p-4">{t('viewSlidesView.emptyDeckTheAgentWill')}</div>;
  const slide = slides[cur];
  const stageFor = (s, i) => (
    <SlideStage slide={s} spec={spec} number={i + 1} total={n} sectionNo={sections[i]} resolveImage={resolveImage} />
  );
  const btn = 'p-1.5 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800';
  const toggleBtn = (on) => `${btn} ${on ? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100' : ''}`;
  const theme = deckTheme(spec);

  return (
    <div
      ref={rootRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-roledescription="slide deck"
      className={`flex flex-col h-full outline-none ${fullscreen ? 'bg-black' : ''}`}
      data-testid="slides-view"
    >
      <div ref={frameRef} className={`flex-1 min-h-[240px] flex items-center justify-center ${fullscreen ? '' : 'p-3 bg-gray-100 dark:bg-gray-950'}`}>
        <div className={fullscreen ? '' : 'shadow-md rounded-sm overflow-hidden'} data-testid="slide-stage">
          <Scaled scale={fullscreen ? scale : Math.max(0.1, scale * 0.98)}>{stageFor(slide, cur)}</Scaled>
        </div>
      </div>

      {notes && (
        <div className="max-h-40 overflow-auto px-4 py-2 border-t border-gray-200 dark:border-gray-700 bg-amber-50/60 dark:bg-gray-900 text-sm" data-testid="slide-notes">
          {(slide.notes || '').trim()
            ? <SlideMarkdown md={slide.notes} size={14} t={{ ...theme, text: 'inherit', muted: 'inherit', bg: 'transparent', surface: 'rgba(0,0,0,0.05)' }} accent={theme.accent} />
            : <span className="text-gray-500">{t('viewSlidesView.noNotes')}</span>}
        </div>
      )}

      {thumbs && !fullscreen && (
        <div className="flex gap-2 overflow-x-auto px-3 py-2 border-t border-gray-200 dark:border-gray-700" data-testid="slide-thumbs">
          {slides.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setIdx(i)}
              title={`${i + 1}. ${s.title || ''}`}
              aria-current={i === cur ? 'true' : undefined}
              className={`relative flex-none rounded border-2 overflow-hidden ${i === cur ? 'border-blue-500' : 'border-transparent hover:border-gray-300 dark:hover:border-gray-600'}`}
            >
              <Scaled scale={THUMB_SCALE}>{stageFor(s, i)}</Scaled>
              <span className="absolute bottom-0.5 left-1 text-[10px] font-medium text-white bg-black/50 rounded px-1">{i + 1}</span>
            </button>
          ))}
        </div>
      )}

      <div className={`flex items-center gap-2 px-3 py-2 border-t ${fullscreen ? 'border-gray-800 bg-black/80' : 'border-gray-200 dark:border-gray-700'}`}>
        <button type="button" onClick={() => go(-1)} disabled={cur === 0} className={btn} aria-label={t('viewSlidesView.prev')}>
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="text-xs text-gray-500 tabular-nums min-w-[3.5rem] text-center" data-testid="slide-counter">{cur + 1} / {n}</span>
        <button type="button" onClick={() => go(1)} disabled={cur === n - 1} className={btn} aria-label={t('viewSlidesView.next')}>
          <ChevronRight className="w-4 h-4" />
        </button>
        <div className="flex-1 h-1 rounded bg-gray-200 dark:bg-gray-800 overflow-hidden mx-1">
          <div className="h-full" style={{ width: `${((cur + 1) / n) * 100}%`, background: theme.accent }} />
        </div>
        {!fullscreen && (
          <button type="button" onClick={() => setThumbs((v) => !v)} className={toggleBtn(thumbs)} title={t('viewSlidesView.thumbnails')} aria-pressed={thumbs}>
            <LayoutGrid className="w-4 h-4" />
          </button>
        )}
        {hasNotes && (
          <button type="button" onClick={() => setNotes((v) => !v)} className={toggleBtn(notes)} title={t('viewSlidesView.notes')} aria-pressed={notes}>
            <StickyNote className="w-4 h-4" />
          </button>
        )}
        <button type="button" onClick={toggleFullscreen} className={btn} title={fullscreen ? t('viewSlidesView.exitPresent') : t('viewSlidesView.present')}>
          {fullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
        </button>
        {!fullscreen && (
          <>
            <button type="button" onClick={() => setPrinting(true)} disabled={printing} title={t('viewSlidesView.exportPdf')}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
              <FileDown className="w-3.5 h-3.5" /> {t('viewSlidesView.pdf')}
            </button>
            {view?.view_id && (
              <button type="button" onClick={exportPptx} disabled={busy} title={t('viewSlidesView.exportPptx')}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                <Presentation className="w-3.5 h-3.5" /> {busy ? t('viewSlidesView.exporting') : t('viewSlidesView.pptx')}
              </button>
            )}
          </>
        )}
      </div>
      {error && <div className="px-3 pb-2 text-xs text-red-600" role="alert">{error}</div>}

      {printing && (
        <div aria-hidden="true" style={{ position: 'fixed', left: -100000, top: 0, width: STAGE_W, pointerEvents: 'none' }} ref={printRef}>
          {slides.map((s, i) => (
            <div key={s.id} className="slide-page">{stageFor(s, i)}</div>
          ))}
        </div>
      )}
    </div>
  );
}
