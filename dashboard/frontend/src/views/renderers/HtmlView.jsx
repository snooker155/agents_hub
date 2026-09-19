import React, { useRef, useEffect, useMemo } from 'react';
import { viewAssetUrl, viewProxyUrl } from '../../api';
import { useI18n } from '../../i18n';

// Live HTML/JS/CSS renderer — the escape hatch for fully custom interactivity.
// Runs in a sandboxed iframe with allow-scripts but NO allow-same-origin, so the
// content has a null origin: no cookies, no access to the dashboard API or the
// host's storage. The sandbox — not review of the generated code — is the
// security boundary (design §7). All host↔content talk goes through the injected
// `viewhost` postMessage bridge; sendToAgent is host-confirmed so embedded code
// can never silently prompt the agent.
//
// Full-stack tier (§16.8): when the view has a served backend (view_serve), the
// injected CSP opens connect-src to *exactly the view's own proxy prefix* — the
// generated frontend can fetch its backend, and nothing else. A view with a
// service but no content of its own renders the service directly through the
// proxy (the backend serves its own frontend).

// Injected into inline (srcdoc) content: a default-deny CSP + the viewhost API.
// `proxyBase` (view_serve configured) is both allowed in connect-src and exposed
// as viewhost.proxyBase / viewhost.proxyUrl(path).
function bridgeFor(proxyBase) {
  const connect = proxyBase ? `${new URL(proxyBase, window.location.origin).href}` : "'none'";
  return `
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src ${connect}">
<script>
window.viewhost = (function () {
  var data = null, state = {}, dataCbs = [], controlCbs = [];
  var proxyBase = ${JSON.stringify(proxyBase || '')};
  window.addEventListener('message', function (e) {
    var m = e.data || {};
    if (m.type === 'viewhost:data') { data = m.data; dataCbs.forEach(function (h) { try { h(data); } catch (x) {} }); }
    else if (m.type === 'viewhost:control') { controlCbs.forEach(function (h) { try { h(m.control, m.value); } catch (x) {} }); }
    else if (m.type === 'viewhost:state') { state = m.state || {}; }
  });
  function post(msg) { parent.postMessage(msg, '*'); }
  post({ type: 'viewhost:ready' });
  return {
    getData: function () { return data; },
    onData: function (cb) { dataCbs.push(cb); if (data != null) cb(data); },
    onControl: function (cb) { controlCbs.push(cb); },
    getState: function () { return state; },
    setState: function (s) { state = s; post({ type: 'viewhost:setState', state: s }); },
    sendToAgent: function (text, payload) { post({ type: 'viewhost:sendToAgent', text: text, payload: payload || null }); },
    proxyBase: proxyBase,
    proxyUrl: function (path) { return proxyBase ? proxyBase + String(path || '').replace(/^\\//, '') : null; },
  };
})();
</script>
`;
}

export default function HtmlView({ view, onSendToAgent, onState }) {
  const { t } = useI18n();
  const frameRef = useRef(null);
  const inlineHtml = view?.spec?.html || '';
  const entry = view?.spec?.entry || '';
  const served = !!(view?.serve?.upstream) && view?.view_id;
  const proxyBase = served ? viewProxyUrl(view.view_id) : '';
  const hasOwnContent = !!inlineHtml || !!entry;
  // a served view without its own content renders the service through the proxy
  const useProxySrc = served && !hasOwnContent;
  const useSrcDoc = !useProxySrc && (!!inlineHtml || !entry);
  const srcDoc = useMemo(
    () => (useSrcDoc ? bridgeFor(proxyBase) + (inlineHtml || '') : undefined),
    [useSrcDoc, inlineHtml, proxyBase],
  );
  const src = useProxySrc ? proxyBase
    : (!useSrcDoc && view?.view_id ? viewAssetUrl(view.view_id, entry) : undefined);
  const data = view?.data ?? view?.spec?.data ?? null;

  useEffect(() => {
    const onMessage = (e) => {
      const frame = frameRef.current;
      if (!frame || e.source !== frame.contentWindow) return;   // only our iframe
      const m = e.data || {};
      if (m.type === 'viewhost:ready') {
        frame.contentWindow.postMessage({ type: 'viewhost:data', data }, '*');
      } else if (m.type === 'viewhost:setState') {
        onState && onState(m.state || {});
      } else if (m.type === 'viewhost:sendToAgent') {
        // Host-confirmed: embedded content can never silently message the agent.
        const text = String(m.text || '').slice(0, 2000);
        if (text && window.confirm(`This view wants to send a message to the agent:\n\n"${text}"\n\nSend it?`)) {
          onSendToAgent && onSendToAgent(text, m.payload);
        }
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [data, onSendToAgent, onState]);

  if (!inlineHtml && !entry && !served) {
    return <div className="text-sm text-gray-500">{t('viewHtmlView.empty')}</div>;
  }
  return (
    <iframe
      ref={frameRef}
      title={view?.title || 'html view'}
      sandbox="allow-scripts"
      srcDoc={srcDoc}
      src={src}
      className="w-full h-full min-h-[360px] rounded-lg border border-gray-200 dark:border-gray-700 bg-white"
      style={{ height: '100%' }}
    />
  );
}
