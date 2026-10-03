import { useCallback, useEffect, useState } from 'react';
import { Database, Plus, RefreshCw, Trash2, Wifi } from 'lucide-react';
import {
  listDbConnections, createDbConnection, deleteDbConnection, testDbConnection,
} from '../../api/databases';
import { useWorkspace } from '../workspace';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import PageLoader from '../PageLoader';

const KINDS = ['postgres', 'mysql', 'clickhouse', 'sqlite'];
const PLACEHOLDERS = {
  postgres: 'postgresql://user:password@host:5432/dbname',
  mysql: 'mysql://user:password@host:3306/dbname',
  clickhouse: 'clickhouse://user:password@host:8123/default',
  sqlite: '/path/to/file.db',
};

// Read-only database connections of the current workspace
// (routes/databases.py). An agent with db_query may only run a single SELECT
// against one of these, row capped, on a read-only session.
export default function DatabasesConnector() {
  const { t } = useI18n();
  const { selectedWorkspace: workspace } = useWorkspace();
  const [loading, setLoading] = useState(true);
  const [connections, setConnections] = useState([]);
  const [form, setForm] = useState({ name: '', kind: 'postgres', dsn: '', allowed_schemas: '', row_limit: 200 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState({});

  const load = useCallback(async () => {
    if (!workspace) { setConnections([]); setLoading(false); return; }
    try {
      const { data } = await listDbConnections(workspace);
      setConnections(data || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [workspace]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    setBusy(true);
    setError('');
    try {
      await createDbConnection({
        workspace,
        name: form.name.trim(),
        kind: form.kind,
        dsn: form.dsn.trim(),
        allowed_schemas: form.allowed_schemas.split(',').map((s) => s.trim()).filter(Boolean),
        row_limit: Number(form.row_limit) || 200,
      });
      setForm({ name: '', kind: form.kind, dsn: '', allowed_schemas: '', row_limit: 200 });
      await load();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c) => {
    if (!window.confirm(t('connectors.databases.confirmRemove', { name: c.name }))) return;
    try { await deleteDbConnection(c.id); await load(); } catch (e) { setError(e.response?.data?.detail || e.message); }
  };

  const test = async (c) => {
    setResults((r) => ({ ...r, [c.id]: { pending: true } }));
    try {
      const { data } = await testDbConnection(c.id);
      setResults((r) => ({ ...r, [c.id]: data }));
    } catch (e) {
      setResults((r) => ({ ...r, [c.id]: { ok: false, error: e.response?.data?.detail || e.message } }));
    }
  };

  if (loading) return <PageLoader size="sm" />;

  return (
    <div className="space-y-5">
      <SectionCard title={t('connectors.databases.title')}>
        <p className="text-sm text-gray-600">{t('connectors.databases.intro')}</p>
        {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
        {connections.length === 0 ? (
          <div className="text-sm text-gray-500 flex items-center gap-2 py-3">
            <Database className="w-4 h-4" /> {t('connectors.databases.none')}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase">
                <th className="py-2 pr-3">{t('connectors.databases.name')}</th>
                <th className="py-2 pr-3">{t('connectors.databases.kind')}</th>
                <th className="py-2 pr-3">{t('connectors.databases.target')}</th>
                <th className="py-2 pr-3">{t('connectors.databases.rowLimit')}</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {connections.map((c) => (
                <tr key={c.id} className="border-t border-gray-100">
                  <td className="py-2 pr-3 text-gray-800">{c.name}</td>
                  <td className="py-2 pr-3 text-gray-700">{c.kind}</td>
                  <td className="py-2 pr-3 font-mono text-xs text-gray-600">{c.dsn_hint}</td>
                  <td className="py-2 pr-3 text-gray-700">{c.row_limit}</td>
                  <td className="py-2 text-right whitespace-nowrap">
                    <button type="button" onClick={() => test(c)} className="inline-flex items-center gap-1 text-xs text-gray-700 hover:bg-gray-50 border border-gray-200 rounded-md px-2 py-1 mr-2">
                      {results[c.id]?.pending ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />} {t('settings.testConnection')}
                    </button>
                    <button type="button" onClick={() => remove(c)} className="inline-flex items-center gap-1 text-xs text-red-600 hover:bg-red-50 border border-red-200 rounded-md px-2 py-1">
                      <Trash2 className="w-3 h-3" /> {t('settings.remove')}
                    </button>
                    {results[c.id] && !results[c.id].pending && (
                      <div className={`text-xs mt-1 ${results[c.id].ok ? 'text-green-700' : 'text-red-600'}`}>
                        {results[c.id].ok ? `ok, ${results[c.id].elapsed_ms} ms` : results[c.id].error}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SectionCard>

      <SectionCard title={t('connectors.databases.add')}>
        <div className="grid gap-3 md:grid-cols-2">
          <input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={t('connectors.databases.name')} />
          <select className={inputCls} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <input className={inputCls + ' md:col-span-2'} type="password" autoComplete="new-password" value={form.dsn} onChange={(e) => setForm({ ...form, dsn: e.target.value })} placeholder={PLACEHOLDERS[form.kind]} />
          <input className={inputCls} value={form.allowed_schemas} onChange={(e) => setForm({ ...form, allowed_schemas: e.target.value })} placeholder={t('connectors.databases.schemas')} />
          <input className={inputCls} type="number" value={form.row_limit} onChange={(e) => setForm({ ...form, row_limit: e.target.value })} placeholder={t('connectors.databases.rowLimit')} />
        </div>
        <p className="text-xs text-gray-500">{t('connectors.databases.dsnHint')}</p>
        <button type="button" onClick={add} disabled={busy || !form.name.trim() || !form.dsn.trim() || !workspace} className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50">
          <Plus className="w-3.5 h-3.5" /> {t('connectors.databases.add')}
        </button>
      </SectionCard>
    </div>
  );
}
