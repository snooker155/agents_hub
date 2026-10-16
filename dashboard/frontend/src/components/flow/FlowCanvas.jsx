import React, { useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  addEdge,
  MarkerType,
  useReactFlow,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Bot, ChevronDown, ChevronRight, Link2, WandSparkles } from 'lucide-react';
import FlowNode from './FlowNode';
import { AGENT_SLOT_ID } from '../flowEditor/graphHelpers';
import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';

// The dot-grid background reads the neutral ramp so a custom neutral tint
// (Account -> Palette) or dark mode retints it along with everything else;
// the cyan edge/marker color has no dedicated palette token (it is not one
// of the 2-4 base colors a person or workspace sets) and stays literal.
const CANVAS_COLOR_SPEC = { gridDot: ['--neutral-300', '#cbd5e1'] };

const nodeTypes = {
  flowNode: FlowNode,
};

// Registry-sourced palette grouped by category. Drags use the same
// 'application/agent-flow' contract as the canvas drop handler expects. `entitiesByCategory` is the { category: [entity, ...] } map from
// GET /api/flow-entities, except that agents are not listed one by one: the
// palette offers a single abstract agent node, and which agent runs there is
// picked in the node inspector.
const CATEGORY_LABELS = {
  agent: 'Agents',
  processor: 'Processors',
  condition: 'Conditions',
  map: 'Per item',
  transform: 'Transforms',
  interrupt: 'Interrupts',
  container: 'Nested runs',
};

// One background per category, so a list of entities reads by kind at a
// glance. Only hues theme.css gives a dark counterpart for (bg-*-50,
// border-*-200/300), written out in full for Tailwind to find.
const CATEGORY_STYLES = {
  processor: { item: 'border-emerald-200 bg-emerald-50 hover:border-emerald-300', dot: 'bg-emerald-400' },
  condition: { item: 'border-amber-200 bg-amber-50 hover:border-amber-300', dot: 'bg-amber-400' },
  map: { item: 'border-teal-200 bg-teal-50 hover:border-teal-300', dot: 'bg-teal-400' },
  transform: { item: 'border-sky-200 bg-sky-50 hover:border-sky-300', dot: 'bg-sky-400' },
  interrupt: { item: 'border-rose-200 bg-rose-50 hover:border-rose-300', dot: 'bg-rose-400' },
  container: { item: 'border-orange-200 bg-orange-50 hover:border-orange-300', dot: 'bg-orange-400' },
};
const OTHER_STYLE = { item: 'border-slate-200 bg-slate-50 hover:border-slate-300', dot: 'bg-slate-400' };

