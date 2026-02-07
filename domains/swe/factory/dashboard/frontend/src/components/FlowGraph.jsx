import React, { useState, useEffect, useCallback } from 'react'
import ReactFlow, {
  Background,
  Controls,
  applyEdgeChanges,
  applyNodeChanges
} from 'reactflow'
import 'reactflow/dist/style.css'
import axios from 'axios'

const initialNodes = []
const initialEdges = []

function FlowGraph() {
  const [nodes, setNodes] = useState(initialNodes)
  const [edges, setEdges] = useState(initialEdges)
  const [activeNode, setActiveNode] = useState(null)

  const onNodesChange = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    []
  )
  const onEdgesChange = useCallback(
    (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  )

  useEffect(() => {
    const fetchGraph = async () => {
      try {
        const res = await axios.get('/api/graph')
        const { nodes: graphNodes, edges: graphEdges } = res.data

        // Layout nodes roughly in a line for simplicity
        const layoutedNodes = graphNodes.map((node, i) => ({
          id: node.id,
          data: { label: node.label },
          position: { x: i * 200, y: 100 },
          style: { background: '#fff', border: '1px solid #777', borderRadius: '5px', padding: '10px' }
        }))

        const layoutedEdges = graphEdges.map((edge, i) => ({
          id: `e-${i}`,
          source: edge.source,
          target: edge.target,
          animated: true
        }))

        setNodes(layoutedNodes)
        setEdges(layoutedEdges)
      } catch (e) {
        console.error("Failed to fetch graph structure", e)
      }
    }
    fetchGraph()
  }, [])

  useEffect(() => {
    const fetchActiveNode = async () => {
      try {
        const res = await axios.get('/api/graph/active-node')
        const currentActive = res.data.active_node
        setActiveNode(currentActive)

        setNodes((nds) => nds.map((node) => {
          if (node.id === currentActive) {
            return { ...node, style: { ...node.style, background: '#ffeb3b', border: '2px solid #fbc02d' } }
          }
          return { ...node, style: { ...node.style, background: '#fff', border: '1px solid #777' } }
        }))
      } catch (e) {
        console.error("Failed to fetch active node", e)
      }
    }
    const interval = setInterval(fetchActiveNode, 2000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ height: '300px', border: '1px solid #ddd', borderRadius: '5px', background: '#f9f9f9', marginBottom: '20px' }}>
      <h3 style={{ margin: '10px' }}>LangGraph Execution Flow</h3>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}

export default FlowGraph
