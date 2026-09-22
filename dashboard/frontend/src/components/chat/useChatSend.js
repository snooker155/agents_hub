import { genId } from './turnState';
import { streamChat } from '../../api';
import { targetId } from './targets';
import { EMPTY_GRAPH_RUN } from '../graphRun';
import { useCallback } from 'react';
import { buildAttachmentLine, buildHistoryPayload, buildStreamRequestBody, resolveConversationTitle } from './send/buildRequest';
import { appendUserMessage, buildAssistantBubble, buildNewConversation } from './send/optimistic';
import { handleAgentEvent } from './send/handleAgentResponse';
import { handleFlowEvent } from './send/handleFlowResponse';
import { handleTeamEvent } from './send/handleTeamResponse';
import { mapSendError, noStreamPatch } from './send/errors';

/**
 * Sending a turn.
 *
 * The longest single thing this page does, and the reason it used to be one
 * 800-line callback: one send fans out into the optimistic bubble, the three
 * target modes, the token stream and everything that rides on it (tools,
 * thoughts, artifacts, views, the graph mirror), and the transcript that is
 * written when it ends. None of that is of any interest to the panes that
 * render the result, so it now lives in `send/`: buildRequest (the outgoing
 * payload), optimistic (the bubbles shown before any response arrives),
 * handleTeamResponse / handleFlowResponse / handleAgentResponse (the stream,
 * split by which of the three targets it's answering), and errors. This file
 * is just the wiring: it stays a single `sendMessage` that calls those stages
 * in order, so behaviour is unchanged.
 */
