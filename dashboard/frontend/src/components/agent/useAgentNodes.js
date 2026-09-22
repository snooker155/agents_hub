/**
 * Starting, stopping, removing and tailing the nodes that carry this agent.
 *
 * The node list itself belongs to the page (the header counts it, and two tabs
 * read it); what lives here is everything about *acting* on one, including the
 * two overlays those actions open. `onChanged` is the page's reload, since
 * every one of these actions changes what the list says.
 */
import { useState } from 'react';
import { deleteNode, getNodeLogs, startNode, stopNode } from '../../api';

export function useAgentNodes({ id, onChanged, t }) {
  const [showStartNodeModal, setShowStartNodeModal] = useState(false);
  const [startWorkspace, setStartWorkspace] = useState('');
  const [startLabel, setStartLabel] = useState('');
  const [startingNode, setStartingNode] = useState(false);
  const [nodeBusy, setNodeBusy] = useState({});
  const [logsNode, setLogsNode] = useState(null);
  const [logsNodeText, setLogsNodeText] = useState('');
  const [logsNodeLoading, setLogsNodeLoading] = useState(false);

  const handleStartNode = async () => {
    setStartingNode(true);
    try {
      await startNode({ agent_id: id, workspace: startWorkspace || null, label: startLabel || null });
      setShowStartNodeModal(false);
      setStartWorkspace('');
      setStartLabel('');
      onChanged();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || t('agentDetails.errors.startNode'));
    } finally {
      setStartingNode(false);
    }
  };

  const handleStopNode = async (nodeId) => {
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'stopping' }));
    try {
      await stopNode(nodeId);
      onChanged();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || t('agentDetails.errors.stopNode'));
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const handleDeleteNode = async (nodeId) => {
    if (!window.confirm(t('agentDetails.confirmRemoveNode'))) return;
    setNodeBusy((prev) => ({ ...prev, [nodeId]: 'deleting' }));
    try {
      await deleteNode(nodeId);
      onChanged();
    } catch (error) {
      alert(error.response?.data?.detail || error.message || t('agentDetails.errors.removeNode'));
    } finally {
      setNodeBusy((prev) => {
        const next = { ...prev };
        delete next[nodeId];
        return next;
      });
    }
  };

  const openNodeLogs = async (node) => {
    const nodeId = node.node_id || node.id;
    setLogsNode(node);
    setLogsNodeLoading(true);
    setLogsNodeText('');
    try {
      const resp = await getNodeLogs(nodeId);
      setLogsNodeText(resp.data?.logs || '(empty)');
    } catch (error) {
      setLogsNodeText(error.response?.data?.detail || t('agentDetails.errors.nodeLogs'));
    } finally {
      setLogsNodeLoading(false);
    }
  };


  return {
    showStartNodeModal, setShowStartNodeModal,
    startWorkspace, setStartWorkspace, startLabel, setStartLabel, startingNode,
    nodeBusy, logsNode, setLogsNode, logsNodeText, logsNodeLoading,
    handleStartNode, handleStopNode, handleDeleteNode, openNodeLogs,
  };
}

export default useAgentNodes;
