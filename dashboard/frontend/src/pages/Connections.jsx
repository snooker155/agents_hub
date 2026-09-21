import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Download,
  Link2,
  Plus,
  RefreshCw,
  Share2,
  Workflow,
  X,
} from 'lucide-react';

import { createConnection, listConnections } from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { CopyButton, SetupSnippet } from '../components/ConnectionSetup';
import { useI18n } from '../i18n';
import { useWorkspace } from '../components/workspace';

/**
 * Connections: agents that run somewhere else, on their own trigger, and report
 * here.
 *
 * The important screen on this page is the empty one. It is what someone sees
 * who is evaluating whether this product can watch the agents they already
 * have, and the answer has to read as three choices rather than as a form.
 */

const KIND_LABEL = {
  langgraph: 'LangGraph',
  crewai: 'CrewAI',
  autogen: 'AutoGen',
  llamaindex: 'LlamaIndex',
  http: 'HTTP',
  other: 'Other',
};

function fmtWhen(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** What a new connection is: an id, a name, and what it is built with. */
function CreateModal({ workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [form, setForm] = useState({ id: '', name: '', kind: 'langgraph' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [issued, setIssued] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const { data } = await createConnection({
        id: form.id.trim(),
        name: form.name.trim() || form.id.trim(),
        kind: form.kind,
        workspace: workspace && workspace !== 'default' ? workspace : null,
      });
      setIssued(data);
      onCreated?.();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  // Once the token exists the dialog stops being a form: it is the only moment
  // the token can be read, so it takes over the whole surface rather than
  // sharing it with fields nobody will edit again.
  if (issued) {
    return (
      <Modal onClose={onClose} title={t('connections.tokenTitle')}>
        <p className="text-sm text-gray-500 mb-3">{t('connections.tokenOnce')}</p>
        <div className="flex items-center gap-2 mb-4">
          <code className="flex-1 text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 break-all">
            {issued.token}
          </code>
          <CopyButton value={issued.token} />
        </div>
        <SetupSnippet token={issued.token} connectionId={issued.connection.id} />
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-500">
            {t('connections.close')}
          </button>
          <button
            onClick={() => navigate(`/connections/${encodeURIComponent(issued.connection.id)}`)}
            className="px-4 py-2 text-sm font-bold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            {t('connections.openConnection')}
          </button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal onClose={onClose} title={t('connections.newConnection')}>
      <form onSubmit={submit}>
        <label className="block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1">
          {t('connections.idLabel')}
        </label>
        <input
          value={form.id}
          onChange={(e) => setForm({ ...form, id: e.target.value })}
          placeholder="billing-graph"
          required
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-1 focus:ring-indigo-500 focus:border-indigo-500"
        />
        <p className="text-[11px] text-gray-400 mb-3">{t('connections.idHint')}</p>

        <label className="block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1">
          {t('connections.nameLabel')}
        </label>
        <input
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="Billing graph"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-3 focus:ring-indigo-500 focus:border-indigo-500"
        />

        <label className="block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1">
          {t('connections.kindLabel')}
        </label>
        <select
          value={form.kind}
          onChange={(e) => setForm({ ...form, kind: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 focus:ring-indigo-500 focus:border-indigo-500"
        >
          {Object.entries(KIND_LABEL).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>

        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-500">
            {t('connections.cancel')}
          </button>
          <button
            type="submit"
            disabled={saving || !form.id.trim()}
            className="px-5 py-2 text-sm font-bold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {t('connections.create')}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Modal({ title, children, onClose }) {
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
      <div className="bg-white rounded-xl max-w-xl w-full p-6 shadow-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">{title}</h3>
          <button onClick={onClose} aria-label="close" className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** The screen a new evaluator sees: three ways in, not a form. */
function EmptyState({ onCreate }) {
  const { t } = useI18n();
  const tiles = [
    {
      key: 'observe',
      icon: Share2,
      to: null,
      onClick: onCreate,
      primary: true,
    },
    { key: 'import', icon: Download, to: '/agents' },
    { key: 'watch', icon: Link2, to: '/agents' },
  ];
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-8 text-center">
      <Workflow className="w-8 h-8 text-indigo-400 mx-auto mb-3" />
      <h3 className="text-base font-semibold text-gray-800 mb-1">{t('connections.emptyTitle')}</h3>
      <p className="text-sm text-gray-500 mb-6">{t('connections.emptyBody')}</p>

      <div className="grid gap-3 sm:grid-cols-3 text-left max-w-3xl mx-auto">
        {tiles.map(({ key, icon: Icon, to, onClick, primary }) => {
          const inner = (
            <>
              <Icon className={`w-5 h-5 mb-2 ${primary ? 'text-indigo-600' : 'text-gray-400'}`} />
              <span className="block text-sm font-semibold text-gray-800 mb-1">
                {t(`connections.tiles.${key}.title`)}
              </span>
              <span className="block text-xs text-gray-500 leading-relaxed">
                {t(`connections.tiles.${key}.body`)}
              </span>
            </>
          );
          const className = `block rounded-xl border p-4 h-full text-left transition-colors ${
            primary ? 'border-indigo-200 bg-indigo-50/40 hover:bg-indigo-50' : 'border-gray-200 hover:bg-gray-50'
          }`;
          return to
            ? <Link key={key} to={to} className={className}>{inner}</Link>
            : <button key={key} type="button" onClick={onClick} className={className}>{inner}</button>;
        })}
      </div>

      <p className="text-xs text-gray-400 mt-6">{t('connections.emptyFooter')}</p>
    </div>
  );
}

function ConnectionRow({ connection }) {
  const { t } = useI18n();
  const stats = connection.stats || {};
  const seen = fmtWhen(connection.last_seen);
  return (
    <Link
      to={`/connections/${encodeURIComponent(connection.id)}`}
      className="flex items-center gap-4 bg-white border border-gray-200 rounded-xl px-4 py-3 hover:border-indigo-200 hover:bg-indigo-50/20"
    >
      <span
        className={`w-2 h-2 rounded-full flex-shrink-0 ${
          connection.disabled ? 'bg-gray-300'
            : stats.running ? 'bg-emerald-500 animate-pulse'
            : seen ? 'bg-emerald-500' : 'bg-amber-400'
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-gray-800 truncate">{connection.name}</span>
        <span className="block text-[11px] text-gray-400 truncate">
          {connection.id}
          {/* A connection that has never reported is the normal state right
              after setup, and saying so is more useful than an empty column. */}
          {seen ? ` · ${t('connections.lastSeen', { when: seen })}` : ` · ${t('connections.neverSeen')}`}
        </span>
      </span>
      <span className="hidden sm:inline text-[10px] uppercase tracking-wider font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
        {KIND_LABEL[connection.kind] || connection.kind}
      </span>
      {connection.disabled && (
        <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">
          <AlertTriangle className="w-3 h-3" /> {t('connections.disabled')}
        </span>
      )}
      <span className="text-right text-xs text-gray-500 w-28 hidden md:block">
        {t('connections.runsCount', { count: stats.runs || 0 })}
        {stats.failed > 0 && (
          <span className="block text-red-600">{t('connections.failedCount', { count: stats.failed })}</span>
        )}
      </span>
    </Link>
  );
}

export default function Connections() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await listConnections(selectedWorkspace);
      setConnections(data.connections || []);
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [selectedWorkspace]);

  useEffect(() => { load(); }, [load]);

  return (
    <PageContainer>
      <PageHeader
        icon={Share2}
        title={t('connections.title')}
        description={t('connections.description')}
        actions={<>
          <button
            onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('connections.refresh')}
          </button>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" />
            {t('connections.connect')}
          </button>
        </>}
      />

      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-400">{t('connections.loading')}</p>
      ) : connections.length === 0 ? (
        <EmptyState onCreate={() => setCreating(true)} />
      ) : (
        <div className="space-y-2">
          {connections.map((c) => <ConnectionRow key={c.id} connection={c} />)}
        </div>
      )}

      {creating && (
        <CreateModal
          workspace={selectedWorkspace}
          onClose={() => setCreating(false)}
          onCreated={load}
        />
      )}
    </PageContainer>
  );
}
