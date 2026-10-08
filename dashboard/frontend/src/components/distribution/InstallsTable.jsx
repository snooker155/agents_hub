/**
 * The installs of one channel (Slack or Teams): an organisation that put the
 * bot in its own workspace or tenant, waiting for approval or already using
 * an agent here. Shared by both cards.
 *
 * A bot that belongs to a workspace other than the default has that
 * workspace fixed, so only the agent is chosen. Approving needs both.
 */
import { useState } from 'react';
import { Check, Save, Trash2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';
import { useAgentsOf } from './useAgentsOf';
import { removeInstall, updateInstall } from '../../api/distribution';
import { inputCls } from '../settingsUi';

const btn = 'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50';
const BADGE = {
  pending: 'bg-amber-50 text-amber-700',
  approved: 'bg-emerald-50 text-emerald-700',
};

function Row({ channel, install, fixedWorkspace, workspaces, onChanged }) {
  const { t } = useI18n();
  const toast = useToast();
  const [workspace, setWorkspace] = useState(fixedWorkspace || install.workspace || '');
  const [agentId, setAgentId] = useState(install.agent_id || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const agents = useAgentsOf(workspace);
  const pending = install.status === 'pending';
  const changed = workspace !== (install.workspace || '') || agentId !== (install.agent_id || '');
  const name = install.name || install.org_id;

  const run = async (call, done) => {
    setBusy(true);
    setError('');
    try {
      await call();
      toast.success(done);
      onChanged();
    } catch (err) {
      setError(errorDetail(err) || t('distribution.actionFailed'));
    } finally {
      setBusy(false);
    }
  };

  const approve = () => run(
    () => updateInstall(channel, install.org_id, { status: 'approved', workspace, agent_id: agentId }),
    t('distribution.installs.approved_ok'),
  );
  const save = () => run(
    () => updateInstall(channel, install.org_id, { workspace, agent_id: agentId }),
    t('distribution.installs.saved'),
  );
  const remove = () => {
    if (!window.confirm(t('distribution.installs.confirmRemove', { name }))) return;
    run(() => removeInstall(channel, install.org_id), t('distribution.installs.removed'));
  };

  return (
    <li className="rounded-lg border border-gray-200 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-sm text-gray-900">{name}</span>
        <code className="text-xs bg-gray-100 text-gray-600 rounded px-1">{install.org_id}</code>
        <span className="text-xs text-gray-500">
          {t('distribution.installs.via')}: {install.via === 'catalog' ? t('distribution.installs.viaCatalog') : t('distribution.installs.viaHub')}
        </span>
        <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${BADGE[install.status] || BADGE.pending}`}>
          {pending ? t('distribution.installs.pending') : t('distribution.installs.approved')}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {!fixedWorkspace && (
          <select className={inputCls} value={workspace} aria-label={`${t('distribution.installs.workspace')} ${name}`}
            onChange={(e) => { setWorkspace(e.target.value); setAgentId(''); }}>
            <option value="">{t('distribution.installs.workspace')}</option>
            {workspaces.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
        )}
        <select className={inputCls} value={agentId} aria-label={`${t('distribution.installs.agent')} ${name}`}
          onChange={(e) => setAgentId(e.target.value)}>
          <option value="">{t('distribution.installs.noAgent')}</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
        </select>
      </div>
      {error && <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}
      <div className="flex flex-wrap gap-2">
        {pending && (
          <button type="button" onClick={approve} disabled={busy || !workspace || !agentId}
            className={`${btn} bg-indigo-600 hover:bg-indigo-700 text-white`}>
            <Check className="w-3.5 h-3.5" /> {t('distribution.installs.approve')}
          </button>
        )}
        {!pending && changed && (
          <button type="button" onClick={save} disabled={busy || !workspace || !agentId}
            className={`${btn} bg-indigo-600 hover:bg-indigo-700 text-white`}>
            <Save className="w-3.5 h-3.5" /> {t('distribution.installs.save')}
          </button>
        )}
        <button type="button" onClick={remove} disabled={busy}
          className={`${btn} border border-red-200 text-red-700 hover:bg-red-50`}>
          <Trash2 className="w-3.5 h-3.5" /> {t('distribution.installs.remove')}
        </button>
      </div>
    </li>
  );
}

export default function InstallsTable({ channel, installs, channelWorkspace, workspaces, onChanged }) {
  const { t } = useI18n();
  const fixedWorkspace = channelWorkspace && channelWorkspace !== 'default' ? channelWorkspace : '';
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-800">{t('distribution.installs.title')}</h3>
      {(installs || []).length === 0 ? (
        <p className="text-sm text-gray-500">{t('distribution.installs.empty')}</p>
      ) : (
        <ul className="space-y-2">
          {installs.map((i) => (
            <Row key={i.org_id} channel={channel} install={i} fixedWorkspace={fixedWorkspace}
              workspaces={workspaces} onChanged={onChanged} />
          ))}
        </ul>
      )}
    </div>
  );
}
