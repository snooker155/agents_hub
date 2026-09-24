import React, { useEffect, useState } from 'react';
import { GitBranch, Loader } from 'lucide-react';
import { getAgentVersions, updateTask } from '../../api/agentVersions';
import { useI18n } from '../../i18n';

/**
 * TaskAgentVersionPin: pin the task's agent runs to a stored version of its
 * assigned agent (agents/versions.py), or leave it on the live definition.
 *
 * Nothing to pin without an assigned agent (no version history to choose
 * from either), so the card renders nothing until one is assigned and it has
 * at least one stored version. The select's own "live" option clears the pin.
 */
export default function TaskAgentVersionPin({ task, onChanged }) {
  const { t } = useI18n();
  const agentId = task?.assigned_agent_type || '';
  const [versions, setVersions] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setError('');
    if (!agentId) { setVersions([]); return; }
    let cancelled = false;
    getAgentVersions(agentId)
      .then(({ data }) => { if (!cancelled) setVersions(data?.versions || []); })
      .catch(() => { if (!cancelled) setVersions([]); });
    return () => { cancelled = true; };
  }, [agentId]);

  if (!task || !agentId || versions.length === 0) return null;

  const currentVersion = versions[versions.length - 1].version;
  const isPinned = task.agent_version != null;
  const selectValue = isPinned ? String(task.agent_version) : '';

  const onSelect = async (e) => {
    const raw = e.target.value;
    const value = raw === '' ? null : Number(raw);
    setBusy(true);
    setError('');
    try {
      await updateTask(task.id, { agent_version: value });
      if (onChanged) onChanged();
    } catch (err) {
      setError(err?.response?.data?.detail || t('agentVersionPin.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="bg-white border border-gray-200 rounded-xl px-4 py-3 mb-6 flex items-center gap-3 flex-wrap"
      data-testid="task-agent-version-pin"
    >
      <GitBranch className="w-4 h-4 text-gray-400 shrink-0" />
      <span className="text-sm font-semibold text-gray-800">{t('agentVersionPin.title')}</span>
      <span className="text-sm text-gray-600">
        {isPinned
          ? t('agentVersionPin.pinned', { version: task.agent_version })
          : t('agentVersionPin.live', { version: currentVersion })}
      </span>
      <div className="ml-auto flex items-center gap-2">
        {busy && <Loader className="w-3.5 h-3.5 animate-spin text-gray-400" />}
        <select
          value={selectValue}
          onChange={onSelect}
          disabled={busy}
          className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 disabled:opacity-50"
          aria-label={t('agentVersionPin.title')}
        >
          <option value="">{t('agentVersionPin.jobFieldLive')}</option>
          {versions.slice().reverse().map((v) => (
            <option key={v.version} value={v.version}>
              {t('agentVersionPin.versionOption', { version: v.version })}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="w-full text-sm text-red-600">{error}</p>}
    </div>
  );
}
