import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ImageDown, Download, FolderInput } from 'lucide-react';
import { uploadWorkspaceFileObject, saveBlobAs } from '../api/files';
import { useWorkspace } from '../components/workspace';
import { useToast, errorDetail } from '../components/toast';
import { useI18n } from '../i18n';
import { captureViewImage, sanitizeFilename } from './viewImage';

// "Image" action of a view: PNG of the rendered view, downloaded or saved as a
// workspace file. `getRoot` returns the element that holds the rendered view.

export default function ViewImageMenu({ view, getRoot, buttonClass = '', iconClass = 'w-4 h-4' }) {
  const { t } = useI18n();
  const toast = useToast();
  const { selectedWorkspace } = useWorkspace() || {};
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const run = useCallback(async (mode) => {
    setOpen(false);
    setBusy(true);
    try {
      const container = getRoot?.();
      const root = container?.querySelector?.('.view-renderer') || container;
      let blob;
      try {
        blob = await captureViewImage(root, { kind: view?.kind });
      } catch (e) {
        toast.error(t(e?.code === 'iframe' ? 'viewImage.iframeUnsupported' : e?.code === 'empty' ? 'viewImage.nothingToCapture' : 'viewImage.captureFailed'));
        return;
      }
      const name = sanitizeFilename(view?.title, view?.kind || 'view');
      if (mode === 'download') {
        saveBlobAs(blob, name);
        return;
      }
      const workspace = view?.workspace || selectedWorkspace || undefined;
      try {
        const res = await uploadWorkspaceFileObject(workspace, blob, { source: 'view', filename: name });
        const id = res?.data?.file_id;
        // A plain anchor: toasts live above the router, so a Link has no context.
        toast.success(t('viewImage.savedToFiles'), id ? (
          <a href={`/artifacts?file=${encodeURIComponent(id)}`} className="underline">{t('viewImage.openFile')}</a>
        ) : undefined);
      } catch (e) {
        toast.error(t('viewImage.saveFailed'), errorDetail(e));
      }
    } finally {
      setBusy(false);
    }
  }, [getRoot, selectedWorkspace, t, toast, view]);

  const item = 'w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700';
  return (
    <div ref={boxRef} className="relative" data-view-chrome="">
      <button type="button" onClick={() => setOpen((v) => !v)} disabled={busy} title={t('viewImage.image')}
        aria-haspopup="menu" aria-expanded={open}
        className={buttonClass || 'p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 disabled:opacity-50'}>
        <ImageDown className={iconClass} />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full mt-1 z-30 min-w-[13rem] py-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg">
          <button type="button" role="menuitem" onClick={() => run('download')} className={item}>
            <Download className="w-4 h-4" /> {t('viewImage.downloadPng')}
          </button>
          <button type="button" role="menuitem" onClick={() => run('files')} className={item}>
            <FolderInput className="w-4 h-4" /> {t('viewImage.saveToFiles')}
          </button>
        </div>
      )}
    </div>
  );
}
