import React, { useEffect, useRef, useState } from 'react';
import { subscribe } from '../viewBus';
import { useI18n } from '../../i18n';

// Chart renderer — a Vega-Lite spec embedded with vega-embed. This module (and
// the heavy vega bundle it pulls in) is code-split: ViewRenderer lazy-loads it
// only when a `chart` view is shown, so vega never weighs on the main bundle.
//
// Linked mode (§14): when the view's `link.timebase` names a shared timebase,
// the chart becomes a live series fed by that timebase's frame aggregates — a
// simulation next to its throughput chart. The rows stream into a named vega
// dataset ('linked') via the view API, so the chart animates without
// re-embedding; a spec without its own encoding gets a folded multi-series
// line over t for free.

const WINDOW = 60;      // seconds of linked history kept on screen
const MAX_ROWS = 900;

function linkedDefaultSpec(keys) {
  return {
    mark: 'line',
    transform: [{ fold: keys, as: ['metric', 'value'] }],
    encoding: {
      x: { field: 't', type: 'quantitative', title: 't (s)' },
      y: { field: 'value', type: 'quantitative' },
      color: { field: 'metric', type: 'nominal' },
    },
  };
}

export default function ChartView({ view, theme }) {
  const { t } = useI18n();
  const ref = useRef(null);
  const [error, setError] = useState(null);
  const vegaViewRef = useRef(null);
  const rowsRef = useRef([]);
  const timebase = view?.link?.timebase;
  // series keys discovered from the first linked frame → triggers the embed
  const [linkedKeys, setLinkedKeys] = useState(null);

  const baseSpec = view?.spec?.vega_lite;
  const hasOwnSpec = baseSpec && Object.keys(baseSpec).length > 0;

  // collect linked rows; stream them into the embedded chart when it's live
  useEffect(() => {
    if (!timebase) return undefined;
    let lastT = -Infinity;
    const unsub = subscribe(`timebase:${timebase}`, ({ t, aggregates }) => {
      if (!(t > lastT + 0.05)) return;   // ~20 rows/s cap
      lastT = t;
      const row = { t: +(+t).toFixed(3) };
      for (const [k, v] of Object.entries(aggregates || {})) {
        if (typeof v === 'number' && Number.isFinite(v)) row[k] = v;
      }
      if (Object.keys(row).length < 2) return;
      rowsRef.current.push(row);
      if (rowsRef.current.length > MAX_ROWS) rowsRef.current.splice(0, rowsRef.current.length - MAX_ROWS);
      setLinkedKeys((keys) => keys || Object.keys(row).filter((k) => k !== 't'));
      const vw = vegaViewRef.current;
      if (vw) {
        (async () => {
          try {
            const vega = await import('vega');
            vw.change('linked', vega.changeset().insert([row]).remove((d) => d.t < row.t - WINDOW)).run();
          } catch { /* chart not ready yet */ }
        })();
      }
    });
    return unsub;
  }, [timebase]);

  // the spec actually embedded: linked charts read the 'linked' named dataset
  const spec = timebase
    ? { ...(hasOwnSpec ? baseSpec : (linkedKeys ? linkedDefaultSpec(linkedKeys) : null)), data: { name: 'linked' } }
    : baseSpec;
  const specJson = JSON.stringify(spec || null);

  useEffect(() => {
    let cancelled = false;
    let result = null;
    if (!ref.current || !spec) return undefined;

    // Fill the frame the host gave us instead of vega's default 200px plot: only
    // when the box has a real height of its own (a page/Studio viewport, not a
    // content-sized card), the spec sets no height, and it is a single-view spec
    // — sizing is per-view, so concat/facet/repeat/layer keep their own layout.
    // A measured pixel height (not height:'container', which needs vega-embed's
    // stylesheet to give .chart-wrapper its 100%) plus autosize:fit makes that
    // number the *total* height, axes and legend included.
    const composed = ['vconcat', 'hconcat', 'concat', 'facet', 'repeat', 'layer'].some((k) => k in spec);
    // …but capped at ~0.6 of the width: a full-height stretch turns a two-bar
    // chart into a skyscraper. Landscape is the readable default for a plot.
    const boxHeight = Math.min(ref.current.clientHeight - 4, Math.round(ref.current.clientWidth * 0.6));
    const fitHeight = !composed && spec.height == null && boxHeight >= 200;
    const embedSpec = fitHeight
      ? { ...spec, autosize: spec.autosize || { type: 'fit', contains: 'padding' } }
      : spec;

    (async () => {
      try {
        const { default: embed } = await import('vega-embed');
        if (cancelled || !ref.current) return;
        result = await embed(ref.current, embedSpec, {
          actions: { export: true, source: false, compiled: false, editor: false },
          theme: theme === 'dark' ? 'dark' : undefined,
          renderer: 'canvas',
          width: 'container',
          ...(fitHeight ? { height: boxHeight } : {}),
        });
        if (timebase) {
          vegaViewRef.current = result.view;
          if (rowsRef.current.length) {
            const vega = await import('vega');
            result.view.change('linked', vega.changeset().insert([...rowsRef.current])).run();
          }
        }
      } catch (e) {
        if (!cancelled) setError(e?.message || String(e));
      }
    })();

    return () => {
      cancelled = true;
      vegaViewRef.current = null;
      try { result && result.finalize && result.finalize(); } catch { /* noop */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [specJson, theme]);

  if (!spec) {
    if (timebase) {
      return <div className="text-sm text-gray-400 p-3">{t('viewChartView.linkedToTimebase', { timebase })}</div>;
    }
    return <div className="text-sm text-gray-500">{t('viewChartView.missingSpec')}</div>;
  }
  if (error) {
    return (
      <div className="text-sm text-red-600">
        Could not render chart: {error}
      </div>
    );
  }
  // min-h keeps the chart visible in content-sized hosts (chat card, gallery),
  // where ViewRenderer's fill wrapper would otherwise give it a zero basis.
  return <div ref={ref} className="w-full h-full min-h-[240px] overflow-x-auto" />;
}
