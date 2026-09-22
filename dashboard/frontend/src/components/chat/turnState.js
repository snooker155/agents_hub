/**
 * The pure helpers a streamed turn is folded together with: a local id for an
 * optimistic bubble, the capped live-thought buffer, merging one `artifact`
 * event into a message's file list, and the notice shown when the backend
 * folds older history into a summary.
 *
 * In their own module because the send path and the session stream both fold
 * the same events, and neither should own the other's copy.
 */

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// Live reasoning is a ticker showing only the tail, so the buffer never needs to
// grow past a few screens' worth; capping it keeps a long chain-of-thought from
// piling megabytes of dead text into React state.
const MAX_LIVE_THOUGHT_CHARS = 4000;

function appendLiveThought(prev, delta) {
  const text = `${prev || ''}${delta || ''}`;
  return text.length > MAX_LIVE_THOUGHT_CHARS ? text.slice(-MAX_LIVE_THOUGHT_CHARS) : text;
}

// Merge one streamed `artifact` event into a message's file list. Metadata only
// (the diff body lives in the session-scoped `artifacts` map, which is far too
// large to store per message). Last write per path wins, but a file keeps the
// position it was first touched at, so the list reads in execution order.
function mergeMessageFile(files, event) {
  const list = files || [];
  const idx = list.findIndex((f) => f.path === event.path);
  const entry = {
    op: event.op,
    path: event.path,
    additions: event.additions || 0,
    deletions: event.deletions || 0,
  };
  if (idx === -1) return [...list, entry];
  const next = [...list];
  // A file the agent created and then edited again is still an "add" overall.
  if (next[idx].op === 'add' && entry.op === 'modify') entry.op = 'add';
  next[idx] = entry;
  return next;
}

// A `compaction` stream event marks the point where the backend replaced older
// turns with a summary. Rendered as a subtle centered notice (see
// MessageBubble) rather than a chat bubble: it's a fact about the transcript,
// not something either party said.
function buildCompactionNotice(t, event) {
  return {
    id: genId(),
    role: 'system',
    kind: 'compaction',
    content: t('chat.compactionNotice', { count: event.folded || 0 }),
  };
}

export { genId, appendLiveThought, mergeMessageFile, buildCompactionNotice, MAX_LIVE_THOUGHT_CHARS };
