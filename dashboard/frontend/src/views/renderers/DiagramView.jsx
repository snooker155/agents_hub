import React, { useEffect, useRef, useState } from 'react';
import { useI18n } from '../../i18n';

// Diagram renderer — Mermaid source rendered to SVG. Code-split like ChartView:
// the mermaid bundle loads only when a `diagram` view is shown.

let _mermaidInit = null;

async function getMermaid(theme) {
  const mod = await import('mermaid');
  const mermaid = mod.default || mod;
  // initialize once; theme is set per-render via the config passed to render.
  if (!_mermaidInit) {
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: theme === 'dark' ? 'dark' : 'default' });
    _mermaidInit = true;
  }
  return mermaid;
}

let _idCounter = 0;

export default function DiagramView({ view, theme }) {
  const { t } = useI18n();
  const ref = useRef(null);
  const [error, setError] = useState(null);
  const source = view?.spec?.mermaid;

  useEffect(() => {
    let cancelled = false;
    if (!ref.current || !source) return undefined;

    (async () => {
      try {
        const mermaid = await getMermaid(theme);
        const id = `view-mermaid-${_idCounter++}`;
        const { svg } = await mermaid.render(id, source);
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) setError(e?.message || String(e));
      }
    })();

    return () => { cancelled = true; };
  }, [source, theme]);

  if (!source) {
    return <div className="text-sm text-gray-500">{t('viewDiagramView.missingSpec')}</div>;
  }
  if (error) {
    return (
      <div className="text-sm text-red-600">
        Could not render diagram: {error}
        <pre className="mt-2 bg-gray-900 text-gray-100 text-xs p-3 rounded overflow-x-auto whitespace-pre">{source}</pre>
      </div>
    );
  }
  return <div ref={ref} className="w-full overflow-x-auto flex justify-center" />;
}
