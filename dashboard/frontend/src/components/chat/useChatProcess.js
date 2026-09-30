import { getMessageInsights } from '../../api';
import { useCallback, useEffect } from 'react';

/**
 * What the Process panel shows: the runs behind the open conversation, fetched
 * only while the panel is open and only for runs not already held.
 */
export function useChatProcess(deps) {
  const {
    activeRunId, continuationMsgIdRef, conversationRunIds, currentConvId, loadedRunIdsRef,
    loading, processInsights, processInsightsRef, processOpen, setArtifacts,
    setProcessError, setProcessInsights, setProcessLoading, setSessionId, t, viewMode,
  } = deps;

  // Keep ref in sync so loadProcessData can check for existing data without a dep cycle
  useEffect(() => { processInsightsRef.current = processInsights; }, [processInsights, processInsightsRef]);

  const loadProcessData = useCallback(async (runId) => {
    if (!runId) return;
    const silent = processInsightsRef.current.message_runs.length > 0;
    if (!silent) {
      setProcessLoading(true);
      setProcessError('');
    }
    try {
      const insightsRes = await getMessageInsights(runId);
      const raw = insightsRes.data || {
          messages: [],
          tools: [],
          thinking: [],
          message_runs: [],
          token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
        };
      const allowed = conversationRunIds;
      // Rebuild the Artifacts panel from persisted diffs for runs in this conversation.
      const rawArtifacts = (raw.artifacts || []).filter((a) => {
        const rid = String(a?.run_id || '');
        return rid ? allowed.has(rid) : true;
      });
      if (rawArtifacts.length) {
        setArtifacts((prev) => {
          const next = { ...prev };
          for (const a of rawArtifacts) {
            if (a?.path) next[a.path] = { ...a };
          }
          return next;
        });
      }
      const filteredRuns = (raw.message_runs || []).filter((mr) => {
        const rid = String(mr?.run_id || '');
        return rid ? allowed.has(rid) : false;
      });
      const filteredTools = (raw.tools || []).filter((t) => {
        const rid = String(t?.run_id || '');
        return rid ? allowed.has(rid) : false;
      });

      // Merge fetched run data with existing runs — the API returns data for only
      // one run at a time, so we must preserve previously-loaded runs rather than
      // replacing the whole list. Runs with matching run_id are updated in-place.
      setProcessInsights((prev) => {
        const newRunIds = new Set(filteredRuns.map((mr) => String(mr?.run_id || mr?.message_id || '')));
        const preserved = (prev.message_runs || []).filter((mr) => {
          const id = String(mr?.run_id || mr?.message_id || '');
          return id && !newRunIds.has(id);
        });
        const mergedRuns = [...preserved, ...filteredRuns];
        const newToolRunIds = new Set(filteredTools.map((t) => String(t?.run_id || '')));
        const preservedTools = (prev.tools || []).filter((t) => {
          const id = String(t?.run_id || '');
          return id && !newToolRunIds.has(id);
        });
        const inTok = mergedRuns.reduce((s, mr) => s + (Number(mr?.inbound_tokens) || 0), 0);
        const outTok = mergedRuns.reduce((s, mr) => s + (Number(mr?.outbound_tokens) || 0), 0);
        const totalTok = mergedRuns.reduce(
          (s, mr) => s + (Number(mr?.total_tokens) || ((Number(mr?.inbound_tokens) || 0) + (Number(mr?.outbound_tokens) || 0))),
          0,
        );
        return {
          ...prev,
          session_id: raw.session_id || prev.session_id,
          message_runs: mergedRuns,
          tools: [...preservedTools, ...filteredTools],
          token_usage: { inbound_tokens: inTok, outbound_tokens: outTok, total_tokens: totalTok },
          // Runs load one at a time, so the session's peak is the running max.
          context_peak: Math.max(prev.context_peak || 0, raw.context_window?.input_tokens_used || 0),
          context_window_tokens: raw.context_window?.context_window_tokens || prev.context_window_tokens || 0,
        };
      });
    } catch (err) {
      if (!silent) setProcessError(err.response?.data?.detail || t('chat.failedToLoadProcess'));
    } finally {
      if (!silent) setProcessLoading(false);
    }
  }, [conversationRunIds, t, processInsightsRef, setArtifacts, setProcessError, setProcessInsights, setProcessLoading]);

  // Load all unloaded conversation runs whenever the panel is open and conversationRunIds changes.
  // Build view also needs this data (for the Artifacts panel), even with the
  // Process panel closed. Uses a ref to avoid re-fetching runs already loaded.
  useEffect(() => {
    if ((!processOpen && viewMode !== 'build') || loading) return;
    const toLoad = Array.from(conversationRunIds).filter(rid => !loadedRunIdsRef.current.has(rid));
    if (!toLoad.length) return;
    toLoad.forEach(runId => {
      loadedRunIdsRef.current.add(runId);
      loadProcessData(runId);
    });
  }, [processOpen, viewMode, activeRunId, loading, loadProcessData, conversationRunIds, loadedRunIdsRef]);


  // Reset process panel when switching conversations. Clearing sessionId
  // releases the session channel on the shared stream automatically.
  useEffect(() => {
    continuationMsgIdRef.current = null;
    setSessionId(null);
    loadedRunIdsRef.current = new Set();
    setArtifacts({});
    setProcessInsights({
      messages: [],
      tools: [],
      thinking: [],
      message_runs: [],
      token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
      context_peak: 0,
      context_window_tokens: 0,
    });
  }, [currentConvId, continuationMsgIdRef, loadedRunIdsRef, setArtifacts, setProcessInsights, setSessionId]);

  return { loadProcessData };
}

export default useChatProcess;
