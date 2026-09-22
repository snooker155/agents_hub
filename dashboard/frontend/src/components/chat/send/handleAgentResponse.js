/**
 * The events shared by every send mode once the team- and flow-specific ones
 * are out of the way: delegation nesting, run metadata, reasoning, artifacts,
 * the graph mirror, tool calls, streamed tokens, the final `done`, and the
 * compaction notice. Handles agent mode's single bubble and flow mode's
 * per-node bubble alike (via `targetMsgId`); team mode has no such target, so
 * every branch below quietly no-ops for it.
 */
import { appendIntoDelegation, appendUnderDelegation, mapDelegation, resolveDelegationTool } from '../delegationTimeline';
import { stripUiBlock } from '../markdown';
import { reduceGraphRun } from '../../graphRun';
import { appendLiveThought, buildCompactionNotice, mergeMessageFile } from '../turnState';

// Which bubble a per-turn event belongs in: the single assistant bubble in
// agent mode, or the bubble for the currently active node in flow mode.
function targetMsgId(event, ctx) {
  if (ctx.isFlowMode) {
    const nid = event.node_id || ctx.state.currentNodeId;
    return nid ? ctx.state.nodeMsgIds[nid] : null;
  }
  return ctx.assistantId;
}

// Delegated child runs (run_agent_tool) stream their inner events tagged with
// `delegation` + the child run_id. Folded into a nested `delegation` timeline
// entry instead of the top-level timeline, so the UI renders a live nested
// block.
function handleDelegationEvent(event, ctx) {
  if (!(event.type === 'delegation_start' || event.type === 'delegation_end' || event.delegation)) {
    return false;
  }
  const { convId, setConversations } = ctx;
  const tgt = targetMsgId(event, ctx);
  if (tgt) {
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) => {
            if (m.id !== tgt) return m;
            const messageRunId = m.run_id || null;
            let tl = m.timeline || [];
            if (event.type === 'delegation_start') {
              tl = appendUnderDelegation(tl, event.parent_run_id, messageRunId, {
                type: 'delegation',
                run_id: event.run_id,
                agent_id: event.agent_id,
                agent_name: event.agent_name || event.agent_id,
                depth: event.depth || 1,
                input: event.input || '',
                running: true,
                ok: null,
                timeline: [],
              });
            } else if (event.type === 'delegation_end') {
              tl = mapDelegation(tl, event.run_id, (d) => ({
                ...d,
                running: false,
                ok: event.ok,
                error: event.error || '',
                duration_ms: event.duration_ms,
              }));
            } else if (event.type === 'think' || event.type === 'plan') {
              tl = appendIntoDelegation(tl, event.run_id, {
                type: 'reasoning', kind: event.type, step: event.step, content: event.content,
              });
            } else if (event.type === 'tool_start') {
              tl = appendIntoDelegation(tl, event.run_id, {
                type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true,
              });
            } else if (event.type === 'tool_end') {
              tl = resolveDelegationTool(tl, event.run_id, { output: event.output });
            } else if (event.type === 'tool_error') {
              tl = resolveDelegationTool(tl, event.run_id, { output: `ERROR: ${event.error}`, error: true });
            }
            return { ...m, timeline: tl };
          }),
        },
      ),
    );
  }
  return true;
}

