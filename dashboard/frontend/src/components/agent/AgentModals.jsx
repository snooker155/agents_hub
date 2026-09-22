import { PageContainer } from '../PageLayout';
import NodeStatusBadge from './NodeStatusBadge';
import { Loader, Play, X } from 'lucide-react';
import { useAgentPage } from './context';

/**
 * The page's three overlays: a container's logs, the start-a-node form and a node's logs. Kept together because they are all 'something opened on top of whichever tab you were on', not part of any one tab.
 */
export default function AgentModals() {
  const {
    agent, containerLogsLoading, containerLogsName, containerLogsText, handleStartNode, id,
    logsNode, logsNodeLoading, logsNodeText, setContainerLogsName, setLogsNode,
    setShowStartNodeModal, setStartLabel, setStartWorkspace, showStartNodeModal,
    startLabel, startWorkspace, startingNode, t, workspaces,
  } = useAgentPage();
  return (
    <>

      {/* Container logs modal */}
      {containerLogsName && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <span className="text-gray-200 text-sm font-semibold font-mono">{containerLogsName}</span>
              <button onClick={() => setContainerLogsName(null)} className="text-gray-500 hover:text-gray-300">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              {containerLogsLoading ? (
                <div className="flex justify-center py-12"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
              ) : (
                <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{containerLogsText || '(no output)'}</pre>
              )}
            </div>
          </div>
        </div>
      )}

      {showStartNodeModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
            <div className="flex items-center justify-between p-5 border-b">
              <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
                <Play className="w-4 h-4 text-indigo-600" />
                {t('agentDetails.startNode')}
              </h2>
              <button onClick={() => setShowStartNodeModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  {t('agentDetails.workspace')}
                </label>
                <select
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  value={startWorkspace}
                  onChange={(e) => setStartWorkspace(e.target.value)}
                >
                  <option value="">{t('agentDetails.none2')}</option>
                  {workspaces.map((ws) => (
                    <option key={ws.name} value={ws.name}>{ws.label || ws.id || ws.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
                  {t('agentDetails.label')} <span className="text-gray-400 font-normal normal-case">({t('common.optional')})</span>
                </label>
                <input
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder={t('agentDetails.eGDevWorker')}
                  value={startLabel}
                  onChange={(e) => setStartLabel(e.target.value)}
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowStartNodeModal(false)}
                  className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200"
                  disabled={startingNode}
                >
                  {t('agentDetails.cancel')}
                </button>
                <button
                  type="button"
                  disabled={startingNode}
                  onClick={handleStartNode}
                  className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                >
                  {startingNode ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  {startingNode ? t('agentDetails.starting') : t('agentDetails.startNode')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {logsNode && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <div className="flex items-center gap-3">
                <NodeStatusBadge status={logsNode.status} />
                <span className="text-gray-200 text-sm font-semibold">
                  {logsNode.agent_name || logsNode.agent_id || agent?.name || id}
                </span>
                <span className="text-gray-500 text-xs">
                  {(logsNode.node_id || logsNode.id || '').toString().slice(0, 12)}…
                </span>
              </div>
              <button onClick={() => setLogsNode(null)} className="text-gray-500 hover:text-gray-300 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              {logsNodeLoading ? (
                <div className="flex justify-center py-12">
                  <Loader className="w-5 h-5 animate-spin text-indigo-400" />
                </div>
              ) : (
                <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logsNodeText}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
