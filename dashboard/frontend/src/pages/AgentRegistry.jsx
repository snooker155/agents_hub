import { useCallback, useEffect, useState } from 'react';
import {
  BadgeCheck, Bot, BookOpen, Check, Filter, GitBranch, Plug, RefreshCw, Search, Server,
  ShieldCheck, X,
} from 'lucide-react';

import {
  approveAgent,
  approveFlow,
  approveMcpCatalogEntry,
  approveSkill,
  blockMcpCatalogEntry,
  getRegistry,
  rejectAgent,
  rejectFlow,
  rejectSkill,
  requestMcpCatalogEntry,
  submitAgentForReview,
  submitFlowForReview,
  submitSkillForReview,
  updateRegistrySettings,
} from '../api/registry';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { isAdmin, isMultiUser, useAuth } from '../components/auth';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

/**
 * Agents and MCP servers across every workspace, with who made them and
 * whether they are cleared to be seen by anyone else. Two catalogs, one page:
 * an agent's review status lives on its own record (agents/registry.py), the
 * MCP allowlist is its own table (mcp_client/catalog.py); both are read from
 * GET /api/registry in one call and mutated through routes/registry.py.
 */

const STATUS_STYLE = {
  draft: 'text-gray-500 bg-gray-100',
  requested: 'text-gray-500 bg-gray-100',
  in_review: 'text-amber-700 bg-amber-50',
  approved: 'text-emerald-700 bg-emerald-50',
  rejected: 'text-red-700 bg-red-50',
  blocked: 'text-red-700 bg-red-50',
};

