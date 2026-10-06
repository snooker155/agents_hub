import { useEffect, useMemo, useState } from 'react';
import { Lock } from 'lucide-react';
import { getAgentVersions } from '../../../api';
import { useI18n } from '../../../i18n';

/**
 * "Based on" picker: choose a parent agent to extend, with an optional pin to
 * one of its stored versions. Shared by the create dialog (AgentManager) and
 * the change parent / repin action on an existing agent's Inheritance tab.
 *
 * A remote agent can never be a parent (agents/registry.py), so the candidate
 * list never includes one. System agents sort first, since they are the most
 * common base to specialize from.
 */
export default function AgentParentPicker({
  agents, excludeId, value, onChange, allowClear = true, disabled = false, idPrefix = 'parent-picker',
}) {
  const { t } = useI18n();
  const [versions, setVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const candidates = useMemo(() => (
    (agents || [])
      .filter((a) => a && !a.remote && a.id !== excludeId)
      .sort((a, b) => (
        (b.system ? 1 : 0) - (a.system ? 1 : 0)
        || (a.name || a.id).localeCompare(b.name || b.id)
      ))
  ), [agents, excludeId]);

  const systemCandidates = candidates.filter((a) => a.system);
  const ownCandidates = candidates.filter((a) => !a.system);

  // On a microtask rather than straight from the effect body: the first
  // branch below sets state synchronously, which an effect should not do
  // (see useAgentVersions.js for the same pattern).
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      if (!value?.extends) { setVersions([]); return; }
      setVersionsLoading(true);
      getAgentVersions(value.extends)
        .then((r) => { if (!cancelled) setVersions(r.data?.versions || []); })
        .catch(() => { if (!cancelled) setVersions([]); })
        .finally(() => { if (!cancelled) setVersionsLoading(false); });
    });
    return () => { cancelled = true; };
  }, [value?.extends]);

  const setParent = (parentId) => {
    onChange(parentId ? { extends: parentId, extends_version: null } : { extends: '', extends_version: null });
  };

  const setVersion = (raw) => {
    onChange({ ...value, extends_version: raw ? Number(raw) : null });
  };

  const selectId = `${idPrefix}-select`;
  const pinId = `${idPrefix}-pin`;

  return (
    <div className="space-y-2">
      <label className="block text-xs font-bold text-gray-500 uppercase mb-1" htmlFor={selectId}>
        {t('agentInheritance.basedOn')}
      </label>
      <select
        id={selectId}
        value={value?.extends || ''}
        disabled={disabled}
        onChange={(e) => setParent(e.target.value)}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white"
      >
        <option value="">{allowClear ? t('agentInheritance.noParent') : t('agentInheritance.chooseParent')}</option>
        {systemCandidates.length > 0 && (
          <optgroup label={t('agentInheritance.systemAgents')}>
            {systemCandidates.map((a) => (
              <option key={a.id} value={a.id}>{a.name || a.id}</option>
            ))}
          </optgroup>
        )}
        {ownCandidates.length > 0 && (
          <optgroup label={t('agentInheritance.yourAgents')}>
            {ownCandidates.map((a) => (
              <option key={a.id} value={a.id}>{a.name || a.id}</option>
            ))}
          </optgroup>
        )}
      </select>
      {value?.extends && (
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500" htmlFor={pinId}>{t('agentInheritance.pinVersion')}</label>
          <select
            id={pinId}
            value={value.extends_version ?? ''}
            disabled={disabled || versionsLoading}
            onChange={(e) => setVersion(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1 text-xs bg-white"
          >
            <option value="">{t('agentInheritance.latest')}</option>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>{t('agentInheritance.versionN', { n: v.version })}</option>
            ))}
          </select>
          {value.extends_version != null && (
            <span className="inline-flex items-center gap-1 text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
              <Lock className="w-2.5 h-2.5" /> {t('agentInheritance.pinned')}
            </span>
          )}
        </div>
      )}
      <p className="text-[11px] text-gray-400">{t('agentInheritance.basedOnHint')}</p>
    </div>
  );
}
