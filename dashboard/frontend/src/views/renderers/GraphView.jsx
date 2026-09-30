import React, { useEffect, useRef } from 'react';
import { useThemeColors } from '../../lib/themeColors';

// Graph renderer — Cytoscape. Reads spec.nodes / spec.edges (keyed maps, the
// live op shape; arrays are also accepted) and reconciles them into a persistent
// Cytoscape instance so ops applied one-by-one make nodes/edges *appear* live
// rather than re-mounting the whole graph. Code-split like the other heavy
// renderers. Clicking an element reports it via onSelect (the Studio persists
// the selection so "make it red" resolves).

function asList(coll) {
  if (Array.isArray(coll)) return coll.map((el, i) => [el?.id ?? el?.data?.id ?? String(i), el?.data || el]);
  if (coll && typeof coll === 'object') return Object.entries(coll);
  return [];
}

function toElements(spec) {
  const nodes = asList(spec?.nodes).map(([id, n]) => ({
    data: { id: String(id), label: n?.label ?? String(id), group: n?.group || '', kind: n?.kind || '', subtitle: n?.subtitle || '' },
  }));
  const nodeIds = new Set(nodes.map((n) => n.data.id));
  const edges = asList(spec?.edges)
    .map(([id, e]) => ({ data: { id: String(id), source: String(e?.source), target: String(e?.target), label: e?.label || '' } }))
    // drop dangling edges so Cytoscape doesn't throw
    .filter((e) => nodeIds.has(e.data.source) && nodeIds.has(e.data.target));
  return { nodes, edges };
}

// Cytoscape takes colours as values, not classes, so the palette tokens are
// resolved through useThemeColors and re-read when the palette or theme
// changes. Fallbacks are the literals this renderer always shipped.
const GRAPH_COLOR_SPEC = {
  node: ['--brand-600', '#2a4fbd'],
  nodeDark: ['--brand-700', '#6b90ff'],
  text: ['--text-primary', '#111827'],
  selected: ['--hue-amber-400', '#f59e0b'],
  selectedBorder: ['--warn', '#b45309'],
  edge: ['--neutral-400', '#9ca3af'],
  edgeDark: ['--neutral-600', '#4b5563'],
  edgeText: ['--text-muted', '#6b7280'],
  edgeTextBg: ['--surface-card', '#ffffff'],
};

const STYLE = (dark, c) => [
  {
    selector: 'node',
    style: {
      'background-color': dark ? c.nodeDark : c.node,
      label: 'data(label)',
      color: c.text,
      'font-size': 11,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      width: 26, height: 26,
      'border-width': 0,
    },
  },
  { selector: 'node:selected', style: { 'background-color': c.selected, 'border-width': 3, 'border-color': c.selectedBorder } },
  {
    selector: 'edge',
    style: {
      width: 1.5,
      'line-color': dark ? c.edgeDark : c.edge,
      'target-arrow-color': dark ? c.edgeDark : c.edge,
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      label: 'data(label)',
      'font-size': 9,
      color: c.edgeText,
      'text-background-color': c.edgeTextBg,
      'text-background-opacity': 0.8,
    },
  },
  { selector: 'edge:selected', style: { 'line-color': c.selected, 'target-arrow-color': c.selected, width: 2.5 } },
];

export default function GraphView({ view, theme, onSelect }) {
  const elRef = useRef(null);
  const cyRef = useRef(null);
  const layoutName = view?.spec?.layout || 'cose';
  const dark = theme === 'dark';
  const colors = useThemeColors(GRAPH_COLOR_SPEC);

  // Create the instance once.
  useEffect(() => {
    let cy;
    let cancelled = false;
    (async () => {
      const cytoscape = (await import('cytoscape')).default;
      if (cancelled || !elRef.current) return;
      cy = cytoscape({ container: elRef.current, elements: [], style: STYLE(dark, colors), wheelSensitivity: 0.2 });
      cy.on('tap', 'node, edge', (evt) => onSelect && onSelect(evt.target.id()));
      cy.on('tap', (evt) => { if (evt.target === cy && onSelect) onSelect(null); });
      cyRef.current = cy;
      reconcile(cy, view, layoutName);
    })();
    return () => { cancelled = true; try { cy && cy.destroy(); } catch { /* noop */ } cyRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reconcile on every doc/theme change (live ops arrive as new `view` props).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.style(STYLE(dark, colors));
    reconcile(cy, view, layoutName);
  }, [view, dark, colors, layoutName]);

  return <div ref={elRef} className="w-full h-full min-h-[320px]" style={{ height: '100%' }} />;
}

function reconcile(cy, view, layoutName) {
  const { nodes, edges } = toElements(view?.spec);
  const desired = [...nodes, ...edges];
  const desiredIds = new Set(desired.map((e) => e.data.id));
  const before = cy.nodes().length;

  // Remove elements that are gone.
  cy.elements().forEach((el) => { if (!desiredIds.has(el.id())) el.remove(); });

  // Add or update.
  let added = 0;
  desired.forEach((el) => {
    const existing = cy.getElementById(el.data.id);
    if (existing.empty()) { cy.add(el); added += 1; }
    else existing.data(el.data);
  });

  // Re-run layout when the node set changed (so new nodes get placed).
  if (added > 0 || cy.nodes().length !== before) {
    cy.layout({ name: layoutName, animate: true, animationDuration: 300, fit: true, padding: 30 }).run();
  }
}
