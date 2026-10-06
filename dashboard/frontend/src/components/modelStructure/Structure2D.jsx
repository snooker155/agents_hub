import { memo, useCallback, useMemo } from 'react';
import ReactFlow, { Background, Controls, Handle, Position } from 'reactflow';
import 'reactflow/dist/style.css';
import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';
import { DTYPE_COLOR_SPEC, dtypeColorKey, formatBytes, formatCount, layerScale } from './graph';

// The 2D view: the blocks in a row, left to right in reading order, `other`
// blocks in a second row underneath. A model of many layers is a long strip,
// so the view opens on its first blocks at a readable size rather than on the
// whole strip shrunk to a line, and the mouse wheel scrolls along it (pinch or
// Ctrl+wheel zooms). Read only: nothing drags or connects, a click only
// selects a block, and the selection lives on the page.

const CANVAS_COLOR_SPEC = {
  ...DTYPE_COLOR_SPEC,
  edge: ['--neutral-400', 'darkgray'],
  dots: ['--neutral-300', 'lightgray'],
};

const NODE_WIDTH = 220;
const COLUMN = NODE_WIDTH + 48;
const SIDE_Y = 130;
// How many blocks of the chain the first view fits.
const FIRST_VIEW = 4;

const BlockNode = memo(function BlockNode({ data }) {
  const { t } = useI18n();
  return (
    <div
      data-testid={`block-node-${data.id}`}
      data-selected={data.selected ? 'true' : 'false'}
      className={`rounded-lg border bg-white px-3 py-2 shadow-sm transition-shadow ${
        data.selected ? 'border-indigo-500 ring-2 ring-indigo-500' : 'border-gray-200 hover:border-indigo-300'
      }`}
      style={{ width: NODE_WIDTH }}
      title={t('modelStructure.node.select', { label: data.label })}
    >
      {!data.side && <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-gray-300" isConnectable={false} />}
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-medium text-gray-900">{data.label}</span>
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-gray-400">{data.dtype || ''}</span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-gray-500">
        <span>{t('modelStructure.node.parameters', { count: formatCount(data.parameters) })}</span>
        <span>{formatBytes(data.bytes)}</span>
      </div>
      <div className="mt-1.5 h-1.5 w-full rounded-full bg-gray-100">
        <div
          className="h-1.5 rounded-full"
          style={{ width: `${Math.max(4, Math.round(data.scale * 100))}%`, background: data.color }}
        />
      </div>
      {!data.side && <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-gray-300" isConnectable={false} />}
    </div>
  );
});

const NODE_TYPES = { block: BlockNode };

export default function Structure2D({ graph, selectedId, onSelect }) {
  const colors = useThemeColors(CANVAS_COLOR_SPEC);
  const scale = useMemo(() => layerScale(graph.nodes), [graph]);

  const nodes = useMemo(() => {
    let mainCol = 0;
    let sideCol = 0;
    return graph.nodes.map((node) => {
      const col = node.side ? sideCol++ : mainCol++;
      return {
        id: node.id,
        type: 'block',
        position: { x: col * COLUMN, y: node.side ? SIDE_Y : 0 },
        selected: node.id === selectedId,
        data: {
          ...node,
          scale: scale[node.id] ?? 0,
          color: colors[dtypeColorKey(node.dtype)] || colors.other,
          selected: node.id === selectedId,
        },
      };
    });
  }, [graph, scale, colors, selectedId]);

  const edges = useMemo(() => graph.edges.map((e) => ({
    id: `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    style: { stroke: colors.edge },
  })), [graph, colors.edge]);

  const onNodeClick = useCallback((_, node) => onSelect?.(node.id), [onSelect]);
  // A click on the empty canvas (not the end of a pan) clears the selection.
  const onPaneClick = useCallback(() => onSelect?.(null), [onSelect]);

  // The first blocks of the chain (and whatever sits under them), so a long
  // model opens readable at its start.
  const firstView = useMemo(() => {
    const main = graph.nodes.filter((n) => !n.side).slice(0, FIRST_VIEW).map((n) => ({ id: n.id }));
    const side = graph.nodes.filter((n) => n.side).slice(0, FIRST_VIEW).map((n) => ({ id: n.id }));
    return [...main, ...side];
  }, [graph]);

  return (
    <div className="h-full w-full" data-testid="structure-2d">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1.2, nodes: firstView }}
        minZoom={0.05}
        // The wheel zooms around the cursor; dragging the canvas pans.
        zoomOnScroll
        proOptions={{ hideAttribution: true }}
      >
        <Background color={colors.dots} gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
