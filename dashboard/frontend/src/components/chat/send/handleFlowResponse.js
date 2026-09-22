/**
 * Flow turns: one bubble per node, created on `node_start` and finalized on
 * `node_done`. `flow_meta` only carries the session id.
 */
import { genId } from '../turnState';
import { stripUiBlock } from '../markdown';

// Returns true when it handled `event`, so the caller knows not to try the
// generic handler on the same event.
function handleFlowEvent(event, ctx) {
  const {
    convId, setConversations, setActiveRunId, setSessionId,
    processOpen, setProcessInsights, userMsgText, state,
  } = ctx;

  if (event.type === 'flow_meta') {
    if (event.session_id) setSessionId(event.session_id);
    return true;
  }

  if (event.type === 'node_start') {
    // Create a new assistant bubble for this node.
    state.currentNodeId = event.node_id;
    const msgId = genId();
    state.nodeMsgIds[event.node_id] = msgId;
    const nodeRunId = event.run_id || null;
    if (nodeRunId) {
      state.runId = nodeRunId;
      setActiveRunId(nodeRunId);
    }
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: [
            ...c.messages,
            {
              id: msgId,
              role: 'agent',
              agent_id: event.agent_id || event.agent_label || '',
              agent_label: event.agent_label || '',
              node_id: event.node_id,
              content: '',
              error: false,
              run_id: nodeRunId,
              inbound_tokens: null,
              outbound_tokens: null,
              total_tokens: null,
              tool_calls: null,
              duration_ms: null,
            },
          ],
        },
      ),
    );
    if (processOpen && nodeRunId) {
      setProcessInsights((prev) => ({
        ...prev,
        message_runs: [
          ...(prev.message_runs || []),
          {
            message_id: nodeRunId,
            run_id: nodeRunId,
            timestamp: new Date().toISOString(),
            input: userMsgText,
            output: '',
            tools: [],
            thinking: [],
            inbound_tokens: 0,
            outbound_tokens: 0,
            total_tokens: 0,
            tool_calls: 0,
            duration_ms: 0,
            agent_id: event.agent_id || '',
          },
        ],
      }));
    }
    return true;
  }

  if (event.type === 'node_done') {
    // Finalize one node's bubble; the surrounding loop continues into the next node.
    const msgId = state.nodeMsgIds[event.node_id];
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) =>
            m.id === msgId
              ? {
                  ...m,
                  // Prefer the node's final response over accumulated stream
                  // tokens (see handleAgentResponse's `done` handling) to
                  // avoid repeating the answer when the model re-states it
                  // across LLM turns.
                  content: (event.response || '').trim() || (event.response_obj ? '' : stripUiBlock(m.content)) || '',
                  entities: event.entities || m.entities || null,
                  thinking_live: '',
                  error: !event.ok,
                  run_id: event.run_id || m.run_id,
                  inbound_tokens: event.usage?.inbound_tokens ?? null,
                  outbound_tokens: event.usage?.outbound_tokens ?? null,
                  total_tokens: event.usage?.total_tokens ?? null,
                  tool_calls: event.tool_calls ?? null,
                  duration_ms: event.duration_ms ?? null,
                }
              : m
          ),
        },
      ),
    );
    if (processOpen) {
      setProcessInsights((prev) => {
        const inTok = event.usage?.inbound_tokens || 0;
        const outTok = event.usage?.outbound_tokens || 0;
        const totTok = event.usage?.total_tokens || (inTok + outTok);
        return {
          ...prev,
          token_usage: {
            inbound_tokens: (prev.token_usage?.inbound_tokens || 0) + inTok,
            outbound_tokens: (prev.token_usage?.outbound_tokens || 0) + outTok,
            total_tokens: (prev.token_usage?.total_tokens || 0) + totTok,
          },
          context_peak: Math.max(prev.context_peak || 0, event.usage?.context_used || 0),
          context_window_tokens: event.usage?.context_window || prev.context_window_tokens || 0,
          message_runs: (prev.message_runs || []).map((mr) =>
            mr.run_id === event.run_id
              ? {
                  ...mr,
                  output: event.response || mr.output || '',
                  inbound_tokens: inTok,
                  outbound_tokens: outTok,
                  total_tokens: totTok,
                  tool_calls: event.tool_calls ?? mr.tool_calls ?? 0,
                  duration_ms: event.duration_ms ?? mr.duration_ms ?? 0,
                }
              : mr
          ),
        };
      });
    }
    return true;
  }

  return false;
}

export { handleFlowEvent };
