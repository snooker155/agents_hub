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
 * Tab: Personal memory — whether it exists in the workspace picked in the
 * header at all (memory/personal.py), and which agents use it. Off, every
 * agent has it off and the agents' switches cannot be changed; they are kept
 * for when it comes back. Saves itself.
 */
export function PersonalMemoryTab({ workspaceFilter }) {
  const { t } = useI18n();
  const toast = useToast();
  const workspace = workspaceFilter || 'default';
  const [config, setConfig] = useState(null);
  // The workspace's agents in its own order: [{id, name, description}].
  const [agents, setAgents] = useState([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    getWorkspacePersonalMemory(workspace)
      .then(({ data }) => setConfig(data))
      .catch((e) => toast.error(t('memoryManager.personalTab.loadFailed'), errorDetail(e)));
  }, [workspace, t, toast]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getAgents(workspace)
      .then(({ data }) => setAgents((data || []).map((a) => ({ id: a.id, name: a.name || a.id, description: a.description || '' }))))
      .catch(() => setAgents([]));
  }, [workspace]);

  const save = async (fn) => {
    setSaving(true);
    try {
      await fn();
    } catch (e) {
      toast.error(t('memoryManager.personalTab.saveFailed'), errorDetail(e));
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
  const byId = Object.fromEntries(agents.map((a) => [a.id, a]));
  const ids = [...new Set([config.main_agent, ...agents.map((a) => a.id)].filter(Boolean))];
  return (
    <SectionCard
      title={t('memoryManager.personalTab.title')}
      actions={<span className="text-xs text-gray-400">{t('memoryManager.appliesInWorkspace', { workspace })}</span>}
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('memoryManager.personalTab.available')}</label>
          <p className="text-xs text-gray-500 mt-1">{t('memoryManager.personalTab.availableHint')}</p>
        </div>
        <Toggle
          checked={config.enabled}
          disabled={saving}
          onChange={(v) => save(() => updateWorkspacePersonalMemory(workspace, v))}
        />
      </div>

      <div className="mt-5">
        <p className="text-sm font-medium text-gray-700">{t('memoryManager.personalTab.agents')}</p>
        <p className="text-xs text-gray-500 mt-1 mb-2">
          {config.enabled ? t('memoryManager.personalTab.agentsHint') : t('memoryManager.personalTab.agentsOff')}
        </p>
        {/* One card per agent, the way the agent page lays out its tools: the
            whole card is the switch, green while the agent has it on. */}
        <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(14rem, 1fr))' }}
             data-testid="personal-memory-agents">
          {ids.map((id) => {
            const on = config.enabled && !!config.agents[id];
            const locked = saving || !config.enabled;
            const name = byId[id]?.name || id;
            const description = byId[id]?.description || '';
            const flip = () => { if (!locked) save(() => updateAgentPersonalMemory(id, !on, workspace)); };
            return (
              <div
                key={id}
                role="checkbox"
                aria-checked={on}
                aria-disabled={locked || undefined}
                aria-label={name}
                tabIndex={locked ? -1 : 0}
                onClick={flip}
                onKeyDown={(e) => {
                  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); flip(); }
                }}
                className={`group rounded-lg border p-2.5 select-none transition-colors flex flex-col gap-1.5 min-w-0 overflow-hidden min-h-[5.5rem] ${
                  !config.enabled
                    ? 'cursor-not-allowed opacity-60 border-gray-200 bg-gray-50'
                    : on
                      ? 'cursor-pointer border-green-300 bg-green-50 hover:bg-green-100'
                      : 'cursor-pointer border-gray-300 bg-white hover:border-indigo-300 hover:bg-indigo-50/40'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className={`text-sm font-semibold leading-snug break-words line-clamp-2 ${on ? 'text-green-900' : 'text-gray-900'}`} title={id}>
                      {name}
                    </div>
                    <div className={`text-xs leading-4 mt-0.5 break-words line-clamp-3 ${on ? 'text-green-800' : 'text-gray-600'}`} title={description || id}>
                      {description || id}
                    </div>
                  </div>
                  <span className={`mt-0.5 h-5 w-5 shrink-0 rounded-full border flex items-center justify-center text-xs font-bold ${
                    on ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent group-hover:border-indigo-300'
                  }`}>✓</span>
                </div>
                <div className="flex items-center gap-1.5 mt-auto min-w-0" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                  {id === config.main_agent && (
                    <span className="shrink-0 inline-flex items-center gap-1 text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded-full font-medium">
                      <Star className="w-3 h-3" /> {t('memoryManager.personalTab.mainAgent')}
                    </span>
                  )}
                  <Link to={`/agents/${encodeURIComponent(id)}`} className="ml-auto text-[11px] text-indigo-600 hover:text-indigo-800 truncate">
                    {t('memoryManager.personalTab.openAgent')}
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </SectionCard>
  );
}
