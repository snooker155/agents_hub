import { useEffect, useState } from 'react';
import {
  Activity, BookOpen, Brain, CheckCircle, ExternalLink, FileText, Layers, Link2, Loader, Share2, StickyNote,
} from 'lucide-react';
import { getMemoryGraphStats, listMemoryEpisodes } from '../../api';
import { useI18n } from '../../i18n';
import { poolDescription, poolName } from '../memoryManager/helpers';

// ── The agent's primary pool, read only ──────────────────────────────────────
// Every kind of memory the pool holds, one tab each, the way the Memory page
// lays a pool out: blocks, slots, notes, journal, episodes, graph and indexed
// files. Editing stays on the Memory page (the Manage link).

const JOURNAL_PREFIX = 'journal:';
const EPISODES_SHOWN = 20;

const short = (v) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > 160 ? `${s.slice(0, 160)}…` : s;
};
const when = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString() : '';
};

function Empty({ t }) {
  return <p className="text-center text-sm text-gray-400 py-6">{t('agentDetails.poolContent.empty')}</p>;
}

function Blocks({ blocks, t }) {
  if (!blocks.length) return <Empty t={t} />;
  return (
    <div className="space-y-2">
      {blocks.map((b) => (
        <div key={b.name} className="border border-gray-200 rounded-lg p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold text-gray-900">{b.name}</span>
            <span className="text-[11px] text-gray-400 shrink-0">
              {t('agentDetails.poolContent.chars', { count: (b.value || '').length, limit: b.limit_chars })}
            </span>
          </div>
          {b.description && <p className="text-xs text-gray-500 mt-0.5">{b.description}</p>}
          <p className="text-xs text-gray-700 mt-1.5 whitespace-pre-wrap line-clamp-4">
            {b.value || <span className="italic text-gray-400">{t('agentDetails.poolContent.emptyBlock')}</span>}
          </p>
        </div>
      ))}
    </div>
  );
}

