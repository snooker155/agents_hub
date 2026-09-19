import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft, Boxes, Camera, Info, Link2, Maximize2, Minimize2, RefreshCw,
  Trash2, AlertCircle,
} from 'lucide-react';
import {
  getView, applyViewOps, setViewState, deleteView, saveViewSnapshot, viewAssetUrl,
} from '../api';
import { useChannel } from '../components/stream';
import { applyOp } from '../views/opsClient';
import ViewRenderer, { FILL_KINDS } from '../views/ViewRenderer';
import ControlsPanel from '../views/ControlsPanel';
import { KindIcon } from '../views/ViewCard';

import { AppBar } from '../components/PageLayout';
import { useI18n } from '../i18n';
// The dedicated page for a single view — the whole content at full size, not a
// preview. The Studio is where a view is *edited* (outliner, chat, op history);
// this is where it is *read*: the renderer gets the entire viewport, the view's
// own controls stay usable, and the envelope's metadata/assets sit in a side
// panel. Stays live on the view's op channel, so an agent editing the view
// updates this page as it works.

// Kinds the Studio can build/edit — mirrors the gallery's list.
const STUDIO_KINDS = new Set(['graph', 'scene3d', 'simulation', 'math', 'process', 'chart', 'table', 'html', 'diagram', 'latex', 'slides', 'document']);