export function EntityPalette({ entitiesByCategory = {} }) {
  const { t } = useI18n();
  // Per-category expand state. Undefined means "use default" (open).
  const [expanded, setExpanded] = useState({});
  const isOpenCat = (cat) => expanded[cat] !== false;
  const toggleCat = (cat) => setExpanded((prev) => ({ ...prev, [cat]: !isOpenCat(cat) }));

  const handleDragStart = (event, entity) => {
    event.dataTransfer.setData('application/agent-flow', JSON.stringify(entity));
    event.dataTransfer.effectAllowed = 'move';
  };

  const cats = Object.keys(entitiesByCategory).filter((c) => c !== 'agent').sort((a, b) => {
    const order = ['agent', 'processor', 'condition', 'map', 'transform', 'interrupt', 'container'];
    const ia = order.indexOf(a); const ib = order.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  const agentSlot = {
    id: AGENT_SLOT_ID,
    name: t('flowFlowCanvas.agentNode'),
    description: t('flowFlowCanvas.agentNodeHint'),
    category: 'agent',
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 px-1">
        <WandSparkles className="h-4 w-4 shrink-0 text-cyan-600" />
        <div className="text-sm font-bold text-slate-900">{t('flowFlowCanvas.registry')}</div>
        <div className="text-[11px] text-slate-400">{t('flowFlowCanvas.dragOntoTheCanvasTo')}</div>
      </div>
      <button
        draggable
        onDragStart={(event) => handleDragStart(event, agentSlot)}
        type="button"
        title={agentSlot.description}
        data-testid="palette-agent-node"
        className="flex w-full cursor-grab items-center gap-3 rounded-xl border border-cyan-200 bg-cyan-50 px-3 py-2.5 text-left transition hover:border-cyan-400 active:cursor-grabbing"
      >
        <Bot className="h-5 w-5 shrink-0 text-cyan-600" />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-900">{agentSlot.name}</div>
          <div className="text-[11px] leading-4 text-slate-500">{agentSlot.description}</div>
        </div>
      </button>
      {cats.length === 0 ? (
        <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
          {t('flowFlowCanvas.noEntitiesAvailable')}
        </div>
      ) : null}
      {cats.map((cat) => {
        const items = entitiesByCategory[cat];
        const isOpen = isOpenCat(cat);
        const style = CATEGORY_STYLES[cat] || OTHER_STYLE;
        return (
          <div key={cat} className="space-y-1.5">
            <button
              type="button"
              onClick={() => toggleCat(cat)}
              className="flex w-full items-center gap-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400 transition hover:text-slate-600"
            >
              {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} aria-hidden="true" />
              <span>{CATEGORY_LABELS[cat] || cat}</span>
              <span className="font-normal text-slate-300">· {items.length}</span>
            </button>
            {isOpen
              ? items.map((entity) => (
                  <button
                    key={entity.id}
                    draggable
                    onDragStart={(event) => handleDragStart(event, entity)}
                    type="button"
                    title={entity.description}
                    className={`block w-full cursor-grab rounded-xl border px-3 py-2.5 text-left transition active:cursor-grabbing ${style.item}`}
                  >
                    <div className="truncate text-sm font-semibold text-slate-900">{entity.name}</div>
                    <div className="truncate text-[11px] text-slate-500">
                      {entity.group || entity.category}
                    </div>
                  </button>
                ))
              : null}
          </div>
        );
      })}
    </div>
  );
}

function FlowCanvasInner({
  nodes,
  edges,
  onEdgesChange,
  onNodesChange,
  onRunNode,
  setEdges,
  setNodes,
  setSelectedNodeId,
  activeNodeId,
  readOnly = false,
  selectedNodeId = null,
}) {
  const { t } = useI18n();
  const wrapperRef = useRef(null);
  const reactFlow = useReactFlow();
  const [isOver, setIsOver] = useState(false);
  const colors = useThemeColors(CANVAS_COLOR_SPEC);

  const enrichedNodes = useMemo(
    // Read only (the run page) applies no node changes, so selection is drawn
    // from the page's selected id rather than from React Flow's own state.
    () => nodes.map((n) => ({
      ...n,
      ...(readOnly ? { selected: n.id === selectedNodeId } : {}),
      data: { ...n.data, isActive: n.id === activeNodeId },
    })),
    [nodes, activeNodeId, readOnly, selectedNodeId]
  );

  const onConnect = (connection) => {
    setEdges((current) =>
      addEdge(
        {
          ...connection,
          type: 'smoothstep',
          animated: false,
          markerEnd: { type: MarkerType.ArrowClosed, color: '#0891b2' },
          style: { stroke: '#0891b2', strokeWidth: 2 },
        },
        current
      )
    );
  };

  const handleDrop = (event) => {
    event.preventDefault();
    if (readOnly) return;
    setIsOver(false);
    const raw = event.dataTransfer.getData('application/agent-flow');
    if (!raw) return;

    const agent = JSON.parse(raw);
    const position = reactFlow.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });

    // A dragged item is the abstract agent node or a registry entity. The
    // agent node starts with no agent (the inspector picks one); other
    // categories carry entity_id + their declared input/output contract.
    const category = agent.category || 'agent';
    const isAgent = category === 'agent';
    const isSlot = agent.id === AGENT_SLOT_ID;
    const nodeId = `${isSlot ? 'agent' : agent.id}-${Date.now()}`;
    setNodes((current) =>
      current.concat({
        id: nodeId,
        type: 'flowNode',
        position,
        style: { width: 90 },
        data: {
          node_id: nodeId,
          label: agent.name,
          description: isSlot ? '' : agent.description,
          agent_id: isAgent && !isSlot ? agent.id : '',
          entity_id: isAgent ? '' : agent.id,
          category,
          input: agent.inputs || [],
          output: agent.outputs || [],
          config: {},
          domain: agent.domain || category,
          nodeTask: '',
          onRunNode,
        },
      })
    );
    setSelectedNodeId(nodeId);
  };

  return (
    <div className="relative h-full">
      {/* Canvas */}
      <div
        ref={wrapperRef}
        onDrop={handleDrop}
        onDragOver={(event) => {
          event.preventDefault();
          if (!readOnly) setIsOver(true);
        }}
        onDragLeave={() => setIsOver(false)}
        className={`flow-canvas h-full overflow-hidden ${
          isOver ? 'bg-cyan-50/50' : 'bg-white'
        }`}
      >
        {nodes.length === 0 && !readOnly ? (
          <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 text-center">
            <Link2 className="h-7 w-7 text-slate-300" />
            <div className="space-y-1">
              <div className="text-sm font-semibold text-slate-900">{t('flowFlowCanvas.dropAgentsToStart')}</div>
              <div className="text-sm text-slate-500">{t('flowFlowCanvas.dragAgentsFromTheGraph')}</div>
            </div>
          </div>
        ) : null}
        <ReactFlow
          nodes={enrichedNodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={readOnly ? undefined : onConnect}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          deleteKeyCode={readOnly ? null : 'Backspace'}
          onNodeClick={(_, node) => setSelectedNodeId(node.id)}
          onPaneClick={() => setSelectedNodeId(null)}
          fitView
          nodeTypes={nodeTypes}
          defaultEdgeOptions={{
            type: 'smoothstep',
            animated: false,
            markerEnd: { type: MarkerType.ArrowClosed, color: '#0891b2' },
            style: { stroke: '#0891b2', strokeWidth: 2 },
          }}
        >
          <MiniMap pannable zoomable className="flow-minimap" />
          <Background gap={24} color={colors.gridDot} />
          <Controls className="!shadow-none" />
        </ReactFlow>
      </div>
    </div>
  );
}

export default function FlowCanvas(props) {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