function handleAgentEvent(event, ctx) {
  if (handleDelegationEvent(event, ctx)) return;

  const {
    convId, setConversations, setActiveRunId, setSessionId, setGraphRun,
    setProcessInsights, mergeArtifact, processOpen, isMultiAgent, assistantId,
    userMsgText, t, state,
  } = ctx;

  if (event.type === 'meta' && event.run_id) {
    state.runId = event.run_id;
    setActiveRunId(state.runId);
    if (event.session_id) setSessionId(event.session_id);
    if (processOpen && !isMultiAgent) {
      // In flow mode, node_start already created the process row.
      setProcessInsights((prev) => ({
        ...prev,
        message_runs: [
          ...(prev.message_runs || []),
          {
            message_id: state.runId,
            run_id: state.runId,
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
          },
        ],
      }));
    }
    if (!isMultiAgent) {
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) => (m.id === assistantId ? { ...m, run_id: state.runId } : m)),
          },
        ),
      );
    }
  } else if (event.type === 'think_delta') {
    // A slice of reasoning the model is still writing. Shown as a
    // three-line ticker under the "working" indicator until the matching
    // `think` event arrives with the completed thought.
    const tgt = targetMsgId(event, ctx);
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) =>
            m.id === tgt
              ? { ...m, thinking_live: appendLiveThought(m.thinking_live, event.delta) }
              : m
          ),
        },
      ),
    );
  } else if (event.type === 'think' || event.type === 'plan') {
    // Reasoning steps are appended in execution order so the UI can render
    // each one inline, before the response that followed it. Also appended
    // to `timeline` (the Build-view chronological feed).
    const tgt = targetMsgId(event, ctx);
    const step = { kind: event.type, step: event.step, content: event.content };
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) =>
            m.id === tgt
              ? {
                  ...m,
                  reasoning: [...(m.reasoning || []), step],
                  timeline: [...(m.timeline || []), { type: 'reasoning', ...step }],
                  // The thought is complete and now renders as a collapsible
                  // ReasoningStep, so the live ticker of the same text goes away.
                  thinking_live: '',
                }
              : m
          ),
        },
      ),
    );
    // Native model thoughts also feed the Process column live.
    if (processOpen && event.native) {
      setProcessInsights((prev) => ({
        ...prev,
        message_runs: (prev.message_runs || []).map((mr, idx) =>
          idx === (prev.message_runs || []).length - 1
            ? { ...mr, reasoning: [...(mr.reasoning || []), { step: event.step, content: event.content, native: true }] }
            : mr
        ),
      }));
    }
  } else if (event.type === 'artifact') {
    // File change — surface in the Artifacts panel and inline in the feed.
    mergeArtifact(event);
    const tgt = targetMsgId(event, ctx);
    const entry = {
      type: 'artifact',
      op: event.op,
      path: event.path,
      additions: event.additions,
      deletions: event.deletions,
    };
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) =>
            m.id === tgt
              ? {
                  ...m,
                  timeline: [...(m.timeline || []), entry],
                  // Persisted alongside the reply so the chat bubble lists
                  // what the agent changed, not just the Artifacts column.
                  files: mergeMessageFile(m.files, event),
                }
              : m
          ),
        },
      ),
    );
  } else if (event.type === 'graph_node_start') {
    setGraphRun((prev) => reduceGraphRun(prev, event));
    // An imported agent walking its own graph. One timeline step per node, in
    // the current bubble: the agent is still one agent, and the nodes are how
    // it got to the answer.
    const tgt = targetMsgId(event, ctx);
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) =>
            m.id === tgt
              ? {
                  ...m,
                  timeline: [
                    ...(m.timeline || []),
                    { type: 'graph_node', node: event.node, depth: event.depth || 0, running: true, ok: null },
                  ],
                }
              : m
          ),
        },
      ),
    );
  } else if (event.type === 'graph_node_end') {
    setGraphRun((prev) => reduceGraphRun(prev, event));
    const tgt = targetMsgId(event, ctx);
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) => {
            if (m.id !== tgt || !m.timeline) return m;
            const tl = [...m.timeline];
            // Last running entry with this name: a graph with a loop enters
            // the same node more than once, and resolving the first would
            // leave later passes looking unfinished.
            for (let i = tl.length - 1; i >= 0; i -= 1) {
              if (tl[i].type === 'graph_node' && tl[i].running
                  && (!event.node || tl[i].node === event.node)) {
                tl[i] = { ...tl[i], running: false, ok: event.ok !== false, error: event.error || '', next: event.next || null };
                break;
              }
            }
            return { ...m, timeline: tl };
          }),
        },
      ),
    );
  } else if (event.type === 'tool_start') {
    // Update message bubble to show the running tool name, and append a tool
    // entry to the Build-view timeline (resolved on tool_end).
    const tgt = targetMsgId(event, ctx);
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) =>
            m.id === tgt
              ? {
                  ...m,
                  running_tool: event.tool,
                  timeline: [
                    ...(m.timeline || []),
                    { type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true },
                  ],
                }
              : m
          ),
        },
      ),
    );
    if (processOpen) {
      setProcessInsights((prev) => ({
        ...prev,
        tools: [
          ...(prev.tools || []),
          { step: event.step, tool: event.tool, input: event.input, output: null, running: true },
        ],
        message_runs: (prev.message_runs || []).map((mr, idx) =>
          idx === (prev.message_runs || []).length - 1
            ? {
                ...mr,
                tools: [
                  ...(mr.tools || []),
                  { step: event.step, tool: event.tool, input: event.input, output: null, running: true },
                ],
                tool_calls: ((mr.tool_calls || 0) + 1),
              }
            : mr
        ),
      }));
    }
  } else if (event.type === 'tool_end') {
    // Keep running_tool set so the name stays visible until the next token arrives.
    // Resolve the last running tool entry in the Build-view timeline.
    {
      const tgt = targetMsgId(event, ctx);
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) => {
              if (m.id !== tgt || !m.timeline) return m;
              const tl = [...m.timeline];
              for (let i = tl.length - 1; i >= 0; i -= 1) {
                if (tl[i].type === 'tool' && tl[i].running) {
                  tl[i] = { ...tl[i], output: event.output, running: false };
                  break;
                }
              }
              return { ...m, timeline: tl };
            }),
          },
        ),
      );
    }
    if (processOpen) {
      setProcessInsights((prev) => {
        const tools = [...(prev.tools || [])];
        for (let i = tools.length - 1; i >= 0; i -= 1) {
          if (tools[i].running) {
            tools[i] = { ...tools[i], output: event.output, running: false };
            break;
          }
        }
        const message_runs = (prev.message_runs || []).map((mr, idx) => {
          if (idx !== (prev.message_runs || []).length - 1) return mr;
          const mrTools = [...(mr.tools || [])];
          for (let i = mrTools.length - 1; i >= 0; i -= 1) {
            if (mrTools[i].running) {
              mrTools[i] = { ...mrTools[i], output: event.output, running: false };
              break;
            }
          }
          return { ...mr, tools: mrTools };
        });
        return { ...prev, tools, message_runs };
      });
    }
  } else if (event.type === 'token') {
    const tgt = targetMsgId(event, ctx);
    const tok = event.token || '';
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : {
          ...c,
          messages: c.messages.map((m) => {
            if (m.id !== tgt) return m;
            // Coalesce contiguous tokens into the trailing text segment so
            // the Build-view feed shows continuous prose, not per-token noise.
            const tl = [...(m.timeline || [])];
            const last = tl[tl.length - 1];
            if (last && last.type === 'text') {
              tl[tl.length - 1] = { ...last, text: `${last.text || ''}${tok}` };
            } else {
              tl.push({ type: 'text', text: tok });
            }
            return {
              ...m,
              content: `${m.content || ''}${tok}`,
              running_tool: null,
              timeline: tl,
            };
          }),
        },
      ),
    );
    if (processOpen) {
      setProcessInsights((prev) => ({
        ...prev,
        message_runs: (prev.message_runs || []).map((mr, idx) =>
          idx === (prev.message_runs || []).length - 1
            ? { ...mr, output: `${mr.output || ''}${event.token || ''}` }
            : mr
        ),
      }));
    }
  } else if (event.type === 'done') {
    state.finalPayload = event;
    const resolvedRunId = state.runId || event.run_id || null;
    if (resolvedRunId) setActiveRunId(resolvedRunId);
    // In flow mode the per-node bubbles were already finalized via node_done,
    // and in team mode every reply is already on the board, so the overall
    // "done" event only carries run-level metadata.
    if (!isMultiAgent) {
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    // Prefer the backend's final response (the de-duplicated
                    // AgentFinish output). Streamed tokens accumulate text from
                    // every LLM turn — including forced-review re-statements — so
                    // using them as-is can repeat the answer. Fall back to the
                    // streamed content only when no final response was sent.
                    content: (event.response || '').trim() || (event.response_obj ? '' : stripUiBlock(m.content)) || '',
                    // Structured response (buttons / Telegram keyboard / …); rendered
                    // as interactive UI under the bubble. null for plain replies.
                    response_obj: event.response_obj || null,
                    // Service entities this turn touched, rendered as links
                    // under the reply (see EntityLinks).
                    entities: event.entities || m.entities || null,
                    thinking_live: '',
                    error: !event.ok,
                    run_id: resolvedRunId,
                    inbound_tokens: event.usage?.inbound_tokens ?? m.inbound_tokens ?? null,
                    outbound_tokens: event.usage?.outbound_tokens ?? m.outbound_tokens ?? null,
                    total_tokens: event.usage?.total_tokens ?? m.total_tokens ?? null,
                    tool_calls: event.tool_calls ?? m.tool_calls ?? null,
                    duration_ms: event.duration_ms ?? m.duration_ms ?? null,
                    // How full the model's window was at this turn's biggest
                    // call, kept on the message so the meter survives
                    // switching conversations and reloading — the transcript
                    // is the context, and it is what localStorage holds.
                    context_used: event.usage?.context_used ?? m.context_used ?? null,
                    context_window: event.usage?.context_window ?? m.context_window ?? null,
                    context_overflow: event.error_code === 'context_overflow',
                  }
                : m
            ),
          },
        ),
      );
    }
    if (processOpen && !isMultiAgent) {
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
          message_runs: (prev.message_runs || []).map((mr, idx) =>
            idx === (prev.message_runs || []).length - 1
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
  } else if (event.type === 'compaction') {
    // Older history was folded into a summary. Inserted right before the
    // bubble it precedes: agent mode's single bubble already exists by the
    // time this can arrive, so the notice goes above it; flow/team mode has
    // no bubble yet at this point, so it's appended, and the bubbles that
    // follow (node_start / team_message) land after it in order.
    const notice = buildCompactionNotice(t, event);
    setConversations((prev) =>
      prev.map((c) => {
        if (c.id !== convId) return c;
        const idx = assistantId ? c.messages.findIndex((m) => m.id === assistantId) : -1;
        const messages = [...c.messages];
        if (idx === -1) messages.push(notice);
        else messages.splice(idx, 0, notice);
        return { ...c, messages };
      }),
    );
  }
}

export { handleAgentEvent, targetMsgId };
