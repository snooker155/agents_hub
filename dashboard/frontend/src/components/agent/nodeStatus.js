// How a node's status reads: the palette behind the badge, and the clock beside it.

export const NODE_STATUS = {
  running: { dot: 'bg-green-500 animate-pulse', badge: 'bg-green-100 text-green-800', label: 'Running' },
  starting: { dot: 'bg-yellow-400 animate-pulse', badge: 'bg-yellow-100 text-yellow-800', label: 'Starting' },
  stopping: { dot: 'bg-orange-400 animate-pulse', badge: 'bg-orange-100 text-orange-800', label: 'Stopping' },
  stopped: { dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-600', label: 'Stopped' },
  failed: { dot: 'bg-red-500', badge: 'bg-red-100 text-red-700', label: 'Failed' },
  completed: { dot: 'bg-blue-400', badge: 'bg-blue-100 text-blue-700', label: 'Completed' },
};

export function fmtNodeDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function nodeUptime(startedAt, finishedAt) {
  if (!startedAt) return '—';
  const end = finishedAt ? new Date(finishedAt) : new Date();
  const secs = Math.max(0, Math.floor((end - new Date(startedAt)) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
