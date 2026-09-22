import { ArtifactsPanel, ProcessPanelContent } from './panels';
import { FileText, RefreshCw, X } from 'lucide-react';
import { useChatPage } from './context';

/**
 * The panel beside the transcript: what the agent's run looked like, and the
 * files it changed.
 */
export default function ChatSidePanel() {
  const {
    activeRunId, agentTopology, artifacts, graphRun, loadProcessData, processError,
    processInsights, processLoading, processOpen, setProcessOpen, t, viewMode,
  } = useChatPage();
  return (
    <>
      {/* ── Side panel ──
          Build view: Artifacts only, always open (file diffs live here; steps and
          tool calls are shown inline in the transcript, so there is no Process tab).
          Chat view: the legacy Agent Process panel, toggled by "Show process". */}
      {(() => {
        const isBuild = viewMode === 'build';
        const panelVisible = isBuild || processOpen;
        if (!panelVisible) return null;
        return (
          <div className={`${isBuild ? 'w-[560px]' : 'w-[420px]'} flex-shrink-0 bg-white border-l border-gray-200 flex flex-col`}>
            <div className="px-4 h-[60px] border-b border-gray-200 flex items-center justify-between">
              {isBuild ? (
                <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                  <FileText className="w-4 h-4 text-indigo-500" />
                  {t('chat.artifacts')}
                </h3>
              ) : (
                <div>
                  <h3 className="text-sm font-semibold text-gray-800">{t('chat.agentProcess')}</h3>
                  {processInsights?.session_id && (
                    <p className="text-[10px] text-gray-500 -mb-0.5">
                      Session:{' '}
                      <a
                        href={`/sessions/${processInsights.session_id}`}
                        className="text-indigo-600 hover:text-indigo-700 hover:underline"
                      >
                        {processInsights.session_id}
                      </a>
                    </p>
                  )}
                </div>
              )}
              {!isBuild && (
                <div className="flex items-center gap-1">
                  {activeRunId && (
                    <button
                      onClick={() => loadProcessData(activeRunId)}
                      className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded"
                      title={t('chat.refreshProcess')}
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    onClick={() => setProcessOpen(false)}
                    className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
                    title={t('chat.closePanel')}
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>

            {isBuild ? (
              <ArtifactsPanel artifacts={artifacts} />
            ) : !activeRunId ? (
              <div className="p-4 text-sm text-gray-500">
                {t('chat.sendAMessageThenOpen')}
              </div>
            ) : processLoading ? (
              <div className="flex items-center gap-2 text-sm text-gray-500 p-4">
                <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />
                {t('chat.loadingProcessDetails')}
              </div>
            ) : processError ? (
              <div className="p-4 text-sm text-red-600">{processError}</div>
            ) : (
              <ProcessPanelContent
                processInsights={processInsights}
                topology={agentTopology}
                graphRun={graphRun}
              />
            )}
          </div>
        );
      })()}
    </>
  );
}