function formatBytes(n) {
  if (!n && n !== 0) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function Row({ label, children }) {
  if (children === null || children === undefined || children === '') return null;
  return (
    <div className="flex items-start gap-2 py-1 text-xs">
      <span className="w-24 flex-shrink-0 text-gray-400">{label}</span>
      <span className="min-w-0 break-words text-gray-700 dark:text-gray-300">{children}</span>
    </div>
  );
}

function dataSource(view) {
  const d = view?.data;
  if (!d) return null;
  if (d.file) return `file · ${d.file}`;
  if (d.url) return `url · ${d.url}`;
  if (d.inline !== undefined) return 'inline';
  return null;
}

export default function ViewDetail() {
  const { t } = useI18n();
  const { viewId } = useParams();
  const navigate = useNavigate();
  const [view, setView] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showInfo, setShowInfo] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [toast, setToast] = useState('');
  const viewportRef = useRef(null);

  const flash = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast(''), 1600);
  }, []);

  // `loading` starts true; see Views.jsx for why the flag is not set here.
  const load = useCallback(() => {
    if (!viewId) return;
    getView(viewId)
      .then((r) => { setView(r.data); setError(null); })
      .catch((e) => { setView(null); setError(e?.response?.data?.detail || t('viewDetail.notFound')); })
      .finally(() => setLoading(false));
  }, [t, viewId]);

  useEffect(() => { load(); }, [load]);

  // Live: apply agent/user ops arriving on the view channel.
  useChannel(viewId ? `view:${viewId}` : null, useCallback((ev) => {
    if (ev.type === 'view_op' && ev.op) setView((v) => (v ? applyOp(v, ev.op) : v));
    else if (ev.type === 'view_reset') setView((v) => (v ? { ...v, spec: ev.doc?.spec, controls: ev.doc?.controls } : v));
  }, []));

  // Control changes: optimistic local apply + persist as a user op.
  const onOp = useCallback((op, persist = true) => {
    setView((v) => (v ? applyOp(v, op) : v));
    if (persist && viewId) applyViewOps(viewId, [op]).catch(() => {});
  }, [viewId]);

  const onState = useCallback((s) => {
    if (viewId) setViewState(viewId, { ...(view?.state || {}), ...s }).catch(() => {});
  }, [viewId, view]);

  const onSelect = useCallback((id) => {
    if (viewId) setViewState(viewId, { ...(view?.state || {}), selection: id }).catch(() => {});
  }, [viewId, view]);

  const capture = useCallback(() => {
    const canvas = viewportRef.current?.querySelector('canvas');
    if (!canvas || !viewId) { flash(t('viewDetail.noImageToCapture')); return; }
    try {
      saveViewSnapshot(viewId, canvas.toDataURL('image/png'))
        .then(() => flash(t('viewDetail.snapshotSaved'))).catch(() => flash(t('viewDetail.saveFailed')));
    } catch { flash(t('viewDetail.captureFailed')); }
  }, [viewId, flash, t]);

  const toggleFullscreen = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else el.requestFullscreen?.().catch(() => {});
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const copyLink = useCallback(() => {
    const url = window.location.href;
    navigator.clipboard?.writeText(url).then(() => flash(t('viewDetail.linkCopied'))).catch(() => flash(t('viewDetail.copyFailed')));
  }, [flash, t]);

  const onDelete = useCallback(async () => {
    if (!window.confirm(t('viewDetail.confirmDelete'))) return;
    try { await deleteView(viewId); navigate('/views'); } catch { flash(t('viewDetail.deleteFailed')); }
  }, [t, viewId, navigate, flash]);

  const hasControls = !!(view?.controls
    && (Array.isArray(view.controls) ? view.controls.length : Object.keys(view.controls).length));
  const assets = view?.assets || [];

  const btn = 'p-1.5 rounded-md border border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800';
  // Whether this kind's renderer fills the block it is given or flows as
  // content — the same split ViewRenderer uses to decide its own wrapper.
  const fills = FILL_KINDS.has(view?.kind);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={(props) => <KindIcon kind={view?.kind} {...props} />}
        title={view?.title || 'View'}
        subtitle={view?.summary}
        backTo="/views"
        backLabel={t('viewDetail.views')}
        badges={view?.kind && (
          <span className="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 flex-shrink-0">{view.kind}</span>
        )}
        actions={<>
          {toast && <span className="text-[11px] text-gray-400 mr-1">{toast}</span>}
          {view && STUDIO_KINDS.has(view.kind) && (
            <Link to={`/studio/${viewId}`} title={t('viewDetail.editInStudio')}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm rounded-md bg-indigo-600 text-white hover:bg-indigo-700">
              <Boxes className="w-4 h-4" /> {t('viewDetail.studio')}
            </Link>
          )}
          <button onClick={capture} title={t('viewDetail.saveSnapshot')} className={btn}><Camera className="w-4 h-4" /></button>
          <button onClick={copyLink} title={t('viewDetail.copyLink')} className={btn}><Link2 className="w-4 h-4" /></button>
          <button onClick={toggleFullscreen} title={fullscreen ? t('viewDetail.exitFullscreen') : t('viewDetail.fullscreen')} className={btn}>
            {fullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
          <button onClick={load} title={t('viewDetail.reload')} className={btn}><RefreshCw className="w-4 h-4" /></button>
          <button onClick={() => setShowInfo((s) => !s)} title={t('viewDetail.details')}
            className={`${btn} ${showInfo ? 'bg-gray-100 dark:bg-gray-800 text-indigo-600' : ''}`}>
            <Info className="w-4 h-4" />
          </button>
          <button onClick={onDelete} title={t('viewDetail.deleteView')} className={`${btn} hover:text-red-600`}><Trash2 className="w-4 h-4" /></button>
        </>}
      />

      {/* body */}
      <div className="flex-1 flex min-h-0">
        {/* A scene, a graph, an html view: the renderer *is* the page, so it
            gets the whole block — no gutter around it and none inside the
            card. Content kinds (a document, a table, LaTeX) keep their
            padding: text run to the edge of a card is unreadable. */}
        <div ref={viewportRef}
             className={`flex-1 min-w-0 overflow-auto bg-gray-50 dark:bg-gray-950 ${fills ? '' : 'p-4'}`}>
          {loading && <div className="h-full grid place-items-center text-gray-400">{t('viewDetail.loadingView')}</div>}
          {error && !loading && (
            <div className="h-full grid place-items-center">
              <div className="text-center">
                <AlertCircle className="w-8 h-8 mx-auto mb-2 text-amber-500" />
                <p className="text-gray-500">{error}</p>
                <Link to="/views" className="text-sm text-indigo-600 hover:underline">{t('viewDetail.backToViews')}</Link>
              </div>
            </div>
          )}
          {view && !loading && (
            // Flex column + `grow` (flex: 1 1 auto): the renderer stretches to the
            // whole viewport when its content is shorter — so html/scene/graph
            // views fill the page instead of sitting at their min-height — but
            // never shrinks below its content, so a long table or document grows
            // the card and scrolls instead of being cut off.
            <div className={`min-h-full flex flex-col border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 ${
              fills ? 'overflow-hidden' : 'rounded-xl border p-4'}`}>
              <ViewRenderer view={view} onOp={onOp} onSelect={onSelect} onState={onState} className="grow" />
            </div>
          )}
        </div>

        {showInfo && view && (
          <aside className="w-80 flex-shrink-0 border-l border-gray-200 dark:border-gray-700 overflow-y-auto">
            {hasControls && (
              <div className="p-3 border-b border-gray-200 dark:border-gray-700">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('viewDetail.controls')}</div>
                <ControlsPanel view={view} onOp={onOp} />
              </div>
            )}
            <div className="p-3">
              <div className="text-xs font-semibold text-gray-500 mb-2">{t('viewDetail.details')}</div>
              <Row label={t('viewDetail.viewId')}><code className="font-mono">{view.view_id}</code></Row>
              <Row label={t('viewDetail.kind')}>{view.kind}</Row>
              <Row label={t('viewDetail.workspace')}>{view.workspace}</Row>
              <Row label={t('viewDetail.created')}>{formatTime(view.created_at)}</Row>
              <Row label={t('viewDetail.updated')}>{formatTime(view.updated_at)}</Row>
              <Row label={t('viewDetail.size')}>{formatBytes(view.size_bytes)}</Row>
              <Row label={t('viewDetail.data')}>{dataSource(view)}</Row>
              <Row label={t('viewDetail.fidelity')}>{view.fidelity}</Row>
              <Row label={t('viewDetail.complexity')}>{view.complexity}</Row>
              <Row label={t('viewDetail.run')}>
                {view.run_id ? <Link className="text-indigo-600 hover:underline" to={`/messages/${view.run_id}`}>{view.run_id}</Link> : null}
              </Row>
              <Row label={t('viewDetail.task')}>
                {view.task_id ? <Link className="text-indigo-600 hover:underline" to={`/tasks/${view.task_id}`}>{view.task_id}</Link> : null}
              </Row>
            </div>
            {assets.length > 0 && (
              <div className="p-3 border-t border-gray-200 dark:border-gray-700">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('viewDetail.assets')} ({assets.length})</div>
                {assets.map((a) => (
                  <a key={a} href={viewAssetUrl(viewId, a)} target="_blank" rel="noreferrer"
                    className="block text-xs text-indigo-600 hover:underline truncate py-0.5">{a}</a>
                ))}
              </div>
            )}
            {view.fallback?.text && (
              <div className="p-3 border-t border-gray-200 dark:border-gray-700">
                <div className="text-xs font-semibold text-gray-500 mb-1">{t('viewDetail.textFallback')}</div>
                <p className="text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{view.fallback.text}</p>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
