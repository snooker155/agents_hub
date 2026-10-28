/**
 * The one modal that hands a task to whoever runs it: an agent, a flow, a team
 * or a loop, each picked from a searchable list. The task board's cards and the
 * task page (the task itself and each subtask) open the same one.
 *
 * It loads its own lists for the task's workspace, and the workspace's
 * assignment mode: with "nodes only" an agent without a running node is shown
 * but cannot be picked. Every kind is assigned through the task's assign route
 * (an agent by id, the others as an executor).
 */
import { useEffect, useState } from 'react';
import { RotateCw, User, Users, Workflow, X } from 'lucide-react';
import SearchSelect from '../SearchSelect';
import { assignAgent, getAgents, getLoops, getSettings, getTeams, listFlows } from '../../api';
import { useI18n } from '../../i18n';

const KINDS = [
  { kind: 'agent', Icon: User },
  { kind: 'flow', Icon: Workflow },
  { kind: 'team', Icon: Users },
  { kind: 'loop', Icon: RotateCw },
];

// Each kind's strings, under taskBoard.
const LABELS = {
  agent: { tab: 'agent', select: 'selectAgent', choose: 'chooseAnAgent', search: 'searchAgents', none: 'noAgents' },
  flow: { tab: 'flow', select: 'selectFlow', choose: 'chooseAFlow', search: 'searchFlows', none: 'noFlows' },
  team: { tab: 'team', select: 'selectTeam', choose: 'chooseATeam', search: 'searchTeams', none: 'noTeams' },
  loop: { tab: 'loop', select: 'selectLoop', choose: 'chooseALoop', search: 'searchLoops', none: 'noLoops' },
};

const subtitleOf = (id, name, description) =>
  [name && name !== id ? id : null, description].filter(Boolean).join(' · ');

/**
 * @param {object} props
 * @param {object} props.task       the task to assign ({id, title, workspace}).
 * @param {function} props.onClose
 * @param {function} props.onAssigned  called with the assign response's data.
 */
export default function AssignExecutorModal({ task, onClose, onAssigned }) {
  const { t } = useI18n();
  const [kind, setKind] = useState('agent');
  const [selected, setSelected] = useState({ agent: '', flow: '', team: '', loop: '' });
  const [catalogs, setCatalogs] = useState(null);
  const [mode, setMode] = useState('any');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const ws = task.workspace || undefined;
    Promise.all([
      getAgents(ws).catch(() => ({ data: [] })),
      listFlows(ws).catch(() => ({ data: [] })),
      getTeams(ws).catch(() => ({ data: { teams: [] } })),
      getLoops(ws).catch(() => ({ data: { loops: [] } })),
      getSettings().catch(() => ({ data: {} })),
    ]).then(([agents, flows, teams, loops, settings]) => {
      if (cancelled) return;
      setCatalogs({
        agent: Array.isArray(agents.data) ? agents.data : (agents.data?.items || []),
        flow: flows.data || [],
        team: teams.data?.teams || [],
        loop: loops.data?.loops || [],
      });
      setMode(settings.data?.task_assignment_mode || 'any');
    });
    return () => { cancelled = true; };
  }, [task.workspace]);

  const nodesOnly = mode === 'nodes_only';
  const items = (() => {
    if (!catalogs) return [];
    if (kind === 'agent') {
      return catalogs.agent.map((a) => ({
        id: a.id,
        name: a.name || a.id,
        subtitle: subtitleOf(a.id, a.name, a.description),
        ...(nodesOnly ? {
          dot: a.has_running_node ? 'bg-green-500' : 'bg-gray-300',
          disabled: !a.has_running_node,
          hint: a.has_running_node ? undefined : t('taskDetails.noRunningNodeHint'),
        } : {}),
      }));
    }
    const idKey = { flow: 'id', team: 'team_id', loop: 'loop_id' }[kind];
    return catalogs[kind].map((x) => ({
      id: x[idKey],
      name: x.name || x[idKey],
      subtitle: subtitleOf(x[idKey], x.name, x.description || x.goal),
    }));
  })();
  const labels = LABELS[kind];
  const selection = selected[kind];

  const assign = async () => {
    if (!selection) return;
    setLoading(true);
    setError('');
    try {
      const body = kind === 'agent' ? { agent_id: selection } : { executor: { kind, id: selection } };
      const resp = await assignAgent(task.id, body);
      onAssigned(resp.data || {});
      onClose();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  const tabClass = (id) => `flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md text-sm font-medium transition-colors ${
    kind === id ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
  }`;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-md w-full p-6 shadow-xl" data-testid="assign-executor-modal">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">{t('taskBoard.assignToTask')}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('taskBoard.cancel')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <p className="text-sm text-gray-600 mb-4 truncate">{t('taskBoard.task')} <span className="font-medium">{task.title}</span></p>

        <div className="flex items-center bg-gray-100 rounded-lg p-1 mb-4" role="tablist">
          {KINDS.map(({ kind: id, Icon }) => (
            <button key={id} type="button" role="tab" aria-selected={kind === id}
              onClick={() => { setKind(id); setError(''); }} className={tabClass(id)}>
              <Icon className="w-4 h-4" />
              {t(`taskBoard.${LABELS[id].tab}`)}
            </button>
          ))}
        </div>

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">{t(`taskBoard.${labels.select}`)}</label>
          <SearchSelect
            key={kind}
            testId={`assign-${kind}-picker`}
            items={items}
            value={selection}
            onChange={(id) => setSelected((s) => ({ ...s, [kind]: id }))}
            placeholder={catalogs ? t(`taskBoard.${labels.choose}`) : '…'}
            searchPlaceholder={t(`taskBoard.${labels.search}`)}
            noMatchText={t('taskBoard.noMatch')}
            emptyText={catalogs ? t(`taskBoard.${labels.none}`) : '…'}
          />
          {kind === 'agent' && nodesOnly && (
            <p className="text-xs text-amber-600 mt-1">{t('taskDetails.nodesOnlyModeAgentsWithout')}</p>
          )}
          {kind === 'flow' && catalogs && catalogs.flow.length === 0 && (
            <p className="text-xs text-gray-400 mt-1">{t('taskBoard.noFlowsAuthorizedForThis')}</p>
          )}
        </div>

        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">{t('taskBoard.cancel')}</button>
          <button
            onClick={assign}
            disabled={!selection || loading}
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? t('taskBoard.assigning') : t('taskBoard.assign')}
          </button>
        </div>
      </div>
    </div>
  );
}
