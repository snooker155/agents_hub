/**
 * Which views a reply, or a whole conversation, made or changed: read off the
 * entities each turn reports (delegated runs included) and the reply's own
 * `view_ref`. The bubble previews them, the Build panel lists them.
 */

// The views a turn made or changed, delegated runs included: the reply's own
// view (a `view_ref` response) is drawn by the bubble already, so it is left out.
function messageViews(msg) {
  const own = msg?.response_obj?.kind === 'view_ref' ? msg.response_obj.view_id : null;
  const seen = new Set(own ? [own] : []);
  const out = [];
  for (const e of msg?.entities || []) {
    if (e?.kind !== 'view' || !e.id || seen.has(e.id)) continue;
    seen.add(e.id);
    out.push({ view_id: e.id, title: e.title });
  }
  return out;
}

// The views the conversation's turns made or changed, in the order they first
// appeared: the reply's own view (view_ref) and every view among the entities a
// turn touched, delegated runs included. Newest last, like the transcript.
function conversationViews(messages) {
  const seen = new Map();
  for (const m of messages || []) {
    if (m?.role !== 'agent') continue;
    const ref = m.response_obj?.kind === 'view_ref' ? m.response_obj : null;
    if (ref?.view_id && !seen.has(ref.view_id)) {
      seen.set(ref.view_id, { view_id: ref.view_id, title: ref.title, view_kind: ref.view_kind, summary: ref.summary });
    }
    for (const e of m.entities || []) {
      if (e?.kind === 'view' && e.id && !seen.has(e.id)) seen.set(e.id, { view_id: e.id, title: e.title });
    }
  }
  return [...seen.values()];
}

export { messageViews, conversationViews };
