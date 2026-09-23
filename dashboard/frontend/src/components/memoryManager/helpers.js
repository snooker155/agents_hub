/**
 * Constants and small pure helpers shared across the memory manager tabs:
 * date formatting, status and colour maps, and the agent-to-pool lookup.
 */
import { FileText, Clock, CheckCircle, AlertCircle } from 'lucide-react';

// The blocks every pool is seeded with (memory/models.py default_blocks). They
// are part of the prompt's shape rather than one pool's content, so the page
// offers no Delete for them.
export const DEFAULT_BLOCK_NAMES = ['persona', 'user'];

export const EPISODE_KIND_COLOR = {
  interaction: 'bg-blue-100 text-blue-700',
  task:        'bg-indigo-100 text-indigo-700',
  decision:    'bg-purple-100 text-purple-700',
  error:       'bg-red-100 text-red-700',
  observation: 'bg-gray-100 text-gray-700',
};
export const EPISODE_OUTCOME_COLOR = {
  success: 'bg-green-100 text-green-700',
  failure: 'bg-red-100 text-red-700',
  partial: 'bg-yellow-100 text-yellow-700',
  'n/a':   'bg-gray-100 text-gray-500',
};

export const fmt = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export const STATUS_CONFIG = {
  raw:        { label: 'Raw',        color: 'bg-gray-100 text-gray-600',    icon: FileText },
  processing: { label: 'Processing', color: 'bg-yellow-100 text-yellow-700', icon: Clock },
  indexed:    { label: 'Indexed',    color: 'bg-green-100 text-green-700',   icon: CheckCircle },
  failed:     { label: 'Failed',     color: 'bg-red-100 text-red-700',      icon: AlertCircle },
};

// Attached pool ids for an agent, primary first. memory_data holds a single
// pool id (legacy) or a list of ids.
export function agentPools(a) {
  if (a.memory_type !== 'shared' || !a.memory_data) return [];
  return Array.isArray(a.memory_data) ? a.memory_data.map(String) : [String(a.memory_data)];
}

// Knowledge graph node type colours (foreground, background), assigned by
// encounter order among the graph's own node types.
export const GRAPH_TYPE_PALETTE = [
  ['#3f66d8', '#eef3ff'], ['#10b981', '#ecfdf5'], ['#f59e0b', '#fffbeb'],
  ['#ef4444', '#fef2f2'], ['#3b82f6', '#eff6ff'], ['#a855f7', '#faf5ff'],
  ['#14b8a6', '#f0fdfa'], ['#ec4899', '#fdf2f8'],
];
export function colorForType(type, allTypes) {
  const idx = allTypes.indexOf(type);
  return GRAPH_TYPE_PALETTE[(idx >= 0 ? idx : 0) % GRAPH_TYPE_PALETTE.length];
}
