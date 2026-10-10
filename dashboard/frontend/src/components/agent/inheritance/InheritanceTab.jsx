import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { GitBranch, Lock, Loader, Unlink, Users } from 'lucide-react';
import { getAgents } from '../../../api';
import { getAgentInheritance, resetAgentOverride, setAgentExtends } from '../../../api/agentInheritance';
import { useI18n } from '../../../i18n';
import AgentParentPicker from './AgentParentPicker';
import { ScalarFieldsTable, ListFieldsView } from './InheritanceFields';
import PromptMergeView from './PromptMergeView';

/**
 * Everything about where this agent's configuration comes from: its parent
 * (if any), the agents that extend it, every inheritable field with its
 * source and a reset for the ones this agent overrides, and how its prompt
 * merges with its parent's (agents/inheritance.py, GET
 * /agents/{id}/inheritance). Changing or repinning the parent and detaching
 * also live here (PUT/DELETE .../extends).
 */
export default function InheritanceTab({ agentId, agent, onChanged, onEditOwnInstructions }) {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerValue, setPickerValue] = useState({ extends: '', extends_version: null });
  const [savingParent, setSavingParent] = useState(false);
  const [parentError, setParentError] = useState('');

  const [detachConfirm, setDetachConfirm] = useState(false);
  const [detaching, setDetaching] = useState(false);

  const [resettingField, setResettingField] = useState(null);

  const load = useCallback(() => {
    if (!agentId) return;
    // Nothing is set before the response: `loading` starts true for the first
    // load, and a reload keeps showing the current data until the new arrives.
    Promise.all([
      getAgentInheritance(agentId),
      getAgents().catch(() => ({ data: [] })),
    ])
      .then(([inhResp, agentsResp]) => {
        setError('');
        setData(inhResp.data || null);
        const list = agentsResp.data?.agents || agentsResp.data || [];
        setAgents(Array.isArray(list) ? list : []);
      })
      .catch((e) => setError(e?.response?.data?.detail || t('agentInheritance.errors.load')))
      .finally(() => setLoading(false));
  }, [agentId, t]);

  useEffect(() => { load(); }, [load]);

  const openPicker = () => {
    setPickerValue({ extends: data?.extends || '', extends_version: data?.extends_version ?? null });
    setParentError('');
    setPickerOpen(true);
  };

  const saveParent = async () => {
    if (!pickerValue.extends) return;
    setSavingParent(true);
    setParentError('');
    try {
      await setAgentExtends(agentId, {
        extends: pickerValue.extends,
        extends_version: pickerValue.extends_version ?? null,
      });
      setPickerOpen(false);
      load();
      onChanged?.();
    } catch (e) {
      setParentError(e?.response?.data?.detail?.message || e?.response?.data?.detail || t('agentInheritance.errors.setParent'));
    } finally {
      setSavingParent(false);
    }
  };

  const detach = async () => {
    setDetaching(true);
    try {
      await setAgentExtends(agentId, { extends: null, extends_version: null });
      setDetachConfirm(false);
      load();
      onChanged?.();
    } catch (e) {
      setError(e?.response?.data?.detail || t('agentInheritance.errors.detach'));
    } finally {
      setDetaching(false);
    }
  };

  const handleReset = async (field) => {
    setResettingField(field);
    try {
      await resetAgentOverride(agentId, field);
      load();
      onChanged?.();
    } catch (e) {
      setError(e?.response?.data?.detail || t('agentInheritance.errors.reset'));
    } finally {
      setResettingField(null);
    }
  };

  if (loading && !data) {
    return (
      <div className="bg-white p-6 shadow-md rounded-lg flex items-center gap-2 text-sm text-gray-500">
        <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
      </div>
    );
  }

  const canBeChild = !agent?.system;
  const hasParent = !!data?.extends;

  return (
    <div className="space-y-6" data-testid="inheritance-tab">
      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="bg-white p-6 shadow-md rounded-lg">
        <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
          <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-indigo-500" /> {t('agentInheritance.title')}
          </h3>
          {canBeChild && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={openPicker}
                className="px-3 py-1.5 text-xs font-semibold text-indigo-700 bg-indigo-50 rounded hover:bg-indigo-100"
              >
                {hasParent ? t('agentInheritance.changeParentOrRepin') : t('agentInheritance.makeChild')}
              </button>
              {hasParent && (
                <button
                  type="button"
                  onClick={() => setDetachConfirm(true)}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-amber-700 bg-amber-50 rounded hover:bg-amber-100"
                >
                  <Unlink className="w-3.5 h-3.5" /> {t('agentInheritance.detach')}
                </button>
              )}
            </div>
          )}
        </div>

        {!canBeChild && (
          <p className="text-xs text-gray-400 mb-3">{t('agentInheritance.systemCannotBeChild')}</p>
        )}

        {!hasParent ? (
          <p className="text-sm text-gray-500">{t('agentInheritance.standalone')}</p>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-gray-500">{t('agentInheritance.chain')}</p>
            <div className="flex items-center gap-1.5 flex-wrap" data-testid="inheritance-chain">
              {(data.chain || []).map((link, i) => (
                <span key={link.id} className="flex items-center gap-1.5">
                  {i > 0 && <span className="text-gray-300">→</span>}
                  <Link to={`/agents/${link.id}`} className="text-sm text-indigo-600 hover:text-indigo-800 font-medium">
                    {link.name || link.id}
                  </Link>
                  {link.pinned && (
                    <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-1 py-0.5 rounded">
                      <Lock className="w-2.5 h-2.5" /> {t('agentInheritance.versionN', { n: link.version })}
                    </span>
                  )}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="mt-4 pt-4 border-t border-gray-100">
          <p className="text-xs text-gray-500 mb-2 flex items-center gap-1.5">
            <Users className="w-3.5 h-3.5" />
            {t('agentInheritance.childrenCount', { count: (data?.children || []).length })}
          </p>
          {(data?.children || []).length > 0 && (
            <div className="flex flex-wrap gap-1.5" data-testid="inheritance-children">
              {data.children.map((c) => (
                <Link
                  key={c.id}
                  to={`/agents/${c.id}`}
                  className="text-xs bg-gray-50 border border-gray-200 text-gray-700 hover:text-indigo-700 hover:border-indigo-200 px-2 py-0.5 rounded-full"
                >
                  {c.name || c.id}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {hasParent && (
        <>
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 mb-3">{t('agentInheritance.fields')}</h3>
            <ScalarFieldsTable
              agentId={agentId}
              fields={data?.fields}
              onReset={handleReset}
              resettingField={resettingField}
            />
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 mb-3">{t('agentInheritance.listFields')}</h3>
            <ListFieldsView
              agentId={agentId}
              effectiveLists={data?.effective_lists}
              listDeltas={data?.list_deltas}
              onReset={handleReset}
              resettingField={resettingField}
            />
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 mb-3">{t('agentInheritance.prompt.title')}</h3>
            <PromptMergeView prompt={data?.prompt} onEditOwnInstructions={onEditOwnInstructions} />
          </div>
        </>
      )}

      {pickerOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold mb-4">{hasParent ? t('agentInheritance.changeParentOrRepin') : t('agentInheritance.makeChild')}</h3>
            <AgentParentPicker
              agents={agents}
              excludeId={agentId}
              value={pickerValue}
              onChange={setPickerValue}
              allowClear={false}
              idPrefix="inheritance-tab-picker"
            />
            {parentError && <p className="text-xs text-red-600 mt-3">{parentError}</p>}
            <div className="flex justify-end gap-3 mt-5">
              <button type="button" onClick={() => setPickerOpen(false)} className="px-4 py-2 text-sm text-gray-500">
                {t('agentDetails.cancel')}
              </button>
              <button
                type="button"
                onClick={saveParent}
                disabled={savingParent || !pickerValue.extends}
                className="bg-indigo-600 text-white px-5 py-2 rounded-md font-semibold shadow-md disabled:opacity-50"
              >
                {savingParent ? t('common.saving') : t('common.save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {detachConfirm && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
          <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold mb-3 text-amber-700">{t('agentInheritance.detach')}</h3>
            <p className="text-sm text-gray-600 mb-5">{t('agentInheritance.detachConfirm')}</p>
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setDetachConfirm(false)} className="px-4 py-2 text-sm text-gray-500">
                {t('agentDetails.cancel')}
              </button>
              <button
                type="button"
                onClick={detach}
                disabled={detaching}
                className="bg-amber-600 text-white px-5 py-2 rounded-md font-semibold shadow-md disabled:opacity-50"
              >
                {detaching ? t('common.saving') : t('agentInheritance.confirmDetach')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
