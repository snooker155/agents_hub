import { useEffect, useState } from 'react';
import { ArtifactsPanel, ProcessPanelContent } from './panels';
import CodePanel from './CodePanel';
import { Code2, FileText, RefreshCw, X } from 'lucide-react';
import { useChatPage } from './context';

// On a phone there is no room beside the conversation: a panel covers the
// whole screen instead, and its X closes it as anywhere else.
const PHONE_SHEET = 'max-sm:fixed max-sm:inset-0 max-sm:z-40 max-sm:min-w-0 max-sm:w-auto max-sm:pt-safe max-sm:pb-safe';

/**
 * The columns beside the transcript, toggled from the top bar: the files and
 * views the turns produced (Artifacts) or the code they wrote (Code), one at
 * a time, and in the chat view the Process (what the agent's run looked like)
 * as a column of its own to the right of it.
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

const ARTIFACTS_MODE_KEY = 'agent_hub_chat_artifacts_mode';

/** Files (what each file holds now) or Diff (what the runs changed). */
function ArtifactsModeSwitch({ mode, onChange, t }) {
  return (
    <div className="inline-flex rounded-md border border-gray-200 overflow-hidden text-xs" role="tablist" aria-label={t('chat.artifactModes.label')}>
      {[['content', t('chat.artifactModes.files')], ['diff', t('chat.artifactModes.diff')]].map(([key, label], i) => (
        <button key={key} type="button" role="tab" aria-selected={mode === key} onClick={() => onChange(key)}
          className={`px-2 py-0.5 ${i ? 'border-l border-gray-200' : ''} ${
            mode === key ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

export default function ChatSidePanel() {
  const {
    activeRunId, agentTopology, artifactViews, artifacts, artifactsOpen, codeOpen, currentConv, graphRun,
    loadProcessData, processError, processInsights, processLoading, processOpen, selectedWorkspace,
    setArtifactsOpen, setCodeOpen, setProcessOpen, t, viewMode,
  } = useChatPage();
  const workspace = currentConv?.workspace || selectedWorkspace || null;
  // Content unless the person picked Diff; the pick survives leaving the page.
  const [artifactsMode, setArtifactsMode] = useState(() => {
    try { return localStorage.getItem(ARTIFACTS_MODE_KEY) === 'diff' ? 'diff' : 'content'; } catch { return 'content'; }
  });
  useEffect(() => {
    try { localStorage.setItem(ARTIFACTS_MODE_KEY, artifactsMode); } catch { /* storage unavailable */ }
  }, [artifactsMode]);
  const showProcess = processOpen && viewMode !== 'build';
  // One panel at a time (the top bar closes one when opening the other); a
  // stale pair from an older saved state resolves to Code.
  const showCode = codeOpen;
  const showArtifacts = artifactsOpen && !codeOpen;
  if (!showArtifacts && !showCode && !showProcess) return null;

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
      {showArtifacts && (
        <Column
          testId="chat-artifacts-column"
          width="flex-1 min-w-[460px]"
          title={t('chat.artifacts')} icon={FileText} t={t}
          actions={<ArtifactsModeSwitch mode={artifactsMode} onChange={setArtifactsMode} t={t} />}
          onClose={() => setArtifactsOpen(false)}
        >
          <ArtifactsPanel artifacts={artifacts} views={artifactViews || []} workspace={workspace} mode={artifactsMode} />
        </Column>
      )}
      {showCode && (
        <Column
          testId="chat-code-column"
          width="flex-1 min-w-[460px]"
          title={t('chat.code.panelTitle')} icon={Code2} t={t}
          onClose={() => setCodeOpen(false)}
        >
          <CodePanel />
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
