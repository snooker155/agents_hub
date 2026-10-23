import { ArtifactsPanel } from '../chat/panels';
import { useArtifactsMode } from '../chat/artifactsMode';

/**
 * A flow run page's Artifacts tab: the files and views the conversation's
 * nodes produced, the whole centre pane wide, with no header of its own; the
 * Chat page's Files / Diff switch sits on the selected file's line.
 * `artifacts` is by path (chat/artifactOps.js conversationArtifacts),
 * `views` as chat/turnViews.js lists them.
 */
export default function FlowArtifacts({ artifacts = {}, views = [], workspace = null, picked = null, onPick = null }) {
  const [mode, setMode] = useArtifactsMode();
  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col bg-white" data-testid="flow-artifacts">
      <ArtifactsPanel
        artifacts={artifacts} views={views} workspace={workspace} mode={mode} onModeChange={setMode}
        {...(onPick ? { picked, onPick } : {})}
      />
    </div>
  );
}
