import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, ExternalLink, Loader } from 'lucide-react';

import { mintPreviewTicket, renewPreviewTicket } from '../../api/preview';
import { useI18n } from '../../i18n';

// Feature 7a: a container's or a project's web page, shown inside the hub
// through an authenticated proxy instead of a direct iframe.
//
// Why: an iframe navigation cannot carry the Bearer header the rest of the
// API uses, and the target must not run same-origin with the dashboard in a
// way that lets it read the hub's own tokens out of localStorage. So this
// mints a short-lived ticket with an authenticated call
// (POST /api/preview/tickets) and points the iframe at
// /preview/<ticket>/<path>, an open path outside /api where the ticket
// itself is the credential (dashboard/backend/routes/preview.py). The
// sandbox deliberately has no allow-same-origin: the previewed page runs in
// an opaque origin and cannot touch this page's storage.
//
// A ticket lives ten minutes. The backend only checks it when the iframe
// (or the page inside it) makes a request, so swapping the src early would
// reload the page for nothing. Instead, every RENEW_EVERY_MS while the
// preview is mounted and the tab visible, this asks whether the current
// ticket would run out before the next check; only then does it renew
// (POST /api/preview/tickets/renew) and swap the src, so the page reloads
// at most about once per ticket lifetime.
//
// `target` is {kind: 'container', name}, {kind: 'project', project_id} or
// {kind: 'deployment', deployment_id, service?} (docs/project-deployments.md).
export const RENEW_EVERY_MS = 4 * 60 * 1000;
const RENEW_MARGIN_MS = 60 * 1000;

const ticketOf = (url) => (url || '').split('/').filter(Boolean)[1] || '';

const expiryOf = (data) => {
  if (data?.expires_at) return data.expires_at * 1000;
  if (data?.expires_in) return Date.now() + data.expires_in * 1000;
  return null;
};

export default function PreviewFrame({ target, height = 480 }) {
  const { t } = useI18n();
  const [ticketUrl, setTicketUrl] = useState(null); // e.g. '/preview/<ticket>/'
  const [path, setPath] = useState('');
  const [pathInput, setPathInput] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const reMintedRef = useRef(false);
  const expiresAtRef = useRef(null);
  // Read by the renewal timer, which must not restart every time a parent
  // passes a new but equal target object.
  const targetRef = useRef(target);
  targetRef.current = target;

  const mint = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await mintPreviewTicket(target);
      setTicketUrl(data.url);
      expiresAtRef.current = expiryOf(data);
    } catch (e) {
      setError(e?.response?.data?.detail || t('preview.mintFailed'));
      setTicketUrl(null);
    } finally {
      setLoading(false);
    }
  }, [target, t]);

  const targetKey = `${target?.kind || ''}:${target?.name || target?.project_id || target?.deployment_id || ''}:${target?.service || ''}`;

  useEffect(() => {
    reMintedRef.current = false;
    setPath('');
    setPathInput('');
    mint();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  // The backend's expired-ticket page posts this from inside the sandboxed
  // (opaque-origin) iframe so a link that outlived its TTL while the tab sat
  // idle quietly gets a fresh one instead of showing a dead page.
  useEffect(() => {
    function onMessage(event) {
      const data = event?.data;
      if (data && data.source === 'agents-hub-preview' && data.type === 'expired'
          && !reMintedRef.current) {
        reMintedRef.current = true;
        mint();
      }
    }
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [mint]);

  useEffect(() => {
    if (!ticketUrl) return undefined;
    let cancelled = false;
    const check = async () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      const expiresAt = expiresAtRef.current;
      if (expiresAt == null || expiresAt - Date.now() > RENEW_EVERY_MS + RENEW_MARGIN_MS) return;
      try {
        const { data } = await renewPreviewTicket({ ticket: ticketOf(ticketUrl), ...targetRef.current });
        if (cancelled || !data?.url || data.url === ticketUrl) return;
        expiresAtRef.current = expiryOf(data);
        setTicketUrl(data.url);
      } catch {
        // Nothing to do: the expired page's message re-mints on the next
        // navigation, the same path a preview left idle in a hidden tab takes.
      }
    };
    const timer = setInterval(check, RENEW_EVERY_MS);
    document.addEventListener('visibilitychange', check);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', check);
    };
  }, [ticketUrl]);

  const handleReload = () => {
    reMintedRef.current = false;
    mint();
  };

  const handleGo = (e) => {
    e.preventDefault();
    setPath(pathInput.replace(/^\/+/, ''));
  };

  const src = ticketUrl ? `${ticketUrl}${path}` : null;

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
      <form onSubmit={handleGo} className="flex items-center gap-2 px-2 py-1.5 border-b border-gray-100 bg-gray-50">
        <input
          type="text"
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          placeholder={t('preview.pathPlaceholder')}
          className="flex-1 min-w-0 text-xs border border-gray-200 rounded px-2 py-1 font-mono"
        />
        <button
          type="submit"
          className="text-xs px-2 py-1 border border-gray-200 rounded hover:bg-gray-100 shrink-0"
        >
          {t('preview.go')}
        </button>
        <button
          type="button"
          onClick={handleReload}
          title={t('preview.reload')}
          className="text-gray-400 hover:text-indigo-600 p-1 rounded shrink-0"
        >
          {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
        </button>
        {src && (
          <a
            href={src}
            target="_blank"
            rel="noopener noreferrer"
            title={t('preview.openInNewTab')}
            className="text-gray-400 hover:text-indigo-600 p-1 rounded shrink-0"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </form>
      {error ? (
        <div className="text-xs text-red-600 p-3">{error}</div>
      ) : src ? (
        <iframe
          key={src}
          src={src}
          title={t('preview.frameTitle')}
          className="w-full border-0"
          style={{ height }}
          sandbox="allow-scripts allow-forms allow-popups allow-modals"
        />
      ) : (
        <div className="text-xs text-gray-400 p-3">{t('preview.loading')}</div>
      )}
    </div>
  );
}
