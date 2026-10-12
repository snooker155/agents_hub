import { useCallback, useEffect, useMemo } from 'react';
import { applyEdgeChanges, applyNodeChanges } from 'reactflow';
import {
  exportFlow, getAgents, getFlow, listFlowEntities, updateFlow,
} from '../../api';
import { errorDetail } from '../toast';
import { isUnchosenAgent, normalizeNode, serializeEdge, serializeNode } from './graphHelpers';

// Agents that exist for internal wiring (orchestration, flow-as-agent, tests)
// but should never be offered as the agent of a node.
export const EXCLUDED_AGENT_IDS = new Set([
  'orchestrator', 'agent_creator', 'flow-graph', 'flow-custom-graph', 'test-agent',
  'research-remote', 'example-agent',
]);

const EDGE_STYLE = { stroke: '#0891b2', strokeWidth: 2 };

export const toCanvasEdges = (edges) =>
  (edges || []).map((edge) => ({ ...edge, animated: false, style: EDGE_STYLE }));

/**
 * The flow document on the editor page (pages/FlowEditor.jsx): loading it
 * plus the agent/entity registries it draws nodes from, editing nodes and
 * flow-level settings, saving and exporting. Running the flow and its
 * history belong to the run page (pages/FlowRun.jsx, useFlowRuns).
 */
