/**
 * Modal that assigns an agent, flow, team or loop to a task.
 */
import { Check, RotateCw, User, Users, Workflow, X } from 'lucide-react';
import { useI18n } from '../../i18n';

export function TaskAssignModal({ agents, assignTarget, catalogsLoaded, executorKind, flows, handleAssignAgent, loops, selectedAgent, selectedFlow, selectedLoop, selectedTeam, setAssignTarget, setExecutorKind, setSelectedAgent, setSelectedFlow, setSelectedLoop, setSelectedTeam, setShowAssignModal, taskAssignmentMode, teams }) {
  const { t } = useI18n();
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-xl max-w-lg w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">
            {assignTarget ? t('taskDetails.assignAgentTo', { title: assignTarget.title }) : t('taskDetails.assignAgentToTask')}
          </h3>
          <button onClick={() => { setShowAssignModal(false); setAssignTarget(null); }} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Executor kind: agent / flow / team / loop */}
        <div className="flex items-center bg-gray-100 rounded-lg p-1 mb-3">
          {[
            { kind: 'agent', Icon: User, label: t('taskDetails.assignAgent') },
            { kind: 'flow', Icon: Workflow, label: t('taskBoard.flow') },
            { kind: 'team', Icon: Users, label: 'Team' },
            { kind: 'loop', Icon: RotateCw, label: 'Loop' },
          ].map(({ kind, Icon, label }) => (
            <button
              key={kind}
              type="button"
              onClick={() => setExecutorKind(kind)}
              className={`flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-md text-xs font-medium transition-colors ${
                executorKind === kind ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />{label}
            </button>
          ))}
        </div>

        {executorKind === 'agent' && (
          <div className="mb-2 flex items-center gap-3 text-xs text-gray-400">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" /> {t('taskDetails.nodeRunning')}</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-gray-300 inline-block" /> {t('taskDetails.noNode')}</span>
            {taskAssignmentMode === 'nodes_only' && (
              <span className="ml-auto text-amber-600 font-medium">{t('taskDetails.nodesOnlyModeAgentsWithout')}</span>
            )}
          </div>
        )}

        {executorKind === 'agent' && (
          <div className="mb-5 grid grid-cols-1 gap-2 max-h-72 overflow-y-auto pr-1">
            {agents.length === 0 && (
              <p className="text-sm text-gray-400 italic py-2">{t('taskDetails.noAgentsAvailableForThis')}</p>
            )}
            {agents.map(a => {
              const hasNode = a.has_running_node;
              const disabled = taskAssignmentMode === 'nodes_only' && !hasNode;
              const selected = selectedAgent === a.id;
              return (
                <button
                  key={a.id}
                  onClick={() => !disabled && setSelectedAgent(a.id)}
                  disabled={disabled}
                  title={disabled ? t('taskDetails.noRunningNodeHint') : undefined}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-all ${
                    disabled
                      ? 'border-gray-100 bg-gray-50 opacity-40 cursor-not-allowed'
                      : selected
                        ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-400'
                        : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-indigo-50'
                  }`}
                >
                  <span className={`flex-shrink-0 w-2.5 h-2.5 rounded-full mt-0.5 ${hasNode ? 'bg-green-500' : 'bg-gray-300'}`} />
                  <span className="flex-1 min-w-0">
                    <span className={`block text-sm font-medium ${selected ? 'text-indigo-700' : 'text-gray-800'}`}>
                      {a.name}
                    </span>
                    <span className="block text-xs text-gray-400 truncate">{a.id}{!hasNode && ` · ${t('taskDetails.noRunningNode')}`}</span>
                  </span>
                  {selected && <Check className="w-4 h-4 text-indigo-600 flex-shrink-0" />}
                </button>
              );
            })}
          </div>
        )}

        {executorKind !== 'agent' && (() => {
          const catalog = { flow: flows, team: teams, loop: loops }[executorKind];
          const idKey = { flow: 'id', team: 'team_id', loop: 'loop_id' }[executorKind];
          const nameKey = 'name';
          const selected = { flow: selectedFlow, team: selectedTeam, loop: selectedLoop }[executorKind];
          const setSelected = { flow: setSelectedFlow, team: setSelectedTeam, loop: setSelectedLoop }[executorKind];
          return (
            <div className="mb-5 grid grid-cols-1 gap-2 max-h-72 overflow-y-auto pr-1">
              {catalog.length === 0 && (
                <p className="text-sm text-gray-400 italic py-2">
                  {catalogsLoaded ? t('taskDetails.noAgentsAvailableForThis') : '…'}
                </p>
              )}
              {catalog.map(item => {
                const itemId = item[idKey];
                const isSelected = selected === itemId;
                return (
                  <button
                    key={itemId}
                    onClick={() => setSelected(itemId)}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-all ${
                      isSelected
                        ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-400'
                        : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-indigo-50'
                    }`}
                  >
                    <span className="flex-1 min-w-0">
                      <span className={`block text-sm font-medium ${isSelected ? 'text-indigo-700' : 'text-gray-800'}`}>
                        {item[nameKey] || itemId}
                      </span>
                      <span className="block text-xs text-gray-400 truncate">{itemId}</span>
                    </span>
                    {isSelected && <Check className="w-4 h-4 text-indigo-600 flex-shrink-0" />}
                  </button>
                );
              })}
            </div>
          );
        })()}

        <div className="flex justify-end gap-3">
          <button
            onClick={() => { setShowAssignModal(false); setAssignTarget(null); }}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >{t('taskDetails.cancel')}</button>
          <button
            onClick={handleAssignAgent}
            disabled={!{ agent: selectedAgent, flow: selectedFlow, team: selectedTeam, loop: selectedLoop }[executorKind]}
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >{t('taskDetails.startExecution')}</button>
        </div>
      </div>
    </div>
  );
}
