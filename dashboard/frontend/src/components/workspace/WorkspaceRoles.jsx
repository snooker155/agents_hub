import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Loader } from 'lucide-react';
import { getAgents, getWorkspaceRoles, updateWorkspaceRole } from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

/**
 * Which agent does each kind of work in this workspace (agents/roles.py).
 * System agents hand code to `@coder`, reviews to `@reviewer` and so on; a
 * role is held by the product's own agent until the workspace gives it to
 * another of its agents, an imported Claude Code, Codex or Aider for
 * example. Saves itself on each change, like the personal memory card.
 */
export default function WorkspaceRoles({ workspace }) {
  const { t } = useI18n();
  const toast = useToast();
  const [rows, setRows] = useState(null);
  const [agents, setAgents] = useState([]);
  const [saving, setSaving] = useState('');

  const load = useCallback(() => {
    getWorkspaceRoles(workspace)
      .then(({ data }) => setRows(data.roles || []))
      .catch((e) => toast.error(t('workspaceDetails.roles.loadFailed'), errorDetail(e)));
  }, [workspace, t, toast]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getAgents(workspace)
      .then(({ data }) => setAgents((data || []).map((a) => ({ id: a.id, name: a.name || a.id }))))
      .catch(() => setAgents([]));
  }, [workspace]);

  const change = async (role, agentId) => {
    setSaving(role);
    try {
      const { data } = await updateWorkspaceRole(workspace, role, agentId);
      setRows(data.roles || []);
      toast.success(t('workspaceDetails.roles.saved'));
    } catch (e) {
      toast.error(t('workspaceDetails.roles.saveFailed'), errorDetail(e));
      load();
    } finally {
      setSaving('');
    }
  };

  if (!rows) {
    return (
      <p className="text-sm text-gray-500 flex items-center gap-2">
        <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
      </p>
    );
  }

  return (
    <SectionCard title={t('workspaceDetails.roles.title')}>
      <p className="text-sm text-gray-500">{t('workspaceDetails.roles.intro')}</p>
      <p className="text-xs text-gray-500">
        {t('workspaceDetails.roles.importHint')}{' '}
        <Link to="/agents" className="text-indigo-600 hover:text-indigo-800">{t('workspaceDetails.roles.openAgents')}</Link>
      </p>
      <div className="divide-y divide-gray-100 rounded-lg border border-gray-200" data-testid="workspace-roles">
        {rows.map((row) => {
          // The role's default first, then every other agent of the workspace.
          const options = [
            { id: row.default, name: row.default_name || row.default },
            ...agents.filter((a) => a.id !== row.default),
          ];
          if (row.agent && !options.some((o) => o.id === row.agent)) {
            options.push({ id: row.agent, name: row.agent_name || row.agent });
          }
          return (
            <div key={row.role} className="flex flex-col gap-2 px-3 py-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0 md:max-w-[55%]">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-800">{t(`workspaceDetails.roles.names.${row.role}`)}</span>
                  <code className="text-[11px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{row.ref}</code>
                </div>
                <p className="text-xs text-gray-500 mt-1">{t(`workspaceDetails.roles.summaries.${row.role}`)}</p>
                <p className="text-[11px] text-gray-400 mt-1">
                  {row.callers.length
                    ? t('workspaceDetails.roles.calledBy', { agents: row.callers.join(', ') })
                    : t('workspaceDetails.roles.noCallers')}
                </p>
                {row.stale && (
                  <p className="mt-1 flex items-center gap-1 text-[11px] text-amber-700">
                    <AlertTriangle className="w-3 h-3 shrink-0" />
                    {t('workspaceDetails.roles.stale', { agent: row.bound, fallback: row.default })}
                  </p>
                )}
              </div>
              <div className="md:w-64 shrink-0">
                <select
                  aria-label={t(`workspaceDetails.roles.names.${row.role}`)}
                  value={row.agent || row.default}
                  disabled={saving === row.role}
                  onChange={(e) => change(row.role, e.target.value === row.default ? '' : e.target.value)}
                  className={`${inputCls} bg-white`}
                >
                  {options.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.id === row.default ? t('workspaceDetails.roles.defaultOption', { name: o.name }) : o.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}
