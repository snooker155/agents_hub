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
import { ChevronDown, ChevronRight, Link2, WandSparkles } from 'lucide-react';
import FlowNode from './FlowNode';
import { useI18n } from '../../i18n';

const nodeTypes = {
  flowNode: FlowNode,
};

// Registry-sourced palette grouped by category. Drags use the same
// 'application/agent-flow' contract as the canvas drop handler expects. `entitiesByCategory` is the { category: [entity, ...] } map from
// GET /api/flow-entities.
const CATEGORY_LABELS = {
  agent: 'Agents',
  processor: 'Processors',
  condition: 'Conditions',
  transform: 'Transforms',
};

export function EntityPalette({ entitiesByCategory = {} }) {
  const { t } = useI18n();
  // Per-category expand state. Undefined means "use default" (collapsed).
  const [expanded, setExpanded] = useState({});
  const toggleCat = (cat) => setExpanded((prev) => ({ ...prev, [cat]: !prev[cat] }));

  const handleDragStart = (event, entity) => {
    event.dataTransfer.setData('application/agent-flow', JSON.stringify(entity));
    event.dataTransfer.effectAllowed = 'move';
  };

  const cats = Object.keys(entitiesByCategory).sort((a, b) => {
    const order = ['agent', 'processor', 'condition', 'transform'];
    const ia = order.indexOf(a); const ib = order.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  if (cats.length === 0) {
    return (
      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
        {t('flowFlowCanvas.noEntitiesAvailable')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 px-1">
        <WandSparkles className="h-4 w-4 shrink-0 text-cyan-600" />
        <div className="text-sm font-bold text-slate-900">{t('flowFlowCanvas.registry')}</div>
        <div className="text-[11px] text-slate-400">{t('flowFlowCanvas.dragOntoTheCanvasTo')}</div>
      </div>
      {cats.map((cat) => {
        const items = entitiesByCategory[cat];
        const isOpen = !!expanded[cat];
        return (
          <div key={cat} className="space-y-1.5">
            <button
              type="button"
              onClick={() => toggleCat(cat)}
              className="flex w-full items-center gap-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400 transition hover:text-slate-600"
            >
              {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
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
                    className="block w-full cursor-grab rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5 text-left transition hover:border-cyan-300 hover:bg-cyan-50 active:cursor-grabbing"
                  >
                    <div className="truncate text-sm font-semibold text-slate-900">{entity.name}</div>
                    <div className="truncate text-[11px] text-slate-400">
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
}) {
  const { t } = useI18n();
  const wrapperRef = useRef(null);
  const reactFlow = useReactFlow();
  const [isOver, setIsOver] = useState(false);

  const enrichedNodes = useMemo(
    () => nodes.map((n) => ({ ...n, data: { ...n.data, isActive: n.id === activeNodeId } })),
    [nodes, activeNodeId]
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
    setIsOver(false);
    const raw = event.dataTransfer.getData('application/agent-flow');
    if (!raw) return;

    const agent = JSON.parse(raw);
    const position = reactFlow.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });

    const nodeId = `${agent.id}-${Date.now()}`;
    // A dragged item may be a plain agent or a registry entity. Agents keep
    // agent_id (so the runner's existing path handles them); other categories
    // carry entity_id + their declared input/output contract.
    const category = agent.category || 'agent';
    const isAgent = category === 'agent';
    setNodes((current) =>
      current.concat({
        id: nodeId,
        type: 'flowNode',
        position,
        style: { width: 90 },
        data: {
          node_id: nodeId,
          label: agent.name,
          description: agent.description,
          agent_id: isAgent ? agent.id : '',
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
          setIsOver(true);
        }}
        onDragLeave={() => setIsOver(false)}
        className={`flow-canvas h-full overflow-hidden ${
          isOver ? 'bg-cyan-50/50' : 'bg-white'
        }`}
      >
        {nodes.length === 0 ? (
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
          onConnect={onConnect}
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
          <Background gap={24} color="#cbd5e1" />
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