function StatusBadge({ status, t }) {
  const cls = STATUS_STYLE[status] || 'text-gray-500 bg-gray-100';
  return (
    <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full ${cls}`}>
      {t(`agentRegistry.status.${status}`, { defaultValue: status })}
    </span>
  );
}

function Toggle({ label, hint, checked, onChange, disabled }) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? 'opacity-50' : ''}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1"
      />
      <span>
        <span className="block text-sm font-semibold text-gray-800">{label}</span>
        <span className="block text-xs text-gray-500">{hint}</span>
      </span>
    </label>
  );
}

function NoteButton({ label, tone, onConfirm, icon: Icon }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className={`inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded-lg ${tone}`}
      >
        {Icon && <Icon className="w-3.5 h-3.5" />}
        {label}
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <input
        autoFocus
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={t('agentRegistry.noteOptional')}
        className="border border-gray-300 rounded-md px-2 py-1 text-xs w-40"
      />
      <button
        onClick={() => { onConfirm(note); setOpen(false); setNote(''); }}
        className="px-2 py-1 text-xs font-bold text-white bg-indigo-600 rounded-md"
      >
        {t('agentRegistry.confirm')}
      </button>
      <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-600">
        <X className="w-3.5 h-3.5" />
      </button>
    </span>
  );
}

/**
 * One review row, shared by agents, flows and skills (they carry the same
 * shape: id, name, owner_user, review_status, shared, workspaces, and an
 * agent alone also carries `system`). Submit shows for the owner or an admin
 * on a draft/rejected item; approve/reject show for an admin on one waiting
 * in_review.
 */
function ReviewableRow({ item, icon: Icon, admin, currentUserId, onSubmit, onApprove, onReject, t }) {
  const mine = currentUserId && item.owner_user === currentUserId;
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 py-3 flex items-center gap-4">
      <Icon className="w-4 h-4 text-indigo-400 flex-shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-gray-800 truncate">
          {item.name}
          {item.system && (
            <span className="ml-2 text-[10px] uppercase tracking-wider font-bold text-indigo-600">
              {t('agentRegistry.system')}
            </span>
          )}
        </span>
        <span className="block text-[11px] text-gray-400 truncate">
          <code>{item.id}</code>
          {' · '}
          {t('agentRegistry.owner')}: {item.owner_user || t('agentRegistry.unknown')}
          {' · '}
          {item.shared
            ? t('agentRegistry.allWorkspaces')
            : (item.workspaces.join(', ') || t('agentRegistry.noWorkspace'))}
        </span>
      </span>
      <StatusBadge status={item.review_status} t={t} />
      {(mine || admin) && (item.review_status === 'draft' || item.review_status === 'rejected') && (
        <NoteButton
          label={t('agentRegistry.submit')}
          tone="text-indigo-700 bg-indigo-50 hover:bg-indigo-100"
          icon={ShieldCheck}
          onConfirm={(note) => onSubmit(item.id, note)}
        />
      )}
      {admin && item.review_status === 'in_review' && (
        <>
          <NoteButton
            label={t('agentRegistry.approve')}
            tone="text-emerald-700 bg-emerald-50 hover:bg-emerald-100"
            icon={Check}
            onConfirm={(note) => onApprove(item.id, note)}
          />
          <NoteButton
            label={t('agentRegistry.reject')}
            tone="text-red-700 bg-red-50 hover:bg-red-100"
            icon={X}
            onConfirm={(note) => onReject(item.id, note)}
          />
        </>
      )}
    </div>
  );
}

function ReviewableTab({
  items, icon, emptyText, admin, currentUserId, onSubmit, onApprove, onReject, filters, t,
}) {
  const visible = items.filter((a) => {
    if (filters.status !== 'all' && a.review_status !== filters.status) return false;
    if (filters.owner && a.owner_user !== filters.owner) return false;
    if (filters.workspace && !a.shared && !a.workspaces.includes(filters.workspace)) return false;
    if (filters.query) {
      const q = filters.query.toLowerCase();
      if (!a.name.toLowerCase().includes(q) && !a.id.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="space-y-2">
      {visible.length === 0 ? (
        <p className="text-sm text-gray-400 py-8 text-center">{emptyText}</p>
      ) : visible.map((item) => (
        <ReviewableRow
          key={item.id}
          item={item}
          icon={icon}
          admin={admin}
          currentUserId={currentUserId}
          onSubmit={onSubmit}
          onApprove={onApprove}
          onReject={onReject}
          t={t}
        />
      ))}
    </div>
  );
}

function McpTab({
  servers, catalogEntries, admin, onApproveEntry, onBlockEntry, onRequest, filters, t,
}) {
  const [form, setForm] = useState({ id: '', name: '', transport: 'stdio', command: '', url: '' });
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState('');

  const submitRequest = async (e) => {
    e.preventDefault();
    setRequesting(true);
    setError('');
    try {
      await onRequest({ ...form, args: [] });
      setForm({ id: '', name: '', transport: 'stdio', command: '', url: '' });
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setRequesting(false);
    }
  };

  const visibleServers = servers.filter((s) => (
    !filters.workspace || s.workspace === filters.workspace
  ));
  const visibleCatalog = catalogEntries.filter((e) => (
    (filters.status === 'all' || e.status === filters.status)
    && (!filters.owner || e.owner_user === filters.owner)
  ));

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2">
          {t('agentRegistry.attachedServers')}
        </h3>
        {visibleServers.length === 0 ? (
          <p className="text-sm text-gray-400">{t('agentRegistry.noServers')}</p>
        ) : (
          <div className="space-y-2">
            {visibleServers.map((s) => (
              <div key={`${s.workspace}:${s.id}`} className="bg-white border border-gray-200 rounded-xl px-4 py-2.5 flex items-center gap-4">
                <Server className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-gray-800 truncate">{s.name}</span>
                  <span className="block text-[11px] text-gray-400 truncate">
                    <code>{s.id}</code> · {s.workspace} · {s.transport}
                  </span>
                </span>
                {!s.enabled && (
                  <span className="text-[10px] uppercase tracking-wider font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">
                    {t('agentRegistry.disabled')}
                  </span>
                )}
                <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full ${
                  s.approved ? 'text-emerald-700 bg-emerald-50' : 'text-amber-700 bg-amber-50'
                }`}
                >
                  {s.approved ? t('agentRegistry.approved') : t('agentRegistry.notApproved')}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2">
          {t('agentRegistry.mcpCatalog')}
        </h3>
        {visibleCatalog.length === 0 ? (
          <p className="text-sm text-gray-400 mb-3">{t('agentRegistry.noCatalogEntries')}</p>
        ) : (
          <div className="space-y-2 mb-4">
            {visibleCatalog.map((e) => (
              <div key={e.id} className="bg-white border border-gray-200 rounded-xl px-4 py-2.5 flex items-center gap-4">
                <ShieldCheck className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-gray-800 truncate">{e.name}</span>
                  <span className="block text-[11px] text-gray-400 truncate">
                    <code>{e.id}</code> · {e.transport} · {t('agentRegistry.owner')}: {e.owner_user || t('agentRegistry.unknown')}
                  </span>
                </span>
                <StatusBadge status={e.status} t={t} />
                {admin && e.status !== 'approved' && (
                  <NoteButton
                    label={t('agentRegistry.approve')}
                    tone="text-emerald-700 bg-emerald-50 hover:bg-emerald-100"
                    icon={Check}
                    onConfirm={(note) => onApproveEntry(e.id, note)}
                  />
                )}
                {admin && e.status !== 'blocked' && (
                  <NoteButton
                    label={t('agentRegistry.block')}
                    tone="text-red-700 bg-red-50 hover:bg-red-100"
                    icon={X}
                    onConfirm={(note) => onBlockEntry(e.id, note)}
                  />
                )}
              </div>
            ))}
          </div>
        )}

        <form onSubmit={submitRequest} className="bg-white border border-gray-200 rounded-xl p-4">
          <h4 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">
            {t('agentRegistry.requestServer')}
          </h4>
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <input
              required value={form.id} placeholder={t('agentRegistry.serverId')}
              onChange={(ev) => setForm((f) => ({ ...f, id: ev.target.value }))}
              className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
            />
            <input
              value={form.name} placeholder={t('agentRegistry.serverName')}
              onChange={(ev) => setForm((f) => ({ ...f, name: ev.target.value }))}
              className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
            />
            <select
              value={form.transport}
              onChange={(ev) => setForm((f) => ({ ...f, transport: ev.target.value }))}
              className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
            >
              <option value="stdio">stdio</option>
              <option value="streamable_http">streamable_http</option>
              <option value="sse">sse</option>
              <option value="websocket">websocket</option>
            </select>
            {form.transport === 'stdio' ? (
              <input
                value={form.command} placeholder={t('agentRegistry.command')}
                onChange={(ev) => setForm((f) => ({ ...f, command: ev.target.value }))}
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
              />
            ) : (
              <input
                value={form.url} placeholder={t('agentRegistry.url')}
                onChange={(ev) => setForm((f) => ({ ...f, url: ev.target.value }))}
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
              />
            )}
          </div>
          {error && <p className="text-xs text-red-600 mb-2">{error}</p>}
          <button
            type="submit" disabled={requesting || !form.id.trim()}
            className="px-4 py-2 text-sm font-bold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {t('agentRegistry.request')}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function AgentRegistry() {
  const { t } = useI18n();
  const auth = useAuth();
  const admin = !isMultiUser(auth) || isAdmin(auth);
  const currentUserId = auth?.user?.id || null;

  const [tab, setTab] = useState('agents');
  const [data, setData] = useState({
    agents: [], flows: [], skills: [], mcp_servers: [], mcp_catalog: [], settings: {},
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('all');
  const [owner, setOwner] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    try {
      const { data: body } = await getRegistry();
      setData(body);
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggleSetting = async (key, value) => {
    try {
      const { data: settings } = await updateRegistrySettings({ [key]: value });
      setData((d) => ({ ...d, settings }));
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    }
  };

  const withRefresh = (fn) => async (...args) => { await fn(...args); await load(); };

  return (
    <PageContainer>
      <PageHeader
        icon={BadgeCheck}
        title={t('agentRegistry.title')}
        description={t('agentRegistry.description')}
        actions={<>
          <button
            onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('agentRegistry.refresh')}
          </button>
        </>}
      />

      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

      {admin && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4 grid sm:grid-cols-2 gap-4">
          <Toggle
            label={t('agentRegistry.requireReviewLabel')}
            hint={t('agentRegistry.requireReviewHint')}
            checked={!!data.settings.registry_require_review}
            onChange={(v) => toggleSetting('registry_require_review', v)}
          />
          <Toggle
            label={t('agentRegistry.allowlistOnlyLabel')}
            hint={t('agentRegistry.allowlistOnlyHint')}
            checked={!!data.settings.mcp_allowlist_only}
            onChange={(v) => toggleSetting('mcp_allowlist_only', v)}
          />
        </div>
      )}

      <div className="flex items-center gap-3 mb-4">
        <div className="flex rounded-lg border border-gray-200 overflow-hidden">
          <button
            onClick={() => setTab('agents')}
            className={`inline-flex items-center px-3 py-2 text-xs font-semibold ${
              tab === 'agents' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
            }`}
          >
            <Bot className="w-3.5 h-3.5 mr-1.5" /> {t('agentRegistry.agentsTab')} ({data.agents.length})
          </button>
          <button
            onClick={() => setTab('flows')}
            className={`inline-flex items-center px-3 py-2 text-xs font-semibold ${
              tab === 'flows' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
            }`}
          >
            <GitBranch className="w-3.5 h-3.5 mr-1.5" /> {t('agentRegistry.flowsTab')} ({data.flows.length})
          </button>
          <button
            onClick={() => setTab('skills')}
            className={`inline-flex items-center px-3 py-2 text-xs font-semibold ${
              tab === 'skills' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
            }`}
          >
            <BookOpen className="w-3.5 h-3.5 mr-1.5" /> {t('agentRegistry.skillsTab')} ({data.skills.length})
          </button>
          <button
            onClick={() => setTab('mcp')}
            className={`inline-flex items-center px-3 py-2 text-xs font-semibold ${
              tab === 'mcp' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
            }`}
          >
            <Plug className="w-3.5 h-3.5 mr-1.5" /> {t('agentRegistry.mcpTab')} ({data.mcp_servers.length})
          </button>
        </div>

        <div className="relative">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('agentRegistry.search')}
            className="pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg w-56"
          />
        </div>

        <div className="flex items-center gap-1.5 text-gray-400">
          <Filter className="w-3.5 h-3.5" />
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="text-sm border border-gray-200 rounded-lg px-2 py-1.5"
          >
            <option value="all">{t('agentRegistry.allStatuses')}</option>
            {(tab === 'mcp'
              ? ['requested', 'approved', 'blocked']
              : ['draft', 'in_review', 'approved', 'rejected']
            ).map((s) => <option key={s} value={s}>{t(`agentRegistry.status.${s}`)}</option>)}
          </select>
        </div>

        <input
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          placeholder={t('agentRegistry.ownerFilter')}
          className="text-sm border border-gray-200 rounded-lg px-2 py-1.5 w-36"
        />

        <input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder={t('agentRegistry.workspaceFilter')}
          className="text-sm border border-gray-200 rounded-lg px-2 py-1.5 w-36"
        />
      </div>

      {loading ? (
        <PageLoader label={t('agentRegistry.loading')} />
      ) : tab === 'agents' ? (
        <ReviewableTab
          items={data.agents}
          icon={Bot}
          emptyText={t('agentRegistry.noAgents')}
          admin={admin}
          currentUserId={currentUserId}
          onSubmit={withRefresh(submitAgentForReview)}
          onApprove={withRefresh(approveAgent)}
          onReject={withRefresh(rejectAgent)}
          filters={{ status, owner, workspace, query }}
          t={t}
        />
      ) : tab === 'flows' ? (
        <ReviewableTab
          items={data.flows}
          icon={GitBranch}
          emptyText={t('agentRegistry.noFlows')}
          admin={admin}
          currentUserId={currentUserId}
          onSubmit={withRefresh(submitFlowForReview)}
          onApprove={withRefresh(approveFlow)}
          onReject={withRefresh(rejectFlow)}
          filters={{ status, owner, workspace, query }}
          t={t}
        />
      ) : tab === 'skills' ? (
        <ReviewableTab
          items={data.skills}
          icon={BookOpen}
          emptyText={t('agentRegistry.noSkills')}
          admin={admin}
          currentUserId={currentUserId}
          onSubmit={withRefresh(submitSkillForReview)}
          onApprove={withRefresh(approveSkill)}
          onReject={withRefresh(rejectSkill)}
          filters={{ status, owner, workspace, query }}
          t={t}
        />
      ) : (
        <McpTab
          servers={data.mcp_servers}
          catalogEntries={data.mcp_catalog}
          admin={admin}
          onApproveEntry={withRefresh(approveMcpCatalogEntry)}
          onBlockEntry={withRefresh(blockMcpCatalogEntry)}
          onRequest={withRefresh(requestMcpCatalogEntry)}
          filters={{ status, workspace, owner }}
          t={t}
        />
      )}
    </PageContainer>
  );
}
