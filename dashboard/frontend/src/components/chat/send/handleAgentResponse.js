/**
 * The events shared by every send mode once the team- and flow-specific ones
 * are out of the way: delegation nesting, run metadata, reasoning, artifacts,
 * the graph mirror, tool calls, streamed tokens, a handoff to another agent,
 * the final `done`, and the compaction notice. Handles agent mode's single bubble and flow mode's
 * per-node bubble alike (via `targetMsgId`); team mode has no such target, so
 * every branch below quietly no-ops for it.
 */
import { stripUiBlock } from '../markdown';
import { reduceGraphRun } from '../../graphRun';
import { buildCompactionNotice, genId } from '../turnState';
import { applyTurnEvent, isDelegationEvent } from '../turnEvents';
import { applyHandoff } from '../handoff';
import { applyUndelivered, markSteerDelivered } from '../steering';
import { applyApprovalEvent } from '../toolApprovals';
import { applyDelegationEvent } from '../processLive';
import { toolOutcome } from '../../toolStatus';

// Which bubble a per-turn event belongs in: the single assistant bubble in
// agent mode, or the bubble for the currently active node in flow mode.
function targetMsgId(event, ctx) {
  if (ctx.isFlowMode) {
    const nid = event.node_id || ctx.state.currentNodeId;
    return nid ? ctx.state.nodeMsgIds[nid] : null;
  }
  return ctx.assistantId;
}

// Fold `event` into the target bubble with the reducer every chat shares
// (../turnEvents.js), so this page and the embedded chats read a turn alike.
function foldIntoTarget(event, ctx) {
  const tgt = targetMsgId(event, ctx);
  if (!tgt) return;
  const { convId, setConversations } = ctx;
  setConversations((prev) =>
    prev.map((c) =>
      c.id !== convId ? c : {
        ...c,
        messages: c.messages.map((m) => (m.id === tgt ? applyTurnEvent(m, event) : m)),
      },
    ),
  );
}

// Delegated child runs (run_agent_tool) stream their inner events tagged with
// `delegation` + the child run_id. Folded into a nested `delegation` timeline
// entry instead of the top-level timeline, so the UI renders a live nested
// block, and into the Process panel's run row, so the worker's graph grows
// step by step (see ../processLive).
function handleDelegationEvent(event, ctx) {
  if (!isDelegationEvent(event)) return false;
  ctx.setProcessInsights?.((prev) => {
    const message_runs = applyDelegationEvent(prev.message_runs, event);
    return message_runs === prev.message_runs ? prev : { ...prev, message_runs };
  });
  foldIntoTarget(event, ctx);
  return true;
}

