import { ArtifactsModeSwitch, ArtifactsPanel, ProcessPanelContent } from './panels';
import { useArtifactsMode } from './artifactsMode';
import { useChatCode } from './useChatCode';
import { FileText, Maximize2, Minimize2, RefreshCw, X } from 'lucide-react';
import { useChatPage } from './context';

// On a phone there is no room beside the conversation: a panel covers the
// whole screen instead, and its X closes it as anywhere else.
const PHONE_SHEET = 'max-sm:fixed max-sm:inset-0 max-sm:z-40 max-sm:min-w-0 max-sm:w-auto max-sm:pt-safe max-sm:pb-safe';

/**
 * The columns beside the transcript, toggled from the top bar: what the turns
 * produced (Artifacts: their views, code and files, in one list), and in the
 * chat view the Process (what the agent's run looked like) as a column of its
 * own to the right of it.
 */
function Column({ width, title, icon: Icon, actions = null, onClose, t, children, testId }) {
  return (
    <div className={`${width} ${PHONE_SHEET} bg-white border-l border-gray-200 flex flex-col min-h-0`} data-testid={testId}>
      <div className="px-4 h-[60px] border-b border-gray-200 flex items-center justify-between shrink-0">
        {typeof title === 'string' ? (
          <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
            {Icon && <Icon className="w-4 h-4 text-indigo-500" />}
            {title}
          </h3>
        ) : title}
        <div className="flex items-center gap-1">
          {actions}
          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
            title={t('chat.closePanel')}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
      {children}
    </div>
  );
}

export default function ChatSidePanel() {
  const {
    activeRunId, agentTopology, artifactPick = null, artifactViews, artifacts, artifactsFull = false, artifactsOpen, currentConv, graphRun,
    loadProcessData, processError, processInsights, processLoading, processOpen, selectedWorkspace,
    setArtifactsFull = () => {}, setArtifactsOpen, setProcessOpen, t, viewMode,
  } = useChatPage();
  const code = useChatCode();
  const workspace = currentConv?.workspace || selectedWorkspace || null;
  const [artifactsMode, setArtifactsMode] = useArtifactsMode();
  // The whole width, in the chat's place (Chat.jsx hides the conversation):
  // nothing else beside it, the Process column included.
  const full = artifactsOpen && artifactsFull;
  const showProcess = processOpen && viewMode !== 'build' && !full;
  const showArtifacts = artifactsOpen;
  if (!showArtifacts && !showProcess) return null;

  const processHeader = (
    <div className="px-4 h-[60px] border-b border-gray-200 flex items-center justify-between shrink-0">
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
    </div>
  );
  const processBody = !activeRunId ? (
    <div className="p-4 text-sm text-gray-500">{t('chat.sendAMessageThenOpen')}</div>
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
      workspace={workspace}
    />
  );

  return (
    <>
      {/* Beside the chat, 15% narrower under 1800 CSS px: a narrow window, and
          a laptop's high-resolution screen too (a 13–14" one is 1440–1512 CSS
          px wide, a 16" MacBook 1728): a smaller share of the room beside the
          transcript and a smaller floor; the full share on a monitor (1920
          on). Or, when asked, the whole width in the chat's place. */}
      {showArtifacts && (
        <Column
          testId="chat-artifacts-column"
          width={full ? 'flex-1 min-w-0' : 'flex-[0.85_1_0%] min-w-[391px] min-[1800px]:flex-1 min-[1800px]:min-w-[460px]'}
          title={t('chat.artifacts')} icon={FileText} t={t}
          actions={(
            <>
              <ArtifactsModeSwitch mode={artifactsMode} onChange={setArtifactsMode} t={t} />
              {/* A phone shows the column over everything already. */}
              <button
                type="button"
                onClick={() => setArtifactsFull((v) => !v)}
                className="max-sm:hidden p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
                title={full ? t('chat.artifactsBeside') : t('chat.artifactsFull')}
                aria-label={full ? t('chat.artifactsBeside') : t('chat.artifactsFull')}
                aria-pressed={full}
                data-testid="artifacts-full-toggle"
              >
                {full ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
              </button>
            </>
          )}
          // Closing keeps the way it was shown: it opens that way next time.
          onClose={() => setArtifactsOpen(false)}
        >
          <ArtifactsPanel artifacts={artifacts} views={artifactViews || []} workspace={workspace} mode={artifactsMode} code={code} pick={artifactPick} />
        </Column>
      )}
      {showProcess && (
        <div className={`w-[400px] flex-shrink-0 ${PHONE_SHEET} bg-white border-l border-gray-200 flex flex-col min-h-0`} data-testid="chat-process-column">
          {processHeader}
          <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">{processBody}</div>
        </div>
      )}
    </>
  );
}
