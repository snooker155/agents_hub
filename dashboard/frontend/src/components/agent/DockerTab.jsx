import { Activity, AlertCircle, FileCode, Layers, Loader, Play, Server } from 'lucide-react';
import { useAgentPage } from './context';

/** The images and containers this agent runs in. */
export default function DockerTab() {
  const {
    buildError, buildLog, buildingAgent, buildingBase, dockerActionBusy, dockerContainers,
    dockerImages, dockerLoading, dockerfileContent, dockerfileLoading, fetchDockerData,
    handleBuildAgent, handleBuildBase, handleRemoveContainer, handleShowContainerLogs,
    handleStopContainer, id, t,
  } = useAgentPage();
  return (
        <div className="space-y-6">
          {/* Image status + build actions */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                <Layers className="w-4 h-4 text-indigo-500" /> {t('agentDetails.dockerImages')}
              </h3>
              <button
                onClick={fetchDockerData}
                disabled={dockerLoading}
                className="text-xs text-gray-500 hover:text-gray-800 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50 flex items-center gap-1 disabled:opacity-40"
              >
                {dockerLoading ? <Loader className="w-3 h-3 animate-spin" /> : <Activity className="w-3 h-3" />}
                Refresh
              </button>
            </div>

            {/* Image rows */}
            <div className="space-y-2 mb-5">
              {[
                { label: t('agentDetails.baseImage'), tag: 'agents-hub/base:latest' },
                { label: t('agentDetails.agentImage', { id }), tag: `agents-hub/${id}:latest` },
              ].map(({ label, tag }) => {
                const exists = dockerImages.some(img => `${img.repository}:${img.tag}` === tag || img.repository === tag.split(':')[0]);
                return (
                  <div key={tag} className="flex items-center justify-between px-4 py-3 rounded-lg border border-gray-200 bg-gray-50">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-800">{label}</p>
                      <p className="text-xs text-gray-400 font-mono mt-0.5">{tag}</p>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold ${exists ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${exists ? 'bg-green-500' : 'bg-gray-400'}`} />
                      {exists ? t('agentDetails.built') : t('agentDetails.notBuilt')}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Build buttons */}
            <div className="flex flex-wrap gap-3">
              <button
                onClick={handleBuildBase}
                disabled={buildingBase || buildingAgent}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-gray-700 rounded-lg hover:bg-gray-800 disabled:opacity-50"
              >
                {buildingBase ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {buildingBase ? t('agentDetails.buildingBase') : t('agentDetails.buildBaseImage')}
              </button>
              <button
                onClick={handleBuildAgent}
                disabled={buildingBase || buildingAgent}
                className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {buildingAgent ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {buildingAgent ? t('agentDetails.building') : t('agentDetails.buildAgentImage')}
              </button>
            </div>

            {/* Build output */}
            {(buildLog || buildError) && (
              <div className="mt-4">
                {buildError && (
                  <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-2">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />{buildError}
                  </div>
                )}
                {buildLog && (
                  <pre className="text-xs bg-gray-900 text-green-300 rounded-lg p-4 overflow-auto max-h-48 whitespace-pre-wrap">{buildLog}</pre>
                )}
              </div>
            )}
          </div>

          {/* Dockerfile preview */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-3">
              <FileCode className="w-4 h-4 text-indigo-500" /> {t('agentDetails.dockerfile')}
            </h3>
            <p className="text-xs text-gray-500 mb-3">
              {t('agentDetails.dockerfileHintBefore')} <code className="text-indigo-600">agents-hub/base:latest</code> {t('agentDetails.dockerfileHintAfter')}{' '}
              <code className="text-indigo-600">agents/state/dockerfiles/{id}.Dockerfile</code>.
            </p>
            {dockerfileLoading ? (
              <div className="flex justify-center py-8"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
            ) : dockerfileContent ? (
              <pre className="text-xs bg-gray-900 text-green-300 rounded-lg p-4 overflow-auto max-h-72 whitespace-pre-wrap">{dockerfileContent}</pre>
            ) : (
              <p className="text-sm text-gray-400 italic">{t('agentDetails.dockerfilePreviewUnavailable')}</p>
            )}
          </div>

          {/* Running containers */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <Server className="w-4 h-4 text-indigo-500" /> {t('agentDetails.containers')}
              <span className="text-xs text-gray-400 font-normal">({t('agentDetails.forThisAgent')})</span>
            </h3>
            {dockerLoading ? (
              <div className="flex justify-center py-6"><Loader className="w-5 h-5 animate-spin text-indigo-400" /></div>
            ) : dockerContainers.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <Server className="w-10 h-10 mx-auto mb-3 opacity-20" />
                <p className="text-sm">{t('agentDetails.noContainersFoundForThis')}</p>
                <p className="text-xs mt-1">{t('agentDetails.startANodeInDocker')}</p>
              </div>
            ) : (
              <div className="space-y-2">
                {dockerContainers.map(c => {
                  const isRunning = c.state === 'running';
                  const busy = dockerActionBusy[c.name];
                  return (
                    <div key={c.id || c.name} className="flex items-center gap-3 px-4 py-3 border border-gray-200 rounded-lg">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-mono font-medium text-gray-800 truncate">{c.name}</p>
                        <div className="flex items-center gap-3 text-xs text-gray-400 mt-0.5">
                          <span>{c.status || c.state}</span>
                          {c.image && <span className="truncate font-mono">{c.image}</span>}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          onClick={() => handleShowContainerLogs(c.name)}
                          className="text-xs text-gray-500 hover:text-gray-800 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50"
                        >
                          {t('agentDetails.logs')}
                        </button>
                        {isRunning && (
                          <button
                            onClick={() => handleStopContainer(c.name)}
                            disabled={busy}
                            className="text-xs text-orange-600 hover:text-orange-800 border border-orange-200 px-2 py-1 rounded-lg hover:bg-orange-50 disabled:opacity-40"
                          >
                            {busy ? <Loader className="w-3 h-3 animate-spin inline" /> : 'Stop'}
                          </button>
                        )}
                        {!isRunning && (
                          <button
                            onClick={() => handleRemoveContainer(c.name)}
                            disabled={busy}
                            className="text-xs text-red-600 hover:text-red-800 border border-red-200 px-2 py-1 rounded-lg hover:bg-red-50 disabled:opacity-40"
                          >
                            {busy ? <Loader className="w-3 h-3 animate-spin inline" /> : 'Remove'}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
  );
}
