import React, { useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  MarkerType,
  useReactFlow,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Link2, WandSparkles } from 'lucide-react';
import FlowNode from './FlowNode';

const nodeTypes = {
  flowNode: FlowNode,
};

// Draggable agent palette — rendered inside the Graph tab of the right panel.
// Dragging an entry onto the canvas drops a new node (see FlowCanvas handleDrop).
export function AgentPalette({ availableAgents = [] }) {
  const handleDragStart = (event, agent) => {
    event.dataTransfer.setData('application/agent-flow', JSON.stringify(agent));
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2 rounded-[20px] border border-slate-200 bg-slate-50 px-4 py-3">
        <div>
          <div className="text-sm font-bold text-slate-900">Agents</div>
          <div className="text-[11px] text-slate-400">Drag onto the canvas to add a node</div>
        </div>
        <WandSparkles className="h-4 w-4 shrink-0 text-cyan-600" />
      </div>
      <div className="space-y-1.5">
        {availableAgents.length === 0 ? (
          <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
            No agents available in this workspace.
          </div>
        ) : (
          availableAgents.map((agent) => (
            <button
              key={agent.id}
              draggable
              onDragStart={(event) => handleDragStart(event, agent)}
              type="button"
              className="block w-full cursor-grab rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5 text-left transition hover:border-cyan-300 hover:bg-cyan-50 active:cursor-grabbing"
            >
              <div className="truncate text-sm font-semibold text-slate-900">{agent.name}</div>
              <div className="truncate text-[11px] text-slate-400">{agent.domain}</div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function FlowCanvasInner({
  availableAgents,
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
          agent_id: agent.id,
          domain: agent.domain,
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
              <div className="text-sm font-semibold text-slate-900">Drop agents to start</div>
              <div className="text-sm text-slate-500">Drag agents from the Graph tab onto the canvas.</div>
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
          <MiniMap pannable zoomable className="!rounded-2xl !border !border-slate-200 !bg-white" />
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

export { applyEdgeChanges, applyNodeChanges };
