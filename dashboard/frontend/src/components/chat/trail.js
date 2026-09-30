/**
 * A turn's trail (`msg.timeline`): the thoughts, tool calls, intermediate text
 * and delegated runs of one reply, in the order they happened.
 *
 * The Build view draws all of it. The Chat view draws only the delegated runs
 * and, while the turn is live, what the agent is doing right now. Both read the
 * trail through these helpers so they agree on what a step is.
 */

// The tools that run another agent. The `delegation` entry the child's stream
// opens right after such a call is the call, drawn with the child's own steps
// inside, so the bare tool entry in front of it is folded into it.
const DELEGATION_TOOLS = new Set(['run_agent_tool', 'delegate_task_tool']);

// How much of a step is kept when the trail is stored with the conversation.
// Tool inputs and outputs can be whole files; the run's own log keeps them in
// full, the chat keeps enough to read what happened.
const STORED_TOOL_CHARS = 2000;
const STORED_REASONING_CHARS = 8000;
const STORED_TEXT_CHARS = 20000;

function clip(value, max) {
  if (value == null) return value;
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}… (truncated)` : text;
}

/** The trail as it is stored: long payloads clipped, nothing left "running". */
function compactTimeline(timeline) {
  if (!Array.isArray(timeline) || !timeline.length) return undefined;
  return timeline.map((e) => {
    if (!e || typeof e !== 'object') return e;
    const { running: _running, ...rest } = e;
    switch (e.type) {
      case 'tool':
        return { ...rest, input: clip(e.input, STORED_TOOL_CHARS), output: clip(e.output, STORED_TOOL_CHARS) };
      case 'reasoning':
        return { ...rest, content: clip(e.content, STORED_REASONING_CHARS) };
      case 'text':
        return { ...rest, text: clip(e.text, STORED_TEXT_CHARS) };
      case 'delegation':
        return {
          ...rest,
          input: clip(e.input, STORED_TOOL_CHARS),
          output: clip(e.output, STORED_TEXT_CHARS),
          timeline: compactTimeline(e.timeline) || [],
        };
      default:
        return rest;
    }
  });
}

/** Drop each delegation tool call that a `delegation` entry right after it stands for. */
function foldDelegationTools(timeline) {
  const list = timeline || [];
  const out = [];
  for (let i = 0; i < list.length; i += 1) {
    const e = list[i];
    if (e?.type === 'tool' && DELEGATION_TOOLS.has(e.tool) && list[i + 1]?.type === 'delegation') continue;
    out.push(e);
  }
  return out;
}

/**
 * The trail with the reply's final text at its end. Streamed tokens of the last
 * step can differ from the answer the turn settled on (the backend sends the
 * de-duplicated final response with `done`), so once the turn is over the
 * trailing text is the reply itself, appended when the trail ended on a tool.
 */
function withFinalText(timeline, content) {
  const list = timeline || [];
  const text = (content || '').trim();
  if (!text) return list;
  const last = list[list.length - 1];
  if (last?.type === 'text') return [...list.slice(0, -1), { ...last, text: content }];
  return [...list, { type: 'text', text: content }];
}

/**
 * What a live turn is doing now, for the Chat view's transient line: the
 * thought being written, the tool running, or the text being streamed. Only the
 * top level: a delegated run shows its own steps in its card.
 */
function currentActivity(msg) {
  if ((msg.thinking_live || '').trim()) return { kind: 'thinking', text: msg.thinking_live };
  const list = foldDelegationTools(msg.timeline);
  const last = list[list.length - 1];
  if (!last) return null;
  if (last.type === 'tool' && last.running) return { kind: 'tool', tool: last.tool };
  if (last.type === 'reasoning') return { kind: 'thinking', text: last.content || '' };
  if (last.type === 'text') return { kind: 'text', text: last.text || '' };
  return null;
}

export { DELEGATION_TOOLS, compactTimeline, foldDelegationTools, withFinalText, currentActivity };