function Slots({ slots, t }) {
  if (!slots.length) return <Empty t={t} />;
  return (
    <div className="space-y-2">
      {slots.map(([slot, data]) => (
        <div key={slot} className="border border-gray-200 rounded-lg p-3">
          <p className="text-sm font-semibold text-gray-900 font-mono">{slot}</p>
          {data && typeof data === 'object' ? (
            <dl className="mt-1.5 grid gap-x-3 gap-y-0.5 text-xs" style={{ gridTemplateColumns: 'minmax(6rem, max-content) 1fr' }}>
              {Object.entries(data).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-gray-500 font-mono truncate">{k}</dt>
                  <dd className="text-gray-800 break-words">{short(v)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-gray-800 mt-1">{short(data)}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function Notes({ notes, t, journal = false }) {
  if (!notes.length) return <Empty t={t} />;
  return (
    <div className="space-y-2">
      {notes.map((n) => (
        <div key={n.id || n.title} className="border border-gray-200 rounded-lg p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold text-gray-900 truncate">
              {journal ? n.title.slice(JOURNAL_PREFIX.length) : n.title}
            </span>
            {n.created_at && <span className="text-[11px] text-gray-400 shrink-0">{when(n.created_at)}</span>}
          </div>
          <p className="text-xs text-gray-700 mt-1 whitespace-pre-wrap line-clamp-4">{n.content}</p>
        </div>
      ))}
    </div>
  );
}

function Episodes({ poolId, t }) {
  const [state, setState] = useState({ loading: true, episodes: [] });
  useEffect(() => {
    let alive = true;
    listMemoryEpisodes(poolId, { limit: EPISODES_SHOWN })
      .then(({ data }) => { if (alive) setState({ loading: false, episodes: data?.episodes || [] }); })
      .catch(() => { if (alive) setState({ loading: false, episodes: [] }); });
    return () => { alive = false; };
  }, [poolId]);
  if (state.loading) return <p className="text-sm text-gray-400 py-6 flex items-center justify-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}</p>;
  if (!state.episodes.length) return <Empty t={t} />;
  return (
    <div className="space-y-2">
      {state.episodes.map((e) => (
        <div key={e.id} className="border border-gray-200 rounded-lg p-3">
          <div className="flex items-center gap-2 text-[11px]">
            <span className="px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 font-medium">{e.kind}</span>
            {e.outcome && <span className="px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">{e.outcome}</span>}
            <span className="ml-auto text-gray-400">{when(e.occurred_at || e.created_at)}</span>
          </div>
          <p className="text-xs text-gray-800 mt-1.5">{e.summary}</p>
        </div>
      ))}
      {state.episodes.length >= EPISODES_SHOWN && (
        <p className="text-xs text-gray-400 text-center">{t('agentDetails.poolContent.latestEpisodes', { count: EPISODES_SHOWN })}</p>
      )}
    </div>
  );
}

function Graph({ stats, t }) {
  if (!stats) return <p className="text-sm text-gray-400 py-6 flex items-center justify-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}</p>;
  if (!stats.node_count) return <Empty t={t} />;
  const chips = (obj) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-700">
        {t('agentDetails.poolContent.graphSize', { nodes: stats.node_count, edges: stats.edge_count || 0 })}
      </p>
      {[['types', stats.types], ['relations', stats.relations]].map(([key, obj]) => chips(obj).length > 0 && (
        <div key={key}>
          <p className="text-xs font-medium text-gray-500 mb-1">{t(`agentDetails.poolContent.${key}`)}</p>
          <div className="flex flex-wrap gap-1.5">
            {chips(obj).map(([name, n]) => (
              <span key={name} className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700">{name} · {n}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Files({ files, t }) {
  if (!files.length) return <Empty t={t} />;
  return (
    <div className="divide-y divide-gray-100 border border-gray-200 rounded-lg">
      {files.map((f) => (
        <div key={`${f.workspace || ''}/${f.filename}`} className="flex items-center gap-3 px-3 py-2 text-sm">
          <FileText className="w-4 h-4 text-gray-400 shrink-0" />
          <span className="truncate flex-1 text-gray-800" title={f.filename}>{f.filename}</span>
          {f.chunks > 0 && <span className="text-xs text-gray-400 shrink-0">{t('agentDetails.chunkCount', { count: f.chunks })}</span>}
          <span className={`text-[11px] px-1.5 py-0.5 rounded-full shrink-0 ${
            f.status === 'indexed' ? 'bg-green-100 text-green-700' : f.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'
          }`}>{f.status || 'pending'}</span>
        </div>
      ))}
    </div>
  );
}

function MemoryPoolDetails({ pool, withWorkspace = false }) {
  const { t } = useI18n();
  const blocks = pool.blocks || [];
  const slots = Object.entries(pool.structured_data || {});
  const allNotes = pool.notes || [];
  const notes = allNotes.filter((n) => !n.title?.startsWith(JOURNAL_PREFIX));
  const journals = allNotes.filter((n) => n.title?.startsWith(JOURNAL_PREFIX))
    .sort((a, b) => String(b.title).localeCompare(String(a.title)));
  const files = pool.rag_files || [];

  // Graph stats come from their own route; the count shows on its tab.
  const [graphStats, setGraphStats] = useState(null);
  useEffect(() => {
    let alive = true;
    getMemoryGraphStats(pool.id)
      .then(({ data }) => { if (alive) setGraphStats(data || { node_count: 0 }); })
      .catch(() => { if (alive) setGraphStats({ node_count: 0 }); });
    return () => { alive = false; };
  }, [pool.id]);

  const tabs = [
    { id: 'blocks',     label: t('memoryManager.contentTabs.blocks'),     icon: Brain,      count: blocks.length },
    { id: 'structured', label: t('memoryManager.contentTabs.structured'), icon: Layers,     count: slots.length },
    { id: 'notes',      label: t('memoryManager.contentTabs.notes'),      icon: StickyNote, count: notes.length },
    { id: 'journals',   label: t('memoryManager.contentTabs.journals'),   icon: BookOpen,   count: journals.length },
    { id: 'episodes',   label: t('memoryManager.contentTabs.episodes'),   icon: Activity,   count: null },
    { id: 'graph',      label: t('memoryManager.contentTabs.graph'),      icon: Share2,     count: graphStats ? graphStats.node_count || 0 : null },
    { id: 'files',      label: t('agentDetails.poolContent.files'),       icon: FileText,   count: files.length },
  ];
  // Opens on the first kind that holds something.
  const [tab, setTab] = useState(() => (tabs.find((x) => x.count) || tabs[0]).id);

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Link2 className="w-4 h-4 text-indigo-500 shrink-0" />
            <h3 className="font-bold text-gray-900">{poolName(pool, t, withWorkspace)}</h3>
            <span className="inline-flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">
              <CheckCircle className="w-3 h-3" /> {t('agentDetails.connected')}
            </span>
          </div>
          {poolDescription(pool, t) && <p className="text-sm text-gray-500 mt-1 ml-6">{poolDescription(pool, t)}</p>}
        </div>
        <a href="/memory" className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50 whitespace-nowrap flex items-center gap-1">
          <ExternalLink className="w-3 h-3" /> {t('agentDetails.manage')}
        </a>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-gray-200" role="tablist">
        {tabs.map(({ id, label, icon: Icon, count }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === id ? 'border-indigo-500 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <Icon className="w-4 h-4" /> {label}
            {count !== null && (
              <span className={`text-xs px-1.5 py-0.5 rounded-full ${tab === id ? 'bg-indigo-100 text-indigo-600' : 'bg-gray-100 text-gray-500'}`}>{count}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'blocks' && <Blocks blocks={blocks} t={t} />}
      {tab === 'structured' && <Slots slots={slots} t={t} />}
      {tab === 'notes' && <Notes notes={notes} t={t} />}
      {tab === 'journals' && <Notes notes={journals} t={t} journal />}
      {tab === 'episodes' && <Episodes poolId={pool.id} t={t} />}
      {tab === 'graph' && <Graph stats={graphStats} t={t} />}
      {tab === 'files' && <Files files={files} t={t} />}
    </div>
  );
}

export { MemoryPoolDetails };