function handleAgentEvent(event, ctx) {
  if (handleDelegationEvent(event, ctx)) return;

  const {
    convId, setConversations, setActiveRunId, setSessionId, setGraphRun,
    setProcessInsights, mergeArtifact, isMultiAgent, assistantId,
    userMsgText, t, state,
  } = ctx;

  if (event.type === 'meta' && event.run_id) {
    state.runId = event.run_id;
    setActiveRunId(state.runId);
    if (event.session_id) setSessionId(event.session_id);
    if (!isMultiAgent) {
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
    // A slice of reasoning still being written: the ticker under "working".
    foldIntoTarget(event, ctx);
  } else if (event.type === 'think' || event.type === 'plan') {
    // Reasoning steps land in the timeline in execution order, before the
    // text that followed them, and the ticker of the same thought goes away.
    foldIntoTarget(event, ctx);
    // Native model thoughts also feed the Process column live.
    if (event.native) {
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
    // File change: the Artifacts panel, and the bubble's own file list.
    mergeArtifact(event);
    foldIntoTarget(event, ctx);
  } else if (event.type === 'graph_node_start' || event.type === 'graph_node_end') {
    // An imported agent walking its own graph: one step per node, in the
    // current bubble, since it is still one agent.
    setGraphRun((prev) => reduceGraphRun(prev, event));
    foldIntoTarget(event, ctx);
  } else if (event.type === 'tool_start') {
    foldIntoTarget(event, ctx);
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
  } else if (event.type === 'tool_end' || event.type === 'tool_error') {
    // A call that raised ends on tool_error instead of tool_end. The bubble
    // keeps the tool's name until the next token arrives.
    const outcome = event.type === 'tool_error'
      ? { output: `ERROR: ${event.error}`, error: true, running: false, ...toolOutcome(event) }
      : { output: event.output, running: false, ...toolOutcome(event) };
    foldIntoTarget(event, ctx);
    setProcessInsights((prev) => {
      const tools = [...(prev.tools || [])];
      for (let i = tools.length - 1; i >= 0; i -= 1) {
        if (tools[i].running) {
          tools[i] = { ...tools[i], ...outcome };
          break;
        }
      }
      const message_runs = (prev.message_runs || []).map((mr, idx) => {
        if (idx !== (prev.message_runs || []).length - 1) return mr;
        const mrTools = [...(mr.tools || [])];
        for (let i = mrTools.length - 1; i >= 0; i -= 1) {
          if (mrTools[i].running) {
            mrTools[i] = { ...mrTools[i], ...outcome };
            break;
          }
        }
        return { ...mr, tools: mrTools };
      });
      return { ...prev, tools, message_runs };
    });
  } else if (event.type === 'token') {
    foldIntoTarget(event, ctx);
    setProcessInsights((prev) => ({
      ...prev,
      message_runs: (prev.message_runs || []).map((mr, idx) =>
        idx === (prev.message_runs || []).length - 1
          ? { ...mr, output: `${mr.output || ''}${event.token || ''}` }
          : mr
      ),
    }));
  } else if (event.type === 'handoff') {
    // The agent gave the conversation to another one (chat/handoff.py): its
    // bubble closes with its own reply, the receiving agent's bubble opens
    // below it, and every later event of the turn streams there (read
    // through ctx.assistantId, which the next call picks up). Only a
    // single-agent turn hands off.
    if (isMultiAgent) return;
    const nextId = genId();
    setConversations((prev) => applyHandoff(prev, convId, assistantId, nextId, event));
    ctx.assistantId = nextId;
    if (Array.isArray(state.handoffs)) state.handoffs.push(event);
    if (event.next_run_id) {
      state.runId = event.next_run_id;
      setActiveRunId(event.next_run_id);
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
                    // The sources the reply cites as [n] (Citations.jsx).
                    citations: event.citations || m.citations || null,
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
                    // Why the turn was refused, when the guard or a limit said (RefusalCard).
                    refusal: event.refusal || null,
                    // Warn guardrails that found something; the turn went on (GuardrailWarnings).
                    guardrail_warnings: event.guardrail_warnings || null,
                  }
                : m
            ),
          },
        ),
      );
    }
    // Messages the turn ended without taking: queued, and sent as the next
    // turn once this one has closed (useChatSteering).
    // A flow or a team turn is several runs and may not stream every
    // delivered notice, so its done event also says which were delivered:
    // those must not be sent again as the next turn.
    if (Array.isArray(event.delivered) && event.delivered.length) {
      setConversations((prev) =>
        prev.map((c) => {
          if (c.id !== convId) return c;
          let messages = c.messages;
          for (const d of event.delivered) messages = markSteerDelivered(messages, d.msg_id, d.after_step);
          return { ...c, messages };
        }),
      );
    }
    if (Array.isArray(event.undelivered) && event.undelivered.length) {
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c, messages: applyUndelivered(c.messages, event.undelivered, isMultiAgent ? null : assistantId),
          },
        ),
      );
    }
    if (!isMultiAgent) {
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
  } else if (event.type === 'tool_approval' || event.type === 'tool_approval_resolved') {
    // A tool call of this turn waits for a person, or was answered: the
    // bubble shows the card (ToolApprovalCard) until the turn moves on.
    const tgt = targetMsgId(event, ctx);
    setConversations((prev) =>
      prev.map((c) => (c.id !== convId ? c : { ...c, messages: applyApprovalEvent(c.messages, tgt, event) })),
    );
  } else if (event.type === 'steer_delivered') {
    // A message sent while this turn worked reached the model (see
    // components/chat/steering.js); its bubble now says at which step.
    setConversations((prev) =>
      prev.map((c) =>
        c.id !== convId ? c : { ...c, messages: markSteerDelivered(c.messages, event.msg_id, event.after_step) },
      ),
    );
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
