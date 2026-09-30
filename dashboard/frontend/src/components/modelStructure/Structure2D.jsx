import { memo, useCallback, useMemo } from 'react';
import ReactFlow, { Background, Controls, Handle, Position } from 'reactflow';
import 'reactflow/dist/style.css';
import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';
import { DTYPE_COLOR_SPEC, dtypeColorKey, formatBytes, formatCount, layerScale } from './graph';

// The 2D view: the blocks stacked top to bottom in reading order, `other`
// blocks in a column to the right. Read only: nothing drags or connects, a
// click only selects a block, and the selection lives on the page.

const CANVAS_COLOR_SPEC = {
  ...DTYPE_COLOR_SPEC,
  edge: ['--neutral-400', 'darkgray'],
  dots: ['--neutral-300', 'lightgray'],
};

const ROW = 84;
const SIDE_X = 300;
const NODE_WIDTH = 240;

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
      {!data.side && <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-gray-300" isConnectable={false} />}
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
      {!data.side && <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-gray-300" isConnectable={false} />}
    </div>
  );
});

const NODE_TYPES = { block: BlockNode };

export default function Structure2D({ graph, selectedId, onSelect }) {
  const colors = useThemeColors(CANVAS_COLOR_SPEC);
  const scale = useMemo(() => layerScale(graph.nodes), [graph]);

  const nodes = useMemo(() => {
    let mainRow = 0;
    let sideRow = 0;
    return graph.nodes.map((node) => {
      const row = node.side ? sideRow++ : mainRow++;
      return {
        id: node.id,
        type: 'block',
        position: { x: node.side ? SIDE_X : 0, y: row * ROW },
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

  return (
    <div className="h-full w-full" data-testid="structure-2d">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={onNodeClick}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1.2 }}
        minZoom={0.05}
        proOptions={{ hideAttribution: true }}
      >
        <Background color={colors.dots} gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
