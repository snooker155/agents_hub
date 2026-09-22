import { appendLiveThought, genId, mergeMessageFile } from './turnState';
import { useChannel } from '../stream';
import { useEffect } from 'react';

/**
 * The session channel: a turn this tab did not start, or one that continues
 * after the request that began it has returned. Everything it publishes lands
 * in the same bubble the send path writes, so a continuation reads as more of
 * the same message rather than as a second one.
 */
export function useChatSessionStream(deps) {
  const {
    continuationMsgIdRef, currentConvId, mergeArtifact, sessionId, setActiveRunId,
    setConversations,
  } = deps;

  // ---- session SSE subscription for continuation runs ----
  // Subscribe to this session's channel on the shared multiplexed stream so
  // continuation runs (spawned as subprocesses after the original HTTP response
  // closed) stream their output into this chat in real time. No dedicated
  // connection — interest is added/removed on the single app-wide EventSource.
  useEffect(() => { continuationMsgIdRef.current = null; }, [sessionId, currentConvId, continuationMsgIdRef]);

  useChannel(sessionId && currentConvId ? sessionId : null, (event) => {
    if (!event || !event.type) return;

    // Ignore heartbeats and events from the primary run (handled by the fetch stream)
    if (event.type === 'heartbeat') return;
    if (event.type === 'meta' && !event.continuation) return;

    const convId = currentConvId;

    if (event.type === 'meta' && event.continuation) {
      // A new continuation run is starting — create a new assistant message bubble
      const continuationMsgId = genId();
      continuationMsgIdRef.current = continuationMsgId;
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: [
              ...c.messages,
              {
                id: continuationMsgId,
                role: 'agent',
                agent_id: event.agent_id || '',
                content: '',
                error: false,
                run_id: event.run_id || null,
                inbound_tokens: null,
                outbound_tokens: null,
                total_tokens: null,
                tool_calls: null,
                duration_ms: null,
              },
            ],
          }
        )
      );
      if (event.run_id) setActiveRunId(event.run_id);
    } else if (event.type === 'token' && continuationMsgIdRef.current) {
      const continuationMsgId = continuationMsgIdRef.current;
      const tok = event.token || '';
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) => {
              if (m.id !== continuationMsgId) return m;
              const tl = [...(m.timeline || [])];
              const last = tl[tl.length - 1];
              if (last && last.type === 'text') {
                tl[tl.length - 1] = { ...last, text: `${last.text || ''}${tok}` };
              } else {
                tl.push({ type: 'text', text: tok });
              }
              return { ...m, content: `${m.content || ''}${tok}`, timeline: tl };
            }),
          }
        )
      );
    } else if (event.type === 'artifact') {
      mergeArtifact(event);
      const continuationMsgId = continuationMsgIdRef.current;
      if (continuationMsgId) {
        const entry = { type: 'artifact', op: event.op, path: event.path, additions: event.additions, deletions: event.deletions };
        setConversations((prev) =>
          prev.map((c) =>
            c.id !== convId ? c : {
              ...c,
              messages: c.messages.map((m) =>
                m.id === continuationMsgId
                  ? { ...m, timeline: [...(m.timeline || []), entry], files: mergeMessageFile(m.files, event) }
                  : m
              ),
            }
          )
        );
      }
    } else if ((event.type === 'think' || event.type === 'plan') && continuationMsgIdRef.current) {
      // Completed thought: it becomes a ReasoningStep in the bubble and ends the
      // live ticker that was showing the same text as it was written.
      const continuationMsgId = continuationMsgIdRef.current;
      const step = { kind: event.type, step: event.step, content: event.content };
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === continuationMsgId
                ? {
                    ...m,
                    reasoning: [...(m.reasoning || []), step],
                    timeline: [...(m.timeline || []), { type: 'reasoning', ...step }],
                    thinking_live: '',
                  }
                : m
            ),
          }
        )
      );
    } else if (event.type === 'think_delta' && continuationMsgIdRef.current) {
      const continuationMsgId = continuationMsgIdRef.current;
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === continuationMsgId
                ? { ...m, thinking_live: appendLiveThought(m.thinking_live, event.delta) }
                : m
            ),
          }
        )
      );
    } else if (event.type === 'done' && continuationMsgIdRef.current) {
      const continuationMsgId = continuationMsgIdRef.current;
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === continuationMsgId
                ? {
                    ...m,
                    content: (m.content || event.response || '').trim() || event.response || '',
                    thinking_live: '',
                    error: !event.ok,
                    run_id: event.run_id || m.run_id,
                    inbound_tokens: event.usage?.inbound_tokens ?? null,
                    outbound_tokens: event.usage?.outbound_tokens ?? null,
                    total_tokens: event.usage?.total_tokens ?? null,
                    tool_calls: event.tool_calls ?? null,
                    duration_ms: event.duration_ms ?? null,
                    context_used: event.usage?.context_used ?? null,
                    context_window: event.usage?.context_window ?? null,
                    context_overflow: event.error_code === 'context_overflow',
                  }
                : m
            ),
          }
        )
      );
      continuationMsgIdRef.current = null;
    } else if (event.type === 'session_done') {
      continuationMsgIdRef.current = null;
    }
  });
}

export default useChatSessionStream;
