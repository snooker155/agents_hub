/**
 * Catalog page: the linked hub as a library (hublink/catalog.py).
 *
 * One tab per kind of thing the hub offers, a search, and cards. A card opens a
 * drawer with the definition and "Install into a workspace", which shows the
 * install plan first: what will be installed (dependencies come first), what
 * is already here, and an id to choose where one is taken. The Updates tab
 * lists what was installed from the hub, says which have a newer version and
 * shows the diff before the Update button replaces anything.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, BookOpen, CheckCircle, Download, RefreshCw, X } from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { isAdmin, isMultiUser, useAuth } from '../components/auth';
import { useI18n } from '../i18n';
import { getWorkspaces } from '../api/workspaces';
import { relativeTime } from '../api/hubSync';
import {
  CATALOG_KINDS, browseCatalog, catalogError, getCatalogDiff, getCatalogItem, getCatalogSettings,
  getCatalogUpdates, installCatalogItem, planCatalogInstall, updateCatalogItem,
} from '../api/catalog';

const btnPrimary = 'flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';
const btnSecondary = 'flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50';
const field = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none';

// The kinds the linked hub's catalog keeps to administrators
// (hublink/catalog.py ADMIN_KINDS): anyone else gets 403 on them, so their
// tabs are not offered.
const ADMIN_KINDS = ['teams', 'loops'];

// A 403 carries the server's reason (admin-only kind, a workspace the viewer
// cannot write to), which says more than a generic "could not load"; the plan
// lookup in catalogError would skip it when the detail is an object.
function failText(e, fallback, t) {
  if (e?.response?.status === 403) {
    const d = e.response.data?.detail;
    if (typeof d === 'string' && d) return d;
    if (d && typeof d === 'object' && d.message) return d.message;
    return t('catalog.forbidden');
  }
  return catalogError(e, fallback);
}

const ACTION_TONE = {
  install: 'bg-indigo-50 text-indigo-700', update: 'bg-indigo-50 text-indigo-700',
  rename: 'bg-amber-50 text-amber-700', exists: 'bg-gray-100 text-gray-600',
  system: 'bg-gray-100 text-gray-600', conflict: 'bg-amber-50 text-amber-700',
  missing: 'bg-red-50 text-red-700',
};

function metaLine(card, t) {
  const m = card.meta || {};
  if (card.kind === 'teams' && m.members_count != null) return t('catalog.card.members', { count: m.members_count });
  if (card.kind === 'flows' && m.nodes_count != null) return t('catalog.card.nodes', { count: m.nodes_count });
  if (card.kind === 'skills' && m.steps_count) return t('catalog.card.steps', { count: m.steps_count });
  return '';
}

function ItemCard({ card, onOpen }) {
  const { t, language } = useI18n();
  const extra = metaLine(card, t);
  return (
    <button type="button" onClick={() => onOpen(card)} data-testid="catalog-card-item"
      className="text-left bg-white rounded-xl border border-gray-100 shadow-sm p-4 hover:border-indigo-200 space-y-1.5 w-full">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-gray-900 break-words min-w-0">{card.name}</span>
        {card.installed_as?.length > 0 && (
          <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-green-50 text-green-700">{t('catalog.card.installed')}</span>
        )}
      </div>
      {card.description && <p className="text-xs text-gray-600 line-clamp-3">{card.description}</p>}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-400">
        {card.author && <span>{t('catalog.card.by', { author: card.author })}</span>}
        {card.updated_at && <span>{t('catalog.card.updated', { when: relativeTime(card.updated_at, language) })}</span>}
        {extra && <span>{extra}</span>}
      </div>
    </button>
  );
}

function PlanList({ plan, newId, setNewId, onRecheck }) {
  const { t } = useI18n();
  const conflict = plan.conflicts?.find((s) => s.root);
  return (
    <div className="space-y-2" data-testid="catalog-plan">
      <ul className="space-y-1.5">
        {plan.steps.map((s) => (
          <li key={`${s.kind}:${s.remote_id}`} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm" data-testid="catalog-plan-step">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${ACTION_TONE[s.action] || ACTION_TONE.exists}`}>{t(`catalog.action.${s.action}`)}</span>
              <span className="text-gray-800 font-medium break-words">{s.name}</span>
              <span className="text-[11px] text-gray-400">{t(`catalog.tabs.${s.kind}`)}</span>
              {s.local_id && s.local_id !== s.remote_id && <span className="font-mono text-[11px] text-gray-500 break-all">{s.local_id}</span>}
            </div>
            {s.reason && <p className="text-[11px] text-gray-500 mt-1">{s.reason}</p>}
            {s.tools_dropped?.length > 0 && (
              <p className="text-[11px] text-amber-700 mt-1">{t('catalog.install.toolsDropped', { tools: s.tools_dropped.join(', ') })}</p>
            )}
          </li>
        ))}
      </ul>
      {conflict && (
        <div className="space-y-1">
          <label className="block text-xs font-medium text-gray-600" htmlFor="catalog-new-id">{t('catalog.install.newId')}</label>
          <div className="flex gap-2">
            <input id="catalog-new-id" className={field} value={newId} onChange={(e) => setNewId(e.target.value)} data-testid="catalog-new-id" />
            <button type="button" className={btnSecondary} onClick={onRecheck} data-testid="catalog-recheck">{t('catalog.install.check')}</button>
          </div>
          <p className="text-[11px] text-gray-400">{t('catalog.install.newIdHint')}</p>
        </div>
      )}
    </div>
  );
}

function Drawer({ card, onClose, onInstalled }) {
  const { t } = useI18n();
  const [item, setItem] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [workspace, setWorkspace] = useState('default');
  const [plan, setPlan] = useState(null);
  const [newId, setNewId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    let live = true;
    getCatalogItem(card.kind, card.id).then((r) => { if (live) setItem(r.data); })
      .catch((e) => { if (live) setError(failText(e, t('catalog.detail.loadFailed'), t)); });
    getWorkspaces().then((r) => {
      if (!live) return;
      const names = (r.data || []).map((w) => w.name);
      setWorkspaces(names);
      if (names.length && !names.includes('default')) setWorkspace(names[0]);
    }).catch(() => {});
    return () => { live = false; };
  }, [card.kind, card.id, t]);

  const check = useCallback(async (ws, id) => {
    setBusy(true); setError('');
    try {
      const { data } = await planCatalogInstall(card.kind, card.id, ws, id);
      setPlan(data);
      const c = data.conflicts?.find((s) => s.root);
      if (c && !id) setNewId(c.suggested_id || '');
    } catch (e) {
      setPlan(null);
      setError(failText(e, t('catalog.install.failed'), t));
    } finally {
      setBusy(false);
    }
  }, [card.kind, card.id, t]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- the plan is fetched when the workspace is chosen
  useEffect(() => { setDone(false); setNewId(''); check(workspace, ''); }, [workspace, check]);

  const install = async () => {
    setBusy(true); setError('');
    try {
      await installCatalogItem(card.kind, card.id, workspace, newId.trim() || null);
      setDone(true);
      onInstalled();
    } catch (e) {
      setError(failText(e, t('catalog.install.failed'), t));
    } finally {
      setBusy(false);
    }
  };

  const hasConflict = !!plan?.conflicts?.length;
  const ready = plan && (plan.can_install || (hasConflict && newId.trim() && !plan.blocked?.length));
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" role="dialog" aria-modal="true" data-testid="catalog-drawer">
      <div className="bg-white w-full sm:max-w-xl h-full overflow-y-auto p-4 sm:p-5 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-gray-900 break-words">{card.name}</h2>
            <p className="text-xs text-gray-500">{t(`catalog.tabs.${card.kind}`)}{card.author ? ` · ${t('catalog.card.by', { author: card.author })}` : ''}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t('catalog.detail.close')} className="p-1 rounded hover:bg-gray-100"><X className="w-4 h-4" /></button>
        </div>
        {card.description && <p className="text-sm text-gray-700">{card.description}</p>}

        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('catalog.install.title')}</h3>
          <label className="block text-xs font-medium text-gray-600" htmlFor="catalog-workspace">{t('catalog.install.workspace')}</label>
          <select id="catalog-workspace" className={field} value={workspace} onChange={(e) => setWorkspace(e.target.value)} data-testid="catalog-workspace">
            {(workspaces.length ? workspaces : ['default']).map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
          {busy && !plan && <p className="text-xs text-gray-400 flex items-center gap-1.5"><RefreshCw className="w-3 h-3 animate-spin" />{t('catalog.install.checking')}</p>}
          {plan && <PlanList plan={plan} newId={newId} setNewId={setNewId} onRecheck={() => check(workspace, newId.trim())} />}
          {plan?.blocked?.length > 0 && <p className="text-sm text-red-600">{t('catalog.install.blocked')}</p>}
          {error && <p className="flex items-center gap-1.5 text-sm text-red-600" data-testid="catalog-error"><AlertCircle className="w-4 h-4 shrink-0" />{error}</p>}
          {done && <p className="flex items-center gap-1.5 text-sm text-green-700" data-testid="catalog-done"><CheckCircle className="w-4 h-4 shrink-0" />{t('catalog.install.done')}</p>}
          <button type="button" className={btnPrimary} disabled={busy || !ready || done} onClick={install} data-testid="catalog-install">
            <Download className="w-4 h-4" />{busy ? t('catalog.install.installing') : t('catalog.install.button')}
          </button>
        </section>

        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('catalog.detail.definition')}</h3>
          {item
            ? <pre className="max-h-96 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3 text-[11px] text-gray-700 whitespace-pre-wrap break-words" data-testid="catalog-definition">{item.text}</pre>
            : <p className="text-xs text-gray-400">{t('catalog.loading')}</p>}
        </section>
      </div>
    </div>
  );
}

function DiffView({ text }) {
  const { t } = useI18n();
  if (!text) return <p className="text-xs text-gray-500">{t('catalog.updates.noChanges')}</p>;
  return (
    <pre className="max-h-80 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-2 text-[11px] whitespace-pre-wrap break-words" data-testid="catalog-diff">
      {text.split('\n').map((line, i) => {
        const tone = line.startsWith('+') && !line.startsWith('+++') ? 'text-green-700'
          : line.startsWith('-') && !line.startsWith('---') ? 'text-red-700'
            : line.startsWith('@@') ? 'text-indigo-600' : 'text-gray-600';
        return <div key={i} className={tone}>{line || ' '}</div>;
      })}
    </pre>
  );
}

function UpdateRow({ row, onChanged }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [diff, setDiff] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true); setError('');
    try {
      const { data } = await getCatalogDiff(row.kind, row.local_id);
      setDiff(data.diff);
    } catch (e) {
      setError(failText(e, t('catalog.updates.failed'), t));
    }
  };
  const apply = async () => {
    setBusy(true); setError('');
    try {
      await updateCatalogItem(row.kind, row.local_id);
      setNotice(t('catalog.updates.updated'));
      setOpen(false); setDiff(null);
      onChanged();
    } catch (e) {
      setError(failText(e, t('catalog.updates.failed'), t));
    } finally {
      setBusy(false);
    }
  };

  const status = row.error || (row.remote_missing ? t('catalog.updates.remoteGone')
    : row.local_missing ? t('catalog.updates.localGone')
      : row.update_available ? t('catalog.updates.available') : t('catalog.updates.current'));
  return (
    <li className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-2" data-testid="catalog-update-row">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-semibold text-gray-900 break-words">{row.name}</span>
        <span className="text-[11px] text-gray-400">{t(`catalog.tabs.${row.kind}`)}</span>
        {row.workspace && <span className="text-[11px] text-gray-400">{t('catalog.updates.installedIn', { workspace: row.workspace })}</span>}
        <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${row.update_available ? 'bg-indigo-50 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>{status}</span>
        {row.update_available && (
          <span className="ml-auto flex gap-2">
            <button type="button" className={btnSecondary} onClick={toggle} data-testid="catalog-show-diff">{open ? t('catalog.updates.hideChanges') : t('catalog.updates.showChanges')}</button>
            <button type="button" className={btnPrimary} disabled={busy || row.local_missing} onClick={apply} data-testid="catalog-update">
              {busy ? t('catalog.updates.updating') : t('catalog.updates.update')}
            </button>
          </span>
        )}
      </div>
      {row.update_available && row.locally_modified && <p className="text-[11px] text-amber-700">{t('catalog.updates.edited')}</p>}
      {open && diff !== null && <DiffView text={diff} />}
      {error && <p className="text-xs text-red-600">{error}</p>}
      {notice && <p className="text-xs text-green-700">{notice}</p>}
    </li>
  );
}

export default function Catalog() {
  const { t } = useI18n();
  const auth = useAuth();
  // Outside multi there is one operator and the server treats them as admin.
  const admin = !isMultiUser(auth) || isAdmin(auth);
  const kinds = admin ? CATALOG_KINDS : CATALOG_KINDS.filter((k) => !ADMIN_KINDS.includes(k));
  const [gate, setGate] = useState(null); // { linked, enabled }
  const [tab, setTab] = useState('agents');
  // An admin-only tab chosen before the role was known falls back to the first one.
  const current = admin || !ADMIN_KINDS.includes(tab) ? tab : 'agents';
  const [q, setQ] = useState('');
  const [items, setItems] = useState(null);
  const [updates, setUpdates] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(null);
  const seq = useRef(0);

  useEffect(() => {
    getCatalogSettings().then((r) => setGate(r.data)).catch(() => setGate({ linked: false, enabled: false }));
  }, []);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    setError('');
    try {
      if (current === 'updates') {
        const { data } = await getCatalogUpdates();
        if (mine === seq.current) setUpdates(data.items || []);
      } else {
        const { data } = await browseCatalog(current, q.trim());
        if (mine === seq.current) setItems(data.items || []);
      }
    } catch (e) {
      if (mine === seq.current) setError(failText(e, current === 'updates' ? t('catalog.updates.loadFailed') : t('catalog.loadFailed'), t));
    }
  }, [current, q, t]);

  const active = gate?.linked && gate?.enabled;
  useEffect(() => {
    if (!active) return undefined;
    const timer = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(timer);
  }, [active, load, q]);

  const switchTab = (next) => { setTab(next); setItems(null); setUpdates(null); };

  return (
    <PageContainer>
      <PageHeader icon={BookOpen} title={t('catalog.title')} description={t('catalog.description')} />
      {gate && !active && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 space-y-3" data-testid="catalog-gate">
          <p className="text-sm text-gray-700">{gate.linked ? t('catalog.off') : t('catalog.notLinked')}</p>
          <Link to="/settings/hubLink" className={`${btnSecondary} inline-flex`}>{t('catalog.openSettings')}</Link>
        </div>
      )}
      {active && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-1 border-b border-gray-200" role="tablist">
            {[...kinds, 'updates'].map((k) => (
              <button key={k} type="button" role="tab" aria-selected={current === k} onClick={() => switchTab(k)} data-testid={`catalog-tab-${k}`}
                className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ${current === k ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                {t(`catalog.tabs.${k}`)}
              </button>
            ))}
          </div>
          {current !== 'updates' && (
            <input className={field} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('catalog.search')} aria-label={t('catalog.search')} data-testid="catalog-search" />
          )}
          {error && <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4 shrink-0" />{error}</p>}
          {current !== 'updates' && items === null && !error && <p className="text-sm text-gray-400">{t('catalog.loading')}</p>}
          {current !== 'updates' && items && (items.length === 0
            ? <p className="text-sm text-gray-500">{q ? t('catalog.noMatch') : t('catalog.empty')}</p>
            : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {items.map((c) => <ItemCard key={`${c.kind}:${c.id}`} card={c} onOpen={setOpen} />)}
              </div>
            ))}
          {current === 'updates' && updates && (updates.length === 0
            ? <p className="text-sm text-gray-500">{t('catalog.updates.empty')}</p>
            : <ul className="space-y-3">{updates.map((u) => <UpdateRow key={`${u.kind}:${u.local_id}`} row={u} onChanged={load} />)}</ul>)}
        </div>
      )}
      {open && <Drawer card={open} onClose={() => setOpen(null)} onInstalled={load} />}
    </PageContainer>
  );
}
