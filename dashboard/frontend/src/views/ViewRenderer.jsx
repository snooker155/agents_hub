import React, { Suspense, lazy } from 'react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import TableView from './renderers/TableView';
import ImageView from './renderers/ImageView';
import AnnotationsLayer from './AnnotationsLayer';
import { useI18n } from '../i18n';

// The kind → renderer dispatcher, mounted everywhere a view is shown (chat card,
// Studio, gallery, message/task details). Heavy renderers (chart/diagram) are
// code-split so vega/mermaid load only when such a view is actually rendered.
// Adding a kind = one entry in RENDERERS + (for a heavy one) a lazy import.

const ChartView = lazy(() => import('./renderers/ChartView'));
const DiagramView = lazy(() => import('./renderers/DiagramView'));
const GraphView = lazy(() => import('./renderers/GraphView'));
const SceneView = lazy(() => import('./renderers/SceneView'));
const HtmlView = lazy(() => import('./renderers/HtmlView'));
const LatexView = lazy(() => import('./renderers/LatexView'));
const MathView = lazy(() => import('./renderers/MathView'));
const SimView = lazy(() => import('./renderers/SimView'));
const ProcessView = lazy(() => import('./renderers/ProcessView'));
const SlidesView = lazy(() => import('./renderers/SlidesView'));
const DocumentView = lazy(() => import('./renderers/DocumentView'));

// Resolve the actually-applied theme from the DOM (ThemeContext toggles the
// `dark` class on <html>), so renderers pick the right palette under 'system'.
function resolvedTheme() {
  if (typeof document !== 'undefined' && document.documentElement.classList.contains('dark')) {
    return 'dark';
  }
  return 'light';
}

const RENDERERS = {
  markdown: ({ view }) => <MarkdownRenderer content={view?.spec?.markdown || ''} />,
  table: TableView,
  image: ImageView,
  chart: ChartView,
  diagram: DiagramView,
  graph: GraphView,
  scene3d: SceneView,
  html: HtmlView,
  latex: LatexView,
  math: MathView,
  simulation: SimView,
  process: ProcessView,
  slides: SlidesView,
  document: DocumentView,
};

// Kinds whose renderer sizes itself to the frame it is given (canvas/iframe/
// graph roots that use h-full) rather than to its content. They only fill when
// the wrapper is a definite-height flex column — content kinds must NOT get one,
// or a long table/document would be clamped to the frame and cut off.
export const FILL_KINDS = new Set(['html', 'scene3d', 'graph', 'simulation', 'slides', 'process', 'math', 'chart']);

function Fallback({ view }) {
  const text = view?.fallback?.text || view?.summary || '';
  return (
    <div className="text-sm text-gray-600 dark:text-gray-300">
      <div className="mb-1 text-xs font-medium text-amber-600">
        No renderer for view kind “{view?.kind}”.
      </div>
      {text && <p>{text}</p>}
    </div>
  );
}

export default function ViewRenderer({ view, onSelect, onSendToAgent, onState, onOp, className = '' }) {
  const { t } = useI18n();
  if (!view) return null;
  const Renderer = RENDERERS[view.kind] || Fallback;
  const theme = resolvedTheme();
  const explain = (ann) => onSendToAgent && onSendToAgent(
    `Explain this ${ann.type || 'annotation'} on the "${view.title || view.kind}" view: ${ann.latex || ann.text || ann.title || ann.id}`,
  );
  const fill = FILL_KINDS.has(view.kind);
  // Fill mode: a flex column whose renderer root is `flex: 1 1 0`. The roots use
  // `h-full`, but a percentage height does not resolve against a wrapper that is
  // itself content-sized — Chrome falls back to the root's min-height (the 360px
  // iframe bug). Flex-basis needs no resolvable parent height, so the root fills
  // whatever the host gave the wrapper; its min-h-[…] fallback still applies when
  // the host has no height to give. Annotations/badge are absolute, so they are
  // not flex items and are unaffected.
  return (
    <div className={`view-renderer relative ${fill ? 'flex flex-col min-h-0 [&>*]:flex-1' : ''} ${className}`}>
      <Suspense fallback={<div className="text-sm text-gray-400 py-6 text-center">{t('viewViewRenderer.loadingView')}</div>}>
        <Renderer view={view} theme={theme} onSelect={onSelect} onSendToAgent={onSendToAgent} onState={onState} onOp={onOp} />
      </Suspense>
      <AnnotationsLayer view={view} onExplain={onSendToAgent ? explain : undefined} />
      {view.fidelity && (
        <span
          title={view.fidelity === 'precise' ? t('viewViewRenderer.preciseResult') : t('viewViewRenderer.realtimeApprox')}
          className={`absolute bottom-2 left-2 z-10 px-1.5 py-0.5 rounded text-[10px] font-medium ${
            view.fidelity === 'precise'
              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300'
              : 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300'}`}
        >
          {view.fidelity}
        </span>
      )}
    </div>
  );
}
