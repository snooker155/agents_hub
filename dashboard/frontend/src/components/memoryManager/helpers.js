/**
 * Constants and small pure helpers shared across the memory manager tabs:
 * date formatting, status and colour maps, and the agent-to-pool lookup.
 */
import { FileText, Clock, CheckCircle, AlertCircle } from 'lucide-react';
import { cssVar } from '../../lib/themeColors';

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
// encounter order among the graph's own node types. The foreground of each
// entry reads the matching custom property fresh on every call (this is a
// plain function, not a component, so it cannot use useThemeColors — see
// src/lib/themeColors.js), so a brand or dark-mode change reaches these the
// next time a caller re-renders; the light background tint has no dedicated
// token (gen-theme.mjs only exposes an rgb triple and two text tiers per
// hue, not a "50" shade) and stays a plain constant, same as 'purple' below
// (a brand hue, remapped elsewhere, not one of the standalone chromatic ones).
function hueRgb(name, fallback) {
  return `rgb(${cssVar(`--hue-${name}-rgb`, fallback)})`;
}

export function graphTypePalette() {
  return [
    [cssVar('--brand-500', '#3f66d8'), cssVar('--brand-50', '#eef3ff')],
    [hueRgb('emerald', '16, 185, 129'), '#ecfdf5'],
    [hueRgb('amber', '245, 158, 11'), '#fffbeb'],
    [hueRgb('red', '239, 68, 68'), '#fef2f2'],
    [hueRgb('blue', '59, 130, 246'), '#eff6ff'],
    ['#a855f7', '#faf5ff'],
    [hueRgb('teal', '20, 184, 166'), '#f0fdfa'],
    [hueRgb('pink', '236, 72, 153'), '#fdf2f8'],
  ];
}
export function colorForType(type, allTypes) {
  const idx = allTypes.indexOf(type);
  const palette = graphTypePalette();
  return palette[(idx >= 0 ? idx : 0) % palette.length];
}

// A personal pool (memory/personal.py) is stored with an English name and
// description for the agent's tools; the UI shows them in the viewer's
// language. Only its owner ever sees one, so "your" is always right. The
// default workspace lists every workspace's pools, where each personal pool
// would read the same, so there `withWorkspace` names the one it belongs to.
export const isPersonalPool = (m) => m?.kind === 'personal';
export const poolName = (m, t, withWorkspace = false) => {
  if (!isPersonalPool(m)) return m?.name;
  const title = t('memoryManager.personalTitle');
  return withWorkspace ? `${title} · ${m.workspace || 'default'}` : title;
};
export const poolDescription = (m, t) => (isPersonalPool(m) ? t('memoryManager.personalHint') : m?.description);
