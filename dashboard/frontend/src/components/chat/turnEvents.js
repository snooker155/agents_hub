/**
 * One streamed event folded into the agent message it belongs to.
 *
 * The main chat, a flow's chat, an entity build chat (loops, teams, agents,
 * projects), an instance's live turn and a flow node's log block all read the
 * same agent stream. Each of them used to keep its own idea of what an event
 * means, so the same turn read differently on every page. This is the one
 * reading: it takes a message (`{content, timeline, files, thinking_live,
 * running_tool, ...}`) and an event and returns the message after it, without
 * touching anything else. The renderers (MessageBubble for the Chat view,
 * BuildMessage for the Build view) draw what it builds.
 *
 * Pure and immutable: an event that does not concern the message returns the
 * same object, so a caller can tell nothing changed.
 */
import { appendIntoDelegation, appendUnderDelegation, mapDelegation, resolveDelegationTool } from './delegationTimeline';
import { appendLiveThought, mergeMessageFile } from './turnState';
import { toolOutcome } from '../toolStatus';

/** An empty agent message, ready to be folded into. */
export function agentMessage(fields = {}) {
  return { role: 'agent', content: '', timeline: [], ...fields };
}

// A delegated run's events carry `delegation` and the child's run id; they go
// inside its card, not on the top level of the turn.
function applyDelegationEvent(msg, event) {
  let tl = msg.timeline || [];
  if (event.type === 'delegation_start') {
    tl = appendUnderDelegation(tl, event.parent_run_id, msg.run_id || null, {
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
      output: event.output || '',
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
    tl = resolveDelegationTool(tl, event.run_id, { output: event.output, ...toolOutcome(event) });
  } else if (event.type === 'tool_error') {
    tl = resolveDelegationTool(tl, event.run_id, { output: `ERROR: ${event.error}`, error: true });
  } else if (event.type === 'text' && (event.content || '').trim()) {
    tl = appendIntoDelegation(tl, event.run_id, { type: 'text', text: event.content });
  }
  return tl === msg.timeline ? msg : { ...msg, timeline: tl };
}

/** Whether `event` belongs to a delegated run rather than to the turn itself. */
export function isDelegationEvent(event) {
  return event?.type === 'delegation_start' || event?.type === 'delegation_end' || Boolean(event?.delegation);
}

// The last still running entry of `type` (a tool, a graph node), resolved.
function resolveLast(timeline, match, patch) {
  const tl = [...(timeline || [])];
  for (let i = tl.length - 1; i >= 0; i -= 1) {
    if (match(tl[i])) {
      tl[i] = { ...tl[i], ...patch };
      return tl;
    }
  }
  return timeline;
}

/**
 * The message after `event`. Handles what every agent stream sends: reasoning
 * (live and finished), tool calls, streamed text, file changes, an imported
 * agent's graph nodes and delegated runs. Events about the turn as a whole
 * (meta, done, handoff, approvals, steering) stay with the caller, which knows
 * what a turn is on its page.
 */
export function applyTurnEvent(msg, event) {
  if (!msg || !event?.type) return msg;
  if (isDelegationEvent(event)) return applyDelegationEvent(msg, event);
  switch (event.type) {
    case 'think_delta':
      // Reasoning still being written: a ticker until the finished thought lands.
      return { ...msg, thinking_live: appendLiveThought(msg.thinking_live, event.delta) };
    case 'think':
    case 'plan': {
      if (!(event.content || '').trim()) return { ...msg, thinking_live: '' };
      const step = { kind: event.type, step: event.step, content: event.content };
      return {
        ...msg,
        reasoning: [...(msg.reasoning || []), step],
        timeline: [...(msg.timeline || []), { type: 'reasoning', ...step }],
        thinking_live: '',
      };
    }
    case 'artifact':
      return {
        ...msg,
        timeline: [...(msg.timeline || []), {
          type: 'artifact', op: event.op, path: event.path,
          additions: event.additions, deletions: event.deletions,
          ...(event.url ? { url: event.url } : {}),
          // The Build view unfolds it under the file's chip.
          ...(event.diff ? { diff: event.diff } : {}),
        }],
        files: mergeMessageFile(msg.files, event),
      };
    case 'graph_node_start':
      return {
        ...msg,
        timeline: [
          ...(msg.timeline || []),
          { type: 'graph_node', node: event.node, depth: event.depth || 0, running: true, ok: null },
        ],
      };
    case 'graph_node_end': {
      // The last running pass of this node: a graph with a loop enters the
      // same node more than once.
      const tl = resolveLast(
        msg.timeline,
        (e) => e.type === 'graph_node' && e.running && (!event.node || e.node === event.node),
        { running: false, ok: event.ok !== false, error: event.error || '', next: event.next || null },
      );
      return tl === msg.timeline ? msg : { ...msg, timeline: tl };
    }
    case 'tool_start':
      return {
        ...msg,
        running_tool: event.tool,
        timeline: [
          ...(msg.timeline || []),
          { type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true },
        ],
      };
    case 'tool_end':
    case 'tool_error': {
      const outcome = event.type === 'tool_error'
        ? { output: `ERROR: ${event.error}`, error: true, running: false, ...toolOutcome(event) }
        : { output: event.output, running: false, ...toolOutcome(event), ...(event.memory ? { memory: event.memory } : {}) };
      const tl = resolveLast(msg.timeline, (e) => e.type === 'tool' && e.running, outcome);
      return tl === msg.timeline ? msg : { ...msg, timeline: tl };
    }
    case 'token': {
      // Contiguous tokens make one text step, so the Build view shows prose,
      // not one entry per token.
      const tok = event.token || '';
      const tl = [...(msg.timeline || [])];
      const last = tl[tl.length - 1];
      if (last && last.type === 'text') tl[tl.length - 1] = { ...last, text: `${last.text || ''}${tok}` };
      else tl.push({ type: 'text', text: tok });
      return { ...msg, content: `${msg.content || ''}${tok}`, running_tool: null, timeline: tl };
    }
    case 'entity_changed': {
      // A build chat's turn changed the thing the chat is pinned to.
      const entry = {
        type: 'change', action: event.action || 'updated', kind: event.kind || '', label: event.label || '',
      };
      return {
        ...msg,
        timeline: [...(msg.timeline || []), entry],
        changes: [...(msg.changes || []), entry],
      };
    }
    default:
      return msg;
  }
}

/**
 * A finished turn: nothing left running, the ticker gone, and the reply the
 * turn settled on (when the server sent one) as its text.
 */
export function settleTurn(msg, { content, error } = {}) {
  if (!msg) return msg;
  const timeline = (msg.timeline || []).map((e) => (e?.running ? { ...e, running: false } : e));
  return {
    ...msg,
    timeline,
    thinking_live: '',
    running_tool: null,
    ...(content != null && String(content).trim() ? { content } : {}),
    ...(error ? { error: true } : {}),
  };
}
