/**
 * The architect chat's stored transcript as Chat page messages.
 *
 * Its trace (dashboard/backend/routes/projects/graph.py `_trace_item_for`) is
 * the generic feed plus a few graph items: `{k:'node', label}` for a node the
 * agent added, `{k:'node', label:'', edge:'a → b'}` for an edge, and tool
 * items named `removed <id>` / `removed edge <id>` for deletions. They are
 * turned into the `entity` items the shared reader understands, so a reload
 * shows the same change lines the live run did. Tool steps are stored without
 * an end event, so none is left spinning.
 */
import { messagesFromChatData } from '../chat/traceMessages';

function convert(item) {
  if (!item || typeof item !== 'object') return item;
  if (item.k === 'node') {
    return item.edge
      ? { k: 'entity', action: 'added', kind: 'edge', label: item.edge }
      : { k: 'entity', action: 'added', kind: 'node', label: item.label || '' };
  }
  if (item.k === 'tool') {
    const edge = /^removed edge (.+)$/.exec(item.tool || '');
    if (edge) return { k: 'entity', action: 'removed', kind: 'edge', label: edge[1] };
    const node = /^removed (.+)$/.exec(item.tool || '');
    if (node) return { k: 'entity', action: 'removed', kind: 'node', label: node[1] };
    return item.status === 'running' ? { ...item, status: 'done' } : item;
  }
  return item;
}

export function graphChatMessages(data) {
  const trace = data?.trace;
  if (Array.isArray(trace) && trace.length) {
    return messagesFromChatData({ ...data, trace: trace.map(convert) });
  }
  return messagesFromChatData(data);
}

/** The change a live graph event stands for, as an `entity_changed` event. */
export function graphChangeEvent(ev) {
  switch (ev.type) {
    case 'graph_node':
      return { type: 'entity_changed', action: 'added', kind: 'node', label: ev.node?.data?.label || ev.node?.id || '' };
    case 'graph_edge':
      return { type: 'entity_changed', action: 'added', kind: 'edge', label: `${ev.edge?.source} → ${ev.edge?.target}` };
    case 'graph_node_delete':
      return { type: 'entity_changed', action: 'removed', kind: 'node', label: ev.id };
    case 'graph_edge_delete':
      return { type: 'entity_changed', action: 'removed', kind: 'edge', label: ev.id };
    default:
      return null;
  }
}

/** The graph-builder tools surface as graph events; their tool events are noise. */
export function isGraphBuilderTool(tool) {
  const name = String(tool || '');
  return name.startsWith('add_graph') || name.startsWith('delete_graph') || name === 'clear_graph';
}
