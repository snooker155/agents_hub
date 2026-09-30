import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { UserRound } from 'lucide-react';
import { getAgentPersonalMemory, updateAgentPersonalMemory } from '../../api';
import { errorDetail } from '../toast';

/**
 * Personal memory (memory/personal.py): memory about the user, one private
 * pool per user and workspace, shared by every agent that has it on there.
 * On or off per agent and workspace; the workspace's main agent starts on.
 * An agent with a pool of its own gets both: its own stays the primary one.
 * A workspace with personal memory off has it off for every agent, and the
 * switch is locked until the workspace settings turn it back on.
 */
export default function PersonalMemoryCard({ agentId, workspace, t, toast, onChange }) {
  const [config, setConfig] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    getAgentPersonalMemory(agentId, workspace)
      .then(({ data }) => { if (alive) setConfig(data); })
      .catch(() => { if (alive) setConfig(null); });
    return () => { alive = false; };
  }, [agentId, workspace]);

  if (!config) return null;
  const locked = !config.workspace_enabled;
  const on = config.enabled && !locked;
  const active = config.effective;

  const choose = async (value) => {
    if (value === on || locked) return;
    setSaving(true);
    try {
      const { data } = await updateAgentPersonalMemory(agentId, value, workspace);
      setConfig(data);
      onChange?.(data);
    } catch (e) {
      toast?.error(t('agentDetails.personalMemory.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${active ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}
      data-testid="personal-memory-card"
    >
      <div className="min-w-0">
        <div className="text-sm font-semibold text-gray-900 flex items-center gap-1.5">
          <UserRound className="w-4 h-4 text-indigo-500" /> {t('agentDetails.personalMemory.title')}
        </div>
        <div className="text-xs text-gray-500 mt-0.5">
          {t('agentDetails.personalMemory.hint')}
          {config.is_main_agent && <> {t('agentDetails.personalMemory.mainAgent')}</>}
          {locked ? (
            <>
              {' '}{t('agentDetails.personalMemory.workspaceOff')}{' '}
              <Link
                to={`/workspaces/${encodeURIComponent(config.workspace)}?tab=settings&section=personalMemory`}
                className="text-indigo-600 hover:text-indigo-800"
              >
                {t('agentDetails.personalMemory.openWorkspaceSettings')}
              </Link>
            </>
          ) : config.effective && config.has_own_pool ? (
            <> {t('agentDetails.personalMemory.ownPool')}</>
          ) : null}
        </div>
      </div>
      <div
        className={`inline-flex rounded-lg border border-gray-300 overflow-hidden shrink-0 ${locked ? 'opacity-50' : ''}`}
        title={locked ? t('agentDetails.personalMemory.workspaceOff') : undefined}
      >
        {[true, false].map((value) => (
          <button
            key={String(value)}
            type="button"
            disabled={saving || locked}
            onClick={() => choose(value)}
            aria-pressed={on === value}
            className={`px-2.5 py-1 text-xs font-semibold disabled:cursor-not-allowed ${
              on === value ? 'bg-indigo-600 text-white' : 'bg-white text-gray-500 hover:bg-gray-50'
            }`}
          >
            {value ? t('agentDetails.personalMemory.modes.on') : t('agentDetails.personalMemory.modes.off')}
          </button>
        ))}
      </div>
    </div>
  );
}
