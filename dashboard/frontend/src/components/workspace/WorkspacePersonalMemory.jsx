import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader, Star } from 'lucide-react';
import {
  getAgents, getWorkspacePersonalMemory, updateAgentPersonalMemory, updateWorkspacePersonalMemory,
} from '../../api';
import { SectionCard } from '../settingsUi';
import { Toggle } from '../settings/WorkspaceSettingsSections';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

/**
 * Personal memory in this workspace (memory/personal.py): whether it exists
 * here at all, and which agents use it. Off, every agent has it off and the
 * agents' switches cannot be changed; they are kept for when it comes back.
 * Saves itself, like the tool policy.
 */
export default function WorkspacePersonalMemory({ workspace, agents = [] }) {
  const { t } = useI18n();
  const toast = useToast();
  const [config, setConfig] = useState(null);
  const [names, setNames] = useState({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    getWorkspacePersonalMemory(workspace)
      .then(({ data }) => setConfig(data))
      .catch((e) => toast.error(t('workspaceDetails.personalMemory.loadFailed'), errorDetail(e)));
  }, [workspace, t, toast]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getAgents(workspace)
      .then(({ data }) => setNames(Object.fromEntries((data || []).map((a) => [a.id, a.name || a.id]))))
      .catch(() => setNames({}));
  }, [workspace]);

  const save = async (fn) => {
    setSaving(true);
    try {
      await fn();
    } catch (e) {
      toast.error(t('workspaceDetails.personalMemory.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
      load();
    }
  };

  if (!config) {
    return (
      <p className="text-sm text-gray-500 flex items-center gap-2">
        <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
      </p>
    );
  }

  // The main agent first, then the rest in the workspace's order.
  const ids = [...new Set([config.main_agent, ...agents].filter(Boolean))];
  return (
    <SectionCard title={t('workspaceDetails.personalMemory.title')}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('workspaceDetails.personalMemory.available')}</label>
          <p className="text-xs text-gray-500 mt-1">{t('workspaceDetails.personalMemory.availableHint')}</p>
        </div>
        <Toggle
          checked={config.enabled}
          disabled={saving}
          onChange={(v) => save(() => updateWorkspacePersonalMemory(workspace, v))}
        />
      </div>

      <div className="mt-5">
        <p className="text-sm font-medium text-gray-700">{t('workspaceDetails.personalMemory.agents')}</p>
        <p className="text-xs text-gray-500 mt-1 mb-2">
          {config.enabled ? t('workspaceDetails.personalMemory.agentsHint') : t('workspaceDetails.personalMemory.agentsOff')}
        </p>
        <div className="divide-y divide-gray-100 rounded-lg border border-gray-200" data-testid="personal-memory-agents">
          {ids.map((id) => (
            <div key={id} className="flex items-center justify-between gap-3 px-3 py-2">
              <div className="min-w-0 flex items-center gap-1.5 text-sm text-gray-800">
                <Link to={`/agents/${encodeURIComponent(id)}`} className="truncate hover:text-indigo-600">
                  {names[id] || id}
                </Link>
                {id === config.main_agent && (
                  <span className="shrink-0 inline-flex items-center gap-1 text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded-full font-medium">
                    <Star className="w-3 h-3" /> {t('workspaceDetails.personalMemory.mainAgent')}
                  </span>
                )}
              </div>
              <Toggle
                checked={config.enabled && !!config.agents[id]}
                disabled={saving || !config.enabled}
                onChange={(v) => save(() => updateAgentPersonalMemory(id, v, workspace))}
              />
            </div>
          ))}
        </div>
      </div>
    </SectionCard>
  );
}