export function useChatSend(deps) {
  const {
    abortCtrlRef, clientId, conversations, currentConvId, input, loadProcessData, loading,
    mergeArtifact, navigate, pendingAttachments, pendingReferences, processOpen,
    selectCommand, selectedAgent, selectedFlow, selectedProject, selectedTeam,
    selectedWorkspace, setActiveRunId, setAttachmentError, setConversations,
    setCurrentConvId, setGraphRun, setInput, setLoading, setPendingAttachments,
    setPendingReferences, setProcessInsights, setSessionId, t, targetMode, textareaRef,
  } = deps;

  // ---- send message ----
  const sendMessage = useCallback(async (overrideText) => {
    // Button clicks call this with their value; the onClick handler passes a
    // SyntheticEvent, so only honour an explicit string override.
    const text = (typeof overrideText === 'string' ? overrideText : input).trim();
    const hasAttachments = pendingAttachments.length > 0;
    const hasReferences = pendingReferences.length > 0;
    const isFlowMode = targetMode === 'flow';
    const isTeamMode = targetMode === 'team';
    // Flows and teams both answer with several bubbles rather than one streamed
    // reply, so the single-assistant-bubble path is skipped for both.
    const isMultiAgent = isFlowMode || isTeamMode;
    if ((!text && !hasAttachments && !hasReferences) || loading) return;
    if (!targetId(targetMode, { selectedAgent, selectedFlow, selectedTeam })) return;

    // Each turn walks the graph again: carrying the previous turn's path over
    // would show a route this run never took.
    setGraphRun(EMPTY_GRAPH_RUN);

    // Handle special client-side slash commands
    if (text === '/clear') { selectCommand({ name: '/clear' }); return; }
    if (text === '/new') { selectCommand({ name: '/new' }); return; }
    if (text === '/help') { selectCommand({ name: '/help' }); return; }
    if (text === '/config') { selectCommand({ name: '/config' }); return; }

    const attachmentLine = buildAttachmentLine(t, { pendingAttachments, pendingReferences });
    const userMsgText = [text, attachmentLine].filter(Boolean).join('\n');
    const userMsg = { id: genId(), role: 'user', content: userMsgText };

    // Ensure there is an active conversation
    let convId = currentConvId;
    if (!convId) {
      convId = genId();
      const newConv = buildNewConversation({
        convId, text, attachmentLine, t, targetMode, isFlowMode, isTeamMode,
        selectedAgent, selectedFlow, selectedTeam, selectedWorkspace, selectedProject,
      });
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConvId(convId);
      navigate(`/chat/${convId}`);
    }

    // Append user message
    setConversations((prev) => appendUserMessage(prev, convId, userMsg, text, attachmentLine));

    setInput('');
    setPendingAttachments([]);
    setPendingReferences([]);
    setAttachmentError('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setLoading(true);

    const ctrl = new AbortController();
    abortCtrlRef.current = ctrl;

    // Use the conversation's own workspace (set at creation time), not the current global selection.
    // This locks the conversation to the workspace it was started in.
    const convRecord = conversations.find((c) => c.id === convId);
    const effectiveWorkspace = convRecord?.workspace || selectedWorkspace;
    const historyPayload = buildHistoryPayload(convRecord?.messages);
    const convTitle = resolveConversationTitle({ text, attachmentLine, convRecord, t });

    try {
      // In agent mode we create one assistant bubble up front and stream into it.
      // In flow mode we wait for node_start events and create one bubble per node.
      const assistantId = isMultiAgent ? null : genId();
      if (!isMultiAgent) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? { ...c, messages: [...c.messages, buildAssistantBubble(assistantId, selectedAgent)] }
              : c,
          ),
        );
      }

      // Mutated from inside the event handlers; read after the stream resolves.
      // nodeMsgIds maps node_id -> message bubble id, for flow mode.
      const state = { runId: null, finalPayload: null, currentNodeId: null, nodeMsgIds: {} };
      const ctx = {
        convId, setConversations, setActiveRunId, setSessionId, setGraphRun,
        setProcessInsights, mergeArtifact, processOpen, isMultiAgent, isFlowMode,
        assistantId, userMsgText, t, state,
      };

      await streamChat({
        signal: ctrl.signal,
        body: buildStreamRequestBody({
          targetMode, isFlowMode, isTeamMode, selectedAgent, selectedFlow, selectedTeam,
          text, effectiveWorkspace, projectId: convRecord?.project_id, convId, convTitle,
          clientId, historyPayload, pendingAttachments, pendingReferences, selectedWorkspace,
        }),
        onEvent: (event) => {
          if (handleTeamEvent(event, ctx)) return;
          if (handleFlowEvent(event, ctx)) return;
          handleAgentEvent(event, ctx);
        },
      });

      if (!state.finalPayload) {
        if (!isMultiAgent) {
          setConversations((prev) =>
            prev.map((c) =>
              c.id !== convId ? c : {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantId && !m.content
                    ? { ...m, ...noStreamPatch(t) }
                    : m
                ),
              },
            ),
          );
        }
      } else if ((state.runId || state.finalPayload.run_id) && processOpen) {
        loadProcessData(state.runId || state.finalPayload.run_id);
      }
    } catch (err) {
      const errMsg = mapSendError(err, t);
      if (!errMsg) return;
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId ? { ...c, messages: [...c.messages, errMsg] } : c,
        ),
      );
    } finally {
      setLoading(false);
      abortCtrlRef.current = null;
      // The turn is over however it ended (done, abort, network error): drop any
      // half-written thought so no bubble is left with a stale live ticker.
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) => (m.thinking_live ? { ...m, thinking_live: '' } : m)),
          },
        ),
      );
    }
  }, [input, pendingAttachments, pendingReferences, targetMode, loading, selectedAgent, selectedFlow, selectedTeam, t, currentConvId, conversations, setConversations, clientId, selectedWorkspace, selectCommand, selectedProject, navigate, processOpen, mergeArtifact, loadProcessData, abortCtrlRef, setActiveRunId, setAttachmentError, setCurrentConvId, setGraphRun, setInput, setLoading, setPendingAttachments, setPendingReferences, setProcessInsights, setSessionId, textareaRef]);

  // Build view: clicking a file chip in the transcript scrolls the always-open
  // Artifacts panel to that file's diff.
  return { sendMessage };
}

export default useChatSend;
