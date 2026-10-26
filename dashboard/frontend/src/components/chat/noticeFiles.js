/**
 * The files a background run changed (its nodes' or members' runs), which
 * the notice of the turn it woke carries (chat/background_runs.py files_of):
 * listed under the agent's reply to that notice, and in the chat's Artifacts
 * panel, with their diffs from the runs that changed them once it is open.
 */
import { useEffect, useMemo, useState } from 'react';
import { loadRunTrail } from './runTrail';

const noticeFilesOf = (m) => (m?.role === 'system' && Array.isArray(m.notice?.files) ? m.notice.files : null);

/** `messages` with each notice's files on the agent reply that follows it. */
export function withNoticeFiles(messages) {
  if (!(messages || []).some(noticeFilesOf)) return messages;
  const out = [...messages];
  for (let i = 0; i < out.length; i += 1) {
    const files = noticeFilesOf(out[i]);
    if (!files?.length) continue;
    const j = out.findIndex((m, k) => k > i && m.role !== 'system');
    if (j < 0 || out[j].role !== 'agent') continue;
    const own = out[j].files || [];
    const listed = new Set(own.map((f) => f.path));
    out[j] = { ...out[j], files: [...files.filter((f) => !listed.has(f.path)), ...own] };
  }
  return out;
}

/**
 * The notices' files by path, for the Artifacts panel: the latest change of
 * each, with its diff from the trail of the run that made it while `enabled`.
 */
export function useNoticeArtifacts(messages, enabled) {
  const files = useMemo(() => {
    const out = {};
    for (const m of messages || []) {
      for (const f of noticeFilesOf(m) || []) if (f?.path) out[f.path] = f;
    }
    return out;
  }, [messages]);
  const runIds = useMemo(
    () => [...new Set(Object.values(files).map((f) => f.run_id).filter(Boolean))].sort().join('|'),
    [files],
  );
  const [diffs, setDiffs] = useState({});
  useEffect(() => {
    if (!enabled || !runIds) return undefined;
    let live = true;
    Promise.all(runIds.split('|').map((id) => loadRunTrail(id))).then((trails) => {
      if (!live) return;
      const next = {};
      trails.forEach((trail, i) => {
        for (const f of trail?.files || []) if (f?.path) next[`${runIds.split('|')[i]}:${f.path}`] = f;
      });
      setDiffs(next);
    });
    return () => { live = false; };
  }, [enabled, runIds]);
  return useMemo(() => {
    const out = {};
    for (const [path, f] of Object.entries(files)) {
      const withDiff = diffs[`${f.run_id}:${path}`];
      out[path] = withDiff ? { ...withDiff, ...f } : f;
    }
    return out;
  }, [files, diffs]);
}
