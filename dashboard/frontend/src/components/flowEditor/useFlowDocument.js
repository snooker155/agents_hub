import { useCallback, useEffect, useMemo } from 'react';
import { applyEdgeChanges, applyNodeChanges } from 'reactflow';
import {
  exportFlow, getAgents, getFlow, getTasks, listFlowEntities, updateFlow,
} from '../../api';
import { errorDetail } from '../toast';
import { normalizeNode, serializeEdge, serializeNode } from './graphHelpers';

// Agents that exist for internal wiring (orchestration, flow-as-agent, tests)
// but should never show up as a node a person drags onto a canvas.
const EXCLUDED_AGENT_IDS = new Set([
  'orchestrator', 'agent_creator', 'flow-graph', 'flow-custom-graph', 'test-agent',
  'research-remote', 'example-agent',
]);

/**
 * The flow document itself: loading it plus the agent/entity registries it
 * draws nodes from, editing nodes and flow-level settings, and saving. Runs
 * (executions, history, live logs) are a separate concern, see useFlowRuns.
 *
 * `onRunNode` is a stable callback (identity never changes across renders):
 * the page hands it to normalizeNode so a node's onRunNode always dispatches
 * to whatever useFlowRuns's handleRunNode currently is, without this hook
 * needing to depend on that hook directly.
 */
export function useFlowDocument(deps) {
  const {
    availableAgents, dirty, edges, flow, flowId, navigate,
    nodes, onRunNode, selectedNodeId, selectedWorkspace, setAvailableAgents,
    setDirty, setEdges, setEntitiesByCategory, setFlow, setLoading, setNodes,
    setSaving, setTasks, t, tasks, toast,
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
      setNodes((nextFlow.nodes || []).map((node) => normalizeNode(node, onRunNode)));
      setEdges((nextFlow.edges || []).map((edge) => ({
        ...edge,
        animated: false,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      setAvailableAgents(
        (agentsResponse.data || []).filter((agent) => !EXCLUDED_AGENT_IDS.has(agent.id))
      );
      // Build the registry palette map, applying the same agent exclusions.
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

  // Reloads on flow or workspace change only. loadFlow captures onRunNode, a
  // stable wrapper, so this is safe to run only on the ids that actually
  // identify which flow to load.
  useEffect(() => {
    loadFlow();
  }, [flowId, selectedWorkspace]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadWorkspaceTasks = async (workspaceName) => {
    if (!workspaceName) return;
    try {
      const response = await getTasks(workspaceName);
      setTasks(response.data || []);
    } catch (error) {
      console.error('Failed to load tasks', error);
    }
  };

  const persistFlow = async (override = {}) => {
    if (!flow) return null;
    setSaving(true);
    try {
      const payload = {
        name: flow.name,
        description: flow.description,
        workspace: flow.workspace || selectedWorkspace || null,
        task_id: flow.task_id || null,
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
      setNodes((response.data.nodes || []).map((node) => normalizeNode(node, onRunNode)));
      setEdges((response.data.edges || []).map((edge) => ({
        ...edge,
        animated: false,
        style: { stroke: '#0891b2', strokeWidth: 2 },
      })));
      setDirty(false);
      return response.data;
    } catch (error) {
      alert(`Failed to save flow: ${error.response?.data?.detail || error.message}`);
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
          ? { ...node, data: { ...node.data, ...patch, onRunNode } }
          : node
      )
    );
    setDirty(true);
  };

  const patchNodeHandlers = (items) => items.map((node) => normalizeNode(node, onRunNode));

  const handleCanvasNodesChange = useCallback((changes) => {
    setNodes((current) => applyNodeChanges(changes, current).map((node) => ({
      ...node,
      style: { width: 90, ...(node.style || {}) },
      data: { ...node.data, onRunNode },
    })));
    setDirty(true);
  }, [onRunNode, setDirty, setNodes]);

  const handleCanvasEdgesChange = useCallback((changes) => {
    setEdges((current) => applyEdgeChanges(changes, current));
    setDirty(true);
  }, [setDirty, setEdges]);

  const setCanvasNodes = useCallback((value) => {
    setNodes((current) => {
      const next = typeof value === 'function' ? value(current) : value;
      setDirty(true);
      return patchNodeHandlers(next);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onRunNode, setDirty, setNodes]);

  const setCanvasEdges = useCallback((value) => {
    setEdges((current) => {
      const next = typeof value === 'function' ? value(current) : value;
      setDirty(true);
      return next;
    });
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
      alert(`Failed to export flow: ${error.response?.data?.detail || error.message}`);
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

  // Called by the Create Task modal once a task is created. Attach the new task
  // to this flow (mirroring the old inline form) and refresh the task list.
  const handleTaskCreated = async (task) => {
    const workspaceName = flow?.workspace || selectedWorkspace;
    if (task?.id) {
      const nextDescription = flow?.description?.trim()
        ? flow.description
        : (task.description || '').trim();
      setFlow((current) => ({
        ...current,
        workspace: workspaceName || current.workspace,
        task_id: String(task.id),
        description: nextDescription,
      }));
      setDirty(true);
    }
    await loadWorkspaceTasks(workspaceName);
  };

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const agentLabels = useMemo(() => {
    const map = {};
    for (const agent of availableAgents) map[agent.id] = agent.name;
    for (const node of nodes) {
      if (node.data?.agent_id) map[node.data.agent_id] = node.data.label || map[node.data.agent_id];
    }
    return map;
  }, [availableAgents, nodes]);

  const assignedTask = useMemo(
    () => tasks.find((task) => String(task.id) === String(flow?.task_id)) || null,
    [tasks, flow?.task_id]
  );

  const chatWorkspace = flow?.workspace || selectedWorkspace;

  return {
    agentLabels, assignedTask, chatWorkspace, handleCanvasEdgesChange,
    handleCanvasNodesChange, handleExportFlow, handleSetWebhookSecret, handleTaskCreated,
    loadFlow, loadWorkspaceTasks, patchNodeHandlers, persistFlow, selectedNode,
    setCanvasEdges, setCanvasNodes, updateSelectedNode,
  };
}

export default useFlowDocument;
