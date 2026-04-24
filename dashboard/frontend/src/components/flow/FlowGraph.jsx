import React, { useState, useEffect, useCallback, useRef } from 'react';
import ReactFlow, {
  Background,
  Controls,
  applyEdgeChanges,
  applyNodeChanges,
  addEdge,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { getFactoryGraph, getActiveNode, runFactoryAgent } from '../../api';
import { Play, Plus, Trash2, Save } from 'lucide-react';

const initialNodes = [];
const initialEdges = [];

function FlowGraph({ workspace }) {
  const [nodes, setNodes] = useState(initialNodes);
  const [edges, setEdges] = useState(initialEdges);
  const [activeNode, setActiveNode] = useState(null);
  const [isEditable, setIsEditable] = useState(false);
  const reactFlowWrapper = useRef(null);

  const onNodesChange = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    []
  );
  const onEdgesChange = useCallback(
    (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  );
  const onConnect = useCallback(
    (params) => setEdges((eds) => addEdge({ ...params, animated: true, markerEnd: { type: MarkerType.ArrowClosed } }, eds)),
    []
  );

  useEffect(() => {
    const fetchGraph = async () => {
      try {
        const res = await getFactoryGraph();
        const { nodes: graphNodes, edges: graphEdges } = res.data;

        const layoutedNodes = graphNodes.map((node, i) => ({
          id: node.id,
          data: { label: node.label },
          position: { x: i * 200, y: 150 },
          style: { background: '#fff', border: '1px solid #6366f1', borderRadius: '8px', padding: '10px', width: 150, textAlign: 'center' }
        }));

        const layoutedEdges = graphEdges.map((edge, i) => ({
          id: `e-${i}`,
          source: edge.source,
          target: edge.target,
          animated: true,
          markerEnd: { type: MarkerType.ArrowClosed, color: '#6366f1' },
          style: { stroke: '#6366f1' }
        }));

        setNodes(layoutedNodes);
        setEdges(layoutedEdges);
      } catch (e) {
        console.error("Failed to fetch graph structure", e);
      }
    };
    fetchGraph();
  }, []);

  useEffect(() => {
    if (!workspace || isEditable) return;

    const fetchActiveNode = async () => {
      try {
        const res = await getActiveNode(workspace);
        const currentActive = res.data.active_node;
        setActiveNode(currentActive);

        setNodes((nds) => nds.map((node) => {
          if (node.id === currentActive) {
            return { ...node, style: { ...node.style, background: '#fef08a', border: '2px solid #eab308' } };
          }
          return { ...node, style: { ...node.style, background: '#fff', border: '1px solid #6366f1' } };
        }));
      } catch (e) {
        // console.error("Failed to fetch active node", e);
      }
    };
    const interval = setInterval(fetchActiveNode, 2000);
    return () => clearInterval(interval);
  }, [workspace, isEditable]);

  const addAgentNode = (type) => {
    const id = `${type}-${Date.now()}`;
    const newNode = {
      id,
      data: { label: type.toUpperCase() },
      position: { x: 100, y: 100 },
      style: { background: '#fff', border: '1px solid #6366f1', borderRadius: '8px', padding: '10px', width: 150, textAlign: 'center' }
    };
    setNodes((nds) => nds.concat(newNode));
  };

  const handleRunCustom = async () => {
    if (!workspace) {
      alert("Please select a workspace");
      return;
    }
    // For now, we'll send the graph structure.
    // The backend needs to be able to handle this.
    try {
      await runFactoryAgent({
        agent: 'custom-graph',
        workspace,
        description: JSON.stringify({ nodes, edges })
      });
      alert("Started custom graph execution");
    } catch (e) {
      alert("Failed to start custom graph");
    }
  };

  return (
    <div className="h-full flex flex-col border border-gray-200 rounded-lg overflow-hidden bg-gray-50 relative" ref={reactFlowWrapper}>
      <div className="absolute top-4 left-4 z-10 flex space-x-2">
        <button
          onClick={() => setIsEditable(!isEditable)}
          className={`px-3 py-1 rounded shadow text-sm font-medium ${isEditable ? 'bg-indigo-600 text-white' : 'bg-white text-gray-700 hover:bg-gray-50'}`}
        >
          {isEditable ? 'Finish Editing' : 'Edit Graph'}
        </button>
        {isEditable && (
          <div className="flex space-x-1 bg-white p-1 rounded shadow border border-gray-200">
            {['pm', 'ba', 'sd', 'tl', 'be', 'fe', 'qa', 'ops'].map(agent => (
              <button key={agent} onClick={() => addAgentNode(agent)} className="px-2 py-0.5 text-xs hover:bg-gray-100 rounded border border-gray-100 capitalize">
                + {agent}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="absolute top-4 right-4 z-10 flex space-x-2">
        <button
          onClick={handleRunCustom}
          className="flex items-center px-4 py-1 bg-green-600 text-white rounded shadow hover:bg-green-700 text-sm font-medium"
        >
          <Play className="w-4 h-4 mr-1" /> Run Custom
        </button>
      </div>

      <div className="flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={isEditable ? onConnect : undefined}
          nodesConnectable={isEditable}
          nodesDraggable={isEditable}
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
}

export default FlowGraph;
