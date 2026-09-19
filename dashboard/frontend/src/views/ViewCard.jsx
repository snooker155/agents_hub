import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart3, Table2, GitBranch, FileText, Image as ImageIcon,
  Box, Maximize2, ExternalLink, X, AlertCircle, Boxes, Code2, Sigma, Atom, Waypoints, Activity,
  Presentation, FileText as FileDoc, Camera,
} from 'lucide-react';
import { getView, applyViewOps, saveViewSnapshot } from '../api';
import { applyOp as applyOpLocal } from './opsClient';
import ViewRenderer, { FILL_KINDS } from './ViewRenderer';
import { useI18n } from '../i18n';

// A view card — the surface-agnostic embed for a view reference (view_ref). It
// fetches the full envelope by id, shows a titled header + rendered body, and
// pops out to a fullscreen modal. Used in chat bubbles and anywhere a stored
// view is referenced.

const KIND_ICON = {
  chart: BarChart3,
  table: Table2,
  diagram: GitBranch,
  markdown: FileText,
  image: ImageIcon,
  graph: GitBranch,
  scene3d: Boxes,
  html: Code2,
  latex: Sigma,
  math: Activity,
  simulation: Atom,
  process: Waypoints,
  slides: Presentation,
  document: FileDoc,
};

export function KindIcon({ kind, className }) {
  const Icon = KIND_ICON[kind] || Box;
  return <Icon className={className} />;
}

function useView(viewId) {
  const { t } = useI18n();
  // The id the data belongs to travels with it. While it differs from the id
  // being asked for, the card reports "loading" rather than the previous view —
  // which is what the reset-before-fetch used to do, minus the extra render.
  const [state, setState] = useState({ id: null, view: null, error: null, loading: true });
  useEffect(() => {
    let cancelled = false;
    if (!viewId) return undefined;
    getView(viewId)
      .then((r) => { if (!cancelled) setState({ id: viewId, view: r.data, error: null, loading: false }); })
      .catch((e) => {
        if (!cancelled) setState({ id: viewId, view: null, error: e?.response?.data?.detail || t('viewViewCard.viewUnavailable'), loading: false });
      });
    return () => { cancelled = true; };
  }, [t, viewId]);
  const setView = useCallback((updater) => setState((s) => ({ ...s, view: typeof updater === 'function' ? updater(s.view) : updater })), []);
  const fresh = (state.id ?? null) === (viewId ?? null);
  return {
    view: fresh ? state.view : null,
    error: fresh ? state.error : null,
    loading: !fresh || state.loading,
    setView,
  };
}

// `fill` — kinds that size themselves to the frame (FILL_KINDS) need a
// *definite* panel height, otherwise the auto-height body leaves them at their
// min-height. Content-sized kinds keep the shrink-to-fit panel.
function Modal({ children, onClose, title, fill = false }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className={`bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-6xl flex flex-col ${fill ? 'h-[90vh]' : 'max-h-[90vh]'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <h3 className="font-semibold text-gray-900 dark:text-gray-100 truncate">{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 min-h-0 flex flex-col p-4 overflow-auto">{children}</div>
      </div>
    </div>
  );
}

// `actions` — extra header buttons (rendered before snapshot/expand) so hosts
// like the Views gallery don't have to overlay their own controls on the card.
// `compact` — caps the body height for dense grid layouts.
export default function ViewCard({ viewRef, embedded = true, actions = null, compact = false }) {
  const { t } = useI18n();
  const viewId = viewRef?.view_id;
  const { view, error, loading, setView } = useView(viewId);
  const [expanded, setExpanded] = useState(false);
  const close = useCallback(() => setExpanded(false), []);

  const title = view?.title || viewRef?.title || 'View';
  const summary = view?.summary || viewRef?.summary || '';
  const kind = view?.kind || viewRef?.view_kind;

  const bodyRef = useRef(null);

  // Interactive control/param changes: optimistic local apply + persist as a
  // user op (echoed on the view channel for any Studio watching the same view).
  const onOp = useCallback((op) => {
    setView((v) => (v ? applyOpLocal(v, op) : v));
    if (viewId) applyViewOps(viewId, [op]).catch(() => {});
  }, [viewId, setView]);

  // Best-effort snapshot: capture a <canvas> in the rendered view (chart, scene,
  // simulation, math) and store it as the view's fallback image.
  const [snapMsg, setSnapMsg] = useState('');
  const capture = useCallback(() => {
    const canvas = bodyRef.current?.querySelector('canvas');
    if (!canvas || !viewId) { setSnapMsg(t('viewViewCard.noImageToCapture')); setTimeout(() => setSnapMsg(''), 1500); return; }
    try {
      const dataUrl = canvas.toDataURL('image/png');
      saveViewSnapshot(viewId, dataUrl).then(() => { setSnapMsg(t('viewViewCard.snapshotSaved')); setTimeout(() => setSnapMsg(''), 1500); })
        .catch(() => { setSnapMsg(t('viewViewCard.saveFailed')); setTimeout(() => setSnapMsg(''), 1500); });
    } catch { setSnapMsg(t('viewViewCard.captureFailed')); setTimeout(() => setSnapMsg(''), 1500); }
  }, [t, viewId]);

  return (
    <div className={embedded ? 'mt-2 border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden bg-white dark:bg-gray-900' : ''}>
      <div className="flex items-start justify-between gap-2 px-3 py-2 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-start gap-2 min-w-0">
          <KindIcon kind={kind} className="w-4 h-4 mt-0.5 text-indigo-600 flex-shrink-0" />
          <div className="min-w-0">
            {viewId ? (
              <Link to={`/views/${viewId}`} title={t('viewViewCard.openFullView')}
                className="block text-sm font-medium text-gray-900 dark:text-gray-100 truncate no-underline hover:text-indigo-600 transition-colors">
                {title}
              </Link>
            ) : (
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{title}</div>
            )}
            {summary && <div className="text-xs text-gray-500 dark:text-gray-400 line-clamp-2">{summary}</div>}
          </div>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {snapMsg && <span className="text-[10px] text-gray-400">{snapMsg}</span>}
          {viewId && (
            <Link to={`/views/${viewId}`} title={t('viewViewCard.openFullView')}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-indigo-600">
              <ExternalLink className="w-4 h-4" />
            </Link>
          )}
          {actions}
          {view && (
            <>
              <button onClick={capture} title={t('viewViewCard.saveSnapshot')}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500">
                <Camera className="w-4 h-4" />
              </button>
              <button onClick={() => setExpanded(true)} title={t('viewViewCard.expand')}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500">
                <Maximize2 className="w-4 h-4" />
              </button>
            </>
          )}
        </div>
      </div>

      <div className={compact ? 'p-3 max-h-72 overflow-auto' : 'p-3'} ref={bodyRef}>
        {loading && <div className="text-sm text-gray-400 py-4 text-center">{t('viewViewCard.loadingView')}</div>}
        {error && (
          <div className="flex items-center gap-2 text-sm text-gray-500 py-3">
            <AlertCircle className="w-4 h-4 text-amber-500" /> {error}
          </div>
        )}
        {view && <ViewRenderer view={view} onOp={onOp} />}
      </div>

      {expanded && view && (
        <Modal title={title} onClose={close} fill={FILL_KINDS.has(view.kind)}>
          <ViewRenderer view={view} onOp={onOp}
            className={FILL_KINDS.has(view.kind) ? 'flex-1 min-h-0' : ''} />
        </Modal>
      )}
    </div>
  );
}
