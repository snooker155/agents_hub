/**
 * Guardrails page: the list of guardrails (rules and judges checking a run's
 * input/output), a create/edit modal per kind, an inline test box per row,
 * and a table of recent events. Backend: guardrails/, routes/guardrails.py.
 */
import { Fragment, useState, useEffect, useCallback } from 'react';
import { useWorkspace } from '../components/workspace';
import {
  getGuardrails, archiveGuardrail, deleteGuardrail, getGuardrailEvents,
} from '../api/guardrails';
import {
  ShieldCheck, Plus, Loader, RefreshCw, Archive, Trash2, Pencil, FlaskConical,
} from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { StageBadge, KindBadge, ActionBadge } from '../components/guardrails/badges';
import GuardrailModal from '../components/guardrails/GuardrailModal';
import GuardrailTestBox from '../components/guardrails/GuardrailTestBox';
import GuardrailEventsTable from '../components/guardrails/GuardrailEventsTable';
import PageLoader from '../components/PageLoader';

export default function Guardrails() {
  const { t } = useI18n();
  const { workspaceFilter } = useWorkspace();
  const [guardrails, setGuardrails] = useState([]);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [modalGuardrail, setModalGuardrail] = useState(undefined); // undefined closed, null create, object edit
  const [testingId, setTestingId] = useState(null);
  const [acting, setActing] = useState({});

  const fetchData = useCallback(async () => {
    try {
      const [{ data: rows }, { data: recentEvents }] = await Promise.all([
        getGuardrails(workspaceFilter, includeArchived),
        getGuardrailEvents({ workspace: workspaceFilter || undefined, limit: 50 }),
      ]);
      setGuardrails(rows || []);
      setEvents(recentEvents || []);
    } catch (err) {
      console.error('Failed to load guardrails', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter, includeArchived]);

  useEffect(() => { setLoading(true); fetchData(); }, [fetchData]);

  const act = async (id, fn) => {
    setActing((s) => ({ ...s, [id]: true }));
    try {
      await fn();
      await fetchData();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('guardrails.errors.actionFailed'));
    } finally {
      setActing((s) => ({ ...s, [id]: false }));
    }
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={ShieldCheck}
        title={t('guardrails.guardrails')}
        description={t('guardrails.pageDescription')}
        actions={<>
          <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
            <input type="checkbox" checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600" />
            {t('guardrails.showArchived')}
          </label>
          <button onClick={fetchData}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw className="w-4 h-4" /> {t('guardrails.refresh')}
          </button>
          <button onClick={() => setModalGuardrail(null)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
            <Plus className="w-4 h-4" /> {t('guardrails.newGuardrail')}
          </button>
        </>}
      />

      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200"><PageLoader /></div>
      ) : guardrails.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
          <ShieldCheck className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">{t('guardrails.noGuardrails')}</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.name')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.scope')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.stage.label')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.kind.label')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.action.label')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.enabled')}</th>
                <th className="text-right px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('guardrails.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {guardrails.map((g) => {
                const busy = acting[g.id];
                const archived = !!g.archived_at;
                const testing = testingId === g.id;
                return (
                  <Fragment key={g.id}>
                    <tr className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-900">{g.name}</span>
                          {archived && (
                            <span className="text-[10px] font-bold uppercase bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
                              {t('guardrails.archived')}
                            </span>
                          )}
                        </div>
                        {g.description && <div className="text-xs text-gray-400 mt-0.5 truncate max-w-xs">{g.description}</div>}
                      </td>
                      <td className="px-4 py-3 text-gray-600 text-xs">
                        {g.workspace ? g.workspace : <span className="italic text-gray-400">{t('guardrails.scopeGlobal')}</span>}
                      </td>
                      <td className="px-4 py-3"><StageBadge stage={g.stage} t={t} /></td>
                      <td className="px-4 py-3"><KindBadge kind={g.kind} t={t} /></td>
                      <td className="px-4 py-3"><ActionBadge action={g.action} t={t} /></td>
                      <td className="px-4 py-3 text-xs">
                        {g.enabled
                          ? <span className="text-emerald-600">{t('guardrails.enabled')}</span>
                          : <span className="text-gray-400">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1.5">
                          {busy ? (
                            <Loader className="w-4 h-4 animate-spin text-gray-400" />
                          ) : (
                            <>
                              <button title={t('guardrails.test')} onClick={() => setTestingId(testing ? null : g.id)}
                                className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50">
                                <FlaskConical className="w-4 h-4" />
                              </button>
                              {!archived && (
                                <button title={t('guardrails.edit')} onClick={() => setModalGuardrail(g)}
                                  className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50">
                                  <Pencil className="w-4 h-4" />
                                </button>
                              )}
                              {!archived && (
                                <button title={t('guardrails.archive')} onClick={() => act(g.id, () => archiveGuardrail(g.id))}
                                  className="p-1.5 rounded text-gray-400 hover:text-amber-600 hover:bg-amber-50">
                                  <Archive className="w-4 h-4" />
                                </button>
                              )}
                              <button title={t('guardrails.delete')}
                                onClick={() => {
                                  if (window.confirm(t('guardrails.confirmDelete', { name: g.name }))) act(g.id, () => deleteGuardrail(g.id));
                                }}
                                className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50">
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                    {testing && (
                      <tr>
                        <td colSpan={7} className="px-4 pb-4 bg-gray-50">
                          <GuardrailTestBox guardrail={g} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <h3 className="text-sm font-bold text-gray-900 mb-3">{t('guardrails.events.title')}</h3>
        <GuardrailEventsTable events={events} />
      </div>

      {modalGuardrail !== undefined && (
        <GuardrailModal
          guardrail={modalGuardrail}
          workspace={workspaceFilter}
          onClose={() => setModalGuardrail(undefined)}
          onSaved={() => { setModalGuardrail(undefined); fetchData(); }}
        />
      )}
    </PageContainer>
  );
}
