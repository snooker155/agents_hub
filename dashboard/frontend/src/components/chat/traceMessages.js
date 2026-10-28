/**
 * A build chat's stored transcript as Chat page messages.
 *
 * An entity build chat (loops, teams, agents, projects, views, memory) keeps
 * its display trace server-side as a flat list of feed items
 * (`chat/entity_chat.py trace_item_for`): `user`, `assistant`, `thinking`,
 * `tool`, `text`, `artifact`, `entity`, `error`. This folds them back into the
 * messages the live turn builds (turnEvents.js), so a reloaded chat reads the
 * same as it did while it ran. Older traces, written before tool outputs,
 * text steps and file changes were kept, simply have fewer steps.
 */
import { mergeMessageFile } from './turnState';

// The marker a stopped turn leaves in the trace: not a tool the agent called.
const STOPPED_MARKERS = new Set(['stopped by you']);

function toolEntry(item) {
  const failed = item.status === 'error';
  const output = item.output != null ? item.output : (failed && item.error ? `ERROR: ${item.error}` : null);
  return {
    type: 'tool',
    tool: item.tool,
    input: item.input ?? '',
    output,
    ...(item.status === 'running' ? {} : { status: failed ? 'error' : 'ok' }),
    ...(failed ? { error: true } : {}),
    ...(item.memory ? { memory: item.memory } : {}),
  };
}

/** `[{k, ...}]` feed items → `[{id, role, content, timeline, files, changes}]`. */
export function messagesFromTrace(trace) {
  const out = [];
  let agent = null;
  let n = 0;
  const nextId = () => `trace-${n++}`;
  const current = () => {
    if (!agent) {
      agent = { id: nextId(), role: 'agent', content: '', timeline: [], files: [], changes: [] };
      out.push(agent);
    }
    return agent;
  };
  for (const item of trace || []) {
    if (!item || typeof item !== 'object') continue;
    switch (item.k) {
      case 'user':
        agent = null;
        out.push({ id: nextId(), role: 'user', content: item.text || '' });
        break;
      case 'assistant': {
        const a = current();
        a.content = item.text || '';
        if (item.run_id) a.run_id = item.run_id;
        break;
      }
      case 'thinking':
        if ((item.text || '').trim()) {
          current().timeline.push({ type: 'reasoning', kind: 'think', step: item.step, content: item.text });
        }
        break;
      case 'tool':
        if (STOPPED_MARKERS.has(item.tool)) break;
        current().timeline.push(toolEntry(item));
        break;
      case 'text':
        if ((item.text || '').trim()) current().timeline.push({ type: 'text', text: item.text });
        break;
      case 'artifact': {
        const a = current();
        a.timeline.push({
          type: 'artifact', op: item.op, path: item.path, additions: item.additions, deletions: item.deletions,
          ...(item.url ? { url: item.url } : {}),
        });
        a.files = mergeMessageFile(a.files, item);
        break;
      }
      case 'entity': {
        const entry = { type: 'change', action: item.action || 'updated', kind: item.kind || '', label: item.label || '' };
        const a = current();
        a.timeline.push(entry);
        a.changes.push(entry);
        break;
      }
      case 'error': {
        const a = current();
        a.error = true;
        const text = item.text || 'error';
        a.content = a.content ? `${a.content}\n\n${text}` : text;
        break;
      }
      default:
        break;
    }
  }
  return out;
}

/**
 * What a build chat's load returns, as messages: the rich trace when there is
 * one, else the bare transcript.
 */
export function messagesFromChatData(data) {
  const trace = data?.trace;
  if (Array.isArray(trace) && trace.length) return messagesFromTrace(trace);
  return (data?.messages || []).map((m, i) => ({
    id: `msg-${i}`,
    role: m.role === 'user' ? 'user' : 'agent',
    content: m.content || '',
  }));
}
