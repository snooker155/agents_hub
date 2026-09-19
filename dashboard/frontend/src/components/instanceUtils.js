import { Activity, Box, Layers, MessageSquare, Server, Users } from 'lucide-react';

/*
 * Shared vocabulary for rendering agent instances.
 *
 * Kept out of the component files so the list, the detail page and the agent
 * tab all describe a copy the same way — and so each of those files exports
 * only components (which is what keeps fast refresh working).
 */

export const STATE_STYLES = {
  starting: { dot: 'bg-yellow-400 animate-pulse', badge: 'bg-yellow-100 text-yellow-800' },
  active:   { dot: 'bg-green-500 animate-pulse',  badge: 'bg-green-100 text-green-800' },
  standby:  { dot: 'bg-blue-400',                 badge: 'bg-blue-100 text-blue-700' },
  finished: { dot: 'bg-gray-400',                 badge: 'bg-gray-100 text-gray-600' },
  stopped:  { dot: 'bg-gray-400',                 badge: 'bg-gray-100 text-gray-600' },
  failed:   { dot: 'bg-red-500',                  badge: 'bg-red-100 text-red-700' },
};

export const KIND_ICONS = {
  node: Server,
  container: Box,
  task: Activity,
  chat: MessageSquare,
  flow_node: Layers,
  team_member: Users,
};

export function formatDuration(ms) {
  if (!ms) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** "4 min ago" — instance lists are read for recency, not for timestamps. */
export function relativeTime(iso, t) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Math.max(0, Date.now() - then);
  const s = Math.round(diff / 1000);
  if (s < 60) return t('instances.time.justNow');
  const m = Math.floor(s / 60);
  if (m < 60) return t('instances.time.minutes', { count: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t('instances.time.hours', { count: h });
  return t('instances.time.days', { count: Math.floor(h / 24) });
}
