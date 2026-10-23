// The letter and colour each artifact operation gets in a chat's file list.
// Its own module so the components that draw it stay component-only modules,
// which is what fast refresh needs.
export const ARTIFACT_OP_META = {
  add: { label: 'A', cls: 'bg-emerald-100 text-emerald-700' },
  modify: { label: 'M', cls: 'bg-amber-100 text-amber-700' },
  delete: { label: 'D', cls: 'bg-red-100 text-red-700' },
};

// The page a changed file opens: the link the change carries (`url`, from the
// filesystem tools), else the turn's entity link for the same path. A deleted
// file has none.
export function fileLink(file, entities) {
  if (file.url) return file.url;
  if (file.op === 'delete') return null;
  const match = (entities || []).find((e) => e.kind === 'file' && (e.title === file.path || e.id === file.path
    || String(e.id || '').endsWith(`/${file.path}`)));
  return match?.url || null;
}

// The files a conversation's turns changed, by path, the latest change of
// each (with its diff), for the Artifacts panel of a chat that has no run
// insights to read them from (a flow chat). A turn's file steps carry the
// diffs (live, or from its run's trail); its file list stands in when it has
// no steps. A file added and then edited stays an "add".
export function conversationArtifacts(messages) {
  const out = {};
  for (const m of messages || []) {
    if (m?.role !== 'agent') continue;
    const steps = (m.timeline || []).filter((e) => e?.type === 'artifact' && e.path);
    for (const change of steps.length ? steps : (m.files || [])) {
      if (!change?.path) continue;
      const { type: _step, ...a } = change;
      const prev = out[a.path];
      const op = prev?.op === 'add' && a.op === 'modify' ? 'add' : a.op;
      const url = a.url || (op !== 'delete' ? prev?.url : null);
      out[a.path] = { ...a, op, ...(url ? { url } : {}) };
    }
  }
  return out;
}