export function useFlowDocument(deps) {
  const {
    availableAgents, dirty, edges, flow, flowId, navigate,
    nodes, selectedNodeId, selectedWorkspace, setAvailableAgents,
    setDirty, setEdges, setEntitiesByCategory, setFlow, setLoading, setNodes,
    setSaving, t, toast, entitiesByCategory,
  } = deps;

  const loadFlow = async () => {
    setLoading(true);
    try {
      const workspaceForAgents = selectedWorkspace || null;
      const [flowResponse, agentsResponse, entitiesResponse] = await Promise.all([
        getFlow(flowId), getAgents(workspaceForAgents), listFlowEntities(undefined, workspaceForAgents),
      ]);
      const nextFlow = flowResponse.data;
      setFlow(nextFlow);
      setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node)));
      setEdges(toCanvasEdges(nextFlow.edges));
      setAvailableAgents(
        (agentsResponse.data || []).filter((agent) => !EXCLUDED_AGENT_IDS.has(agent.id))
      );
      // Build the registry map, applying the same agent exclusions. The
      // palette shows the agent category as one abstract node; the list
      // itself feeds the inspector's agent picker.
      const grouped = entitiesResponse.data || {};
      const filteredGrouped = {};
      for (const [cat, list] of Object.entries(grouped)) {
        const items = cat === 'agent'
          ? (list || []).filter((e) => !EXCLUDED_AGENT_IDS.has(e.id))
          : (list || []);
        if (items.length) filteredGrouped[cat] = items;
      }
      setEntitiesByCategory(filteredGrouped);
    } catch (error) {
      console.error('Failed to load flow', error);
      navigate('/flows');
    } finally {
      setLoading(false);
    }
  };

  // Reloads on flow or workspace change only.
  useEffect(() => {
    loadFlow();
  }, [flowId, selectedWorkspace]); // eslint-disable-line react-hooks/exhaustive-deps

  const persistFlow = async (override = {}) => {
    if (!flow) return null;
    setSaving(true);
    try {
      const payload = {
        name: flow.name,
        description: flow.description,
        workspace: flow.workspace || selectedWorkspace || null,
        nodes: nodes.map(serializeNode),
        edges: edges.map(serializeEdge),
        // Flow-level meta (logic). Send only when set so we never clobber
        // existing values with undefined.
        ...(flow.entry_point !== undefined ? { entry_point: flow.entry_point } : {}),
        ...(flow.mutability !== undefined ? { mutability: flow.mutability } : {}),
        ...(flow.recordability !== undefined ? { recordability: flow.recordability } : {}),
        ...(flow.state !== undefined ? { state: flow.state } : {}),
        // Execution policy (flow/engine.py). Sent only when set, so a flow that
        // never touched them keeps the engine's own defaults.
        ...(flow.on_error !== undefined ? { on_error: flow.on_error } : {}),
        ...(flow.max_parallel !== undefined ? { max_parallel: flow.max_parallel } : {}),
        ...override,
      };
      const response = await updateFlow(flowId, payload);
      setFlow(response.data);
      setNodes((response.data.nodes || []).map((node) => normalizeNode(node)));
      setEdges(toCanvasEdges(response.data.edges));
      setDirty(false);
      return response.data;
    } catch (error) {
      toast.error(t('flowEditor.saveFailed'), errorDetail(error));
      return null;
    } finally {
      setSaving(false);
    }
  };

  const updateSelectedNode = (patch) => {
    if (!selectedNodeId) return;
    setNodes((current) =>
      current.map((node) =>
        node.id === selectedNodeId
          ? { ...node, data: { ...node.data, ...patch } }
          : node
      )
    );
    setDirty(true);
  };

  const handleCanvasNodesChange = useCallback((changes) => {
    setNodes((current) => applyNodeChanges(changes, current).map((node) => ({
      ...node,
      style: { width: 90, ...(node.style || {}) },
    })));
    // Selecting, measuring and clicking a node are changes React Flow reports
    // too (a click is a position change with no position); only edits dirty
    // the document.
    if (changes.some((c) => (c.type === 'position' ? !!c.position : c.type !== 'select' && c.type !== 'dimensions'))) {
      setDirty(true);
    }
  }, [setDirty, setNodes]);

  const handleCanvasEdgesChange = useCallback((changes) => {
    setEdges((current) => applyEdgeChanges(changes, current));
    if (changes.some((c) => c.type !== 'select')) setDirty(true);
  }, [setDirty, setEdges]);

  const setCanvasNodes = useCallback((value) => {
    setNodes((current) => {
      const next = typeof value === 'function' ? value(current) : value;
      return next.map((node) => normalizeNode(node));
    });
    setDirty(true);
  }, [setDirty, setNodes]);

  const setCanvasEdges = useCallback((value) => {
    setEdges((current) => (typeof value === 'function' ? value(current) : value));
    setDirty(true);
  }, [setDirty, setEdges]);

  const handleExportFlow = async () => {
    if (!flow) return;
    // Export reads the persisted YAML, so flush any unsaved edits first.
    if (dirty) {
      const saved = await persistFlow();
      if (!saved) return;
    }
    try {
      const response = await exportFlow(flowId);
      const blob = new Blob([response.data], { type: 'application/x-yaml' });
      const url = URL.createObjectURL(blob);
      const slug = (flow.name || flowId).replace(/[^a-zA-Z0-9-_]+/g, '-').replace(/^-+|-+$/g, '') || 'flow';
      const link = document.createElement('a');
      link.href = url;
      link.download = `${slug}.yaml`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(t('flowEditor.exportFailed'), errorDetail(error));
    }
  };

  // Write-only: the secret goes up, and the flow comes back saying only whether
  // one is set. An empty string clears it.
  const handleSetWebhookSecret = async (secret) => {
    try {
      const { data } = await updateFlow(flowId, { webhook_secret: secret });
      setFlow((prev) => ({ ...prev, webhook_secret_configured: !!data.webhook_secret_configured }));
      toast.success(secret ? t('flowEditor.webhookSecretSaved') : t('flowEditor.webhookSecretCleared'));
    } catch (error) {
      toast.error(t('flowEditor.webhookSecretFailed'), errorDetail(error));
    }
  };

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  // What the inspector's agent picker offers: the workspace's agents as the
  // flow registry lists them, else the plain agent list.
  const agentOptions = useMemo(
    () => (entitiesByCategory?.agent?.length ? entitiesByCategory.agent : availableAgents),
    [entitiesByCategory, availableAgents]
  );

  const unchosenCount = useMemo(
    () => nodes.filter((node) => isUnchosenAgent(node.data)).length,
    [nodes]
  );

  return {
    agentOptions, handleCanvasEdgesChange, handleCanvasNodesChange, handleExportFlow,
    handleSetWebhookSecret, loadFlow, persistFlow, selectedNode, setCanvasEdges,
    setCanvasNodes, unchosenCount, updateSelectedNode,
  };
}

export default useFlowDocument;
