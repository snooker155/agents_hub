import { useEffect, useState } from 'react';

// The Artifacts panel's switch, Files (what each file holds now) or Diff
// (what the runs changed): Files unless the person picked Diff, and the pick
// survives leaving the page. Shared by the Chat page and a flow's chat.
const ARTIFACTS_MODE_KEY = 'agent_hub_chat_artifacts_mode';

export function useArtifactsMode() {
  const [mode, setMode] = useState(() => {
    try { return localStorage.getItem(ARTIFACTS_MODE_KEY) === 'diff' ? 'diff' : 'content'; } catch { return 'content'; }
  });
  useEffect(() => {
    try { localStorage.setItem(ARTIFACTS_MODE_KEY, mode); } catch { /* storage unavailable */ }
  }, [mode]);
  return [mode, setMode];
}
