import { useState, useEffect, useMemo } from 'react';
import { Boxes, Search, Bot, Cog, GitBranch, Shuffle, Package } from 'lucide-react';
import { listFlowEntities } from '../api';
import { useWorkspace } from '../components/workspace';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
// Icon + accent per category. Unknown categories fall back to a generic box.
const CATEGORY_META = {
  agent:     { labelKey: 'registry.categories.agent',     icon: Bot,      accent: 'text-cyan-600' },
  processor: { labelKey: 'registry.categories.processor', icon: Cog,      accent: 'text-violet-600' },
  condition: { labelKey: 'registry.categories.condition', icon: GitBranch, accent: 'text-amber-600' },
  transform: { labelKey: 'registry.categories.transform', icon: Shuffle,  accent: 'text-emerald-600' },
};

const metaFor = (category, t) =>
  (CATEGORY_META[category] && { ...CATEGORY_META[category], label: t(CATEGORY_META[category].labelKey) }) || {
    label: category.charAt(0).toUpperCase() + category.slice(1),
    icon: Package,
    accent: 'text-slate-500',
  };

// Build the payload the flow canvas drop handler expects
// (application/agent-flow → {id, name, description, domain}).
const dragPayload = (entity) => ({
  id: entity.id,
  name: entity.name,
  description: entity.description || '',
  domain: entity.group || entity.category,
  category: entity.category,
});

function EntityCard({ entity }) {
  const handleDragStart = (event) => {
    event.dataTransfer.setData('application/agent-flow', JSON.stringify(dragPayload(entity)));
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <button
      draggable
      onDragStart={handleDragStart}
      type="button"
      title={entity.description}
      className="block w-full cursor-grab rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5 text-left transition hover:border-cyan-300 hover:bg-cyan-50 active:cursor-grabbing"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-sm font-semibold text-slate-900">{entity.name}</div>
        {entity.group ? (
          <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-600">
            {entity.group}
          </span>
        ) : null}
      </div>
      {entity.description ? (
        <div className="mt-0.5 line-clamp-2 text-[11px] text-slate-400">{entity.description}</div>
      ) : null}
      {(entity.inputs?.length || entity.outputs?.length) ? (
        <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-400">
          {entity.inputs?.length ? <span>in: {entity.inputs.join(', ')}</span> : null}
          {entity.outputs?.length ? <span>· out: {entity.outputs.join(', ')}</span> : null}
        </div>
      ) : null}
    </button>
  );
}

export default function Registry() {
  const { t } = useI18n();
  const [groups, setGroups] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const { selectedWorkspace } = useWorkspace();

  useEffect(() => {
    let active = true;
    setLoading(true);
    (async () => {
      try {
        // Scope the palette to the active workspace so only its agents appear
        // (same ownership/allowlist rules as the agents list).
        const resp = await listFlowEntities(undefined, selectedWorkspace || null);
        if (active) setGroups(resp.data || {});
      } catch (e) {
        if (active) setError(e?.response?.data?.detail || e.message || t('registry.loadFailed'));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [selectedWorkspace, t]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = {};
    for (const [cat, entities] of Object.entries(groups)) {
      const matched = q
        ? entities.filter(
            (e) =>
              e.name.toLowerCase().includes(q) ||
              (e.description || '').toLowerCase().includes(q) ||
              (e.group || '').toLowerCase().includes(q),
          )
        : entities;
      if (matched.length) out[cat] = matched;
    }
    return out;
  }, [groups, search]);

  const total = Object.values(groups).reduce((n, e) => n + e.length, 0);
  // Stable category order: known categories first, then the rest alphabetically.
  const orderedCats = useMemo(() => {
    const known = Object.keys(CATEGORY_META);
    const cats = Object.keys(filtered);
    return [
      ...known.filter((c) => cats.includes(c)),
      ...cats.filter((c) => !known.includes(c)).sort(),
    ];
  }, [filtered]);

  return (
    <PageContainer>
      <PageHeader
        icon={Boxes}
        title={t('registry.registry')}
        description={t('registry.description', { count: total })}
        actions={
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('registry.searchEntities')}
              className="w-64 rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-cyan-300"
            />
          </div>
        }
      />

      {loading ? (
        <div className="text-sm text-slate-500">{t('registry.loadingRegistry')}</div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>
      ) : orderedCats.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center text-sm text-slate-500">
          {t('registry.noEntitiesMatchYourSearch')}
        </div>
      ) : (
        <div className="space-y-6">
          {orderedCats.map((cat) => {
            const meta = metaFor(cat, t);
            const Icon = meta.icon;
            return (
              <section key={cat}>
                <div className="mb-2 flex items-center gap-2">
                  <Icon className={`h-4 w-4 ${meta.accent}`} />
                  <h2 className="text-sm font-bold text-slate-900">{meta.label}</h2>
                  <span className="text-[11px] text-slate-400">({filtered[cat].length})</span>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {filtered[cat].map((entity) => (
                    <EntityCard key={entity.id} entity={entity} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}
