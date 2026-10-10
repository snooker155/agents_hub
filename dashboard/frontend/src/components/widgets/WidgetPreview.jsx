/**
 * The Preview tab: a page with the widget embedded, running the real script
 * against the real public API, as a visitor would.
 *
 * The frame is a srcdoc document, so it runs on the dashboard's own origin,
 * which is not one of the widget's allowed origins. A short-lived preview
 * ticket (POST /api/widgets/<id>/preview) is what lets it in, and it also
 * works while the widget is switched off. The script is loaded from the
 * dashboard's origin under /api, so the development server's /api proxy
 * carries it the same way it carries every other call.
 */
import { useCallback, useEffect, useState } from 'react';
import { Loader, RefreshCw } from 'lucide-react';
import { API_ORIGIN } from '../../api';
import { createWidgetPreview } from '../../api/widgets';
import { useI18n } from '../../i18n';
import { errorDetail } from '../toast';
import { previewDocument } from './widgetUtils';

export default function WidgetPreview({ widget }) {
  const { t, language } = useI18n();
  const [doc, setDoc] = useState('');
  const [error, setError] = useState('');
  // The preview on screen is for this key; a different one shows the loader
  // again without a setState inside the effect.
  const previewKey = `${widget.widget_id}|${widget.updated_at}|${widget.public_key}|${language}`;
  const [loadedKey, setLoadedKey] = useState(null);
  const [reloading, setReloading] = useState(false);
  const loading = reloading || loadedKey !== previewKey;
  const [generation, setGeneration] = useState(0);

  // A promise chain, not an async function: the React Compiler lint treats an
  // async function called from an effect as a synchronous setState.
  const load = useCallback(() => createWidgetPreview(widget.widget_id)
    .then(({ data }) => {
      const hub = API_ORIGIN || window.location.origin;
      setDoc(previewDocument({
        hub, widgetId: data.widget_id, publicKey: data.public_key, ticket: data.ticket,
        scriptPath: data.script_path, text: t('widgets.preview.pageText'), lang: language,
      }));
      setGeneration((g) => g + 1);
      setError('');
    })
    .catch((err) => {
      setDoc('');
      setError(errorDetail(err) || t('widgets.preview.failed'));
    })
    .then(() => setReloading(false)), [widget.widget_id, t, language]);

  const reload = () => {
    setReloading(true);
    setError('');
    return load();
  };

  // A changed look, text or key is a new preview.
  useEffect(() => {
    let cancelled = false;
    load().then(() => { if (!cancelled) setLoadedKey(previewKey); });
    return () => { cancelled = true; };
  }, [load, previewKey]);

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-gray-500">{t('widgets.preview.hint')}</p>
        <button type="button" onClick={reload} disabled={loading}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 shrink-0">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> {t('widgets.preview.reload')}
        </button>
      </div>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      {loading && !doc ? (
        <div className="flex items-center justify-center gap-2 h-64 text-sm text-gray-400">
          <Loader className="w-4 h-4 animate-spin" /> {t('widgets.preview.loading')}
        </div>
      ) : doc ? (
        <iframe key={generation} title={t('widgets.preview.frameTitle')} srcDoc={doc}
          className="w-full h-[600px] rounded-lg border border-gray-200" />
      ) : null}
    </div>
  );
}
