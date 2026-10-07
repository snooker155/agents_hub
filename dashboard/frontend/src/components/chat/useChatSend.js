import { genId } from './turnState';
import { stopMessage, streamChat } from '../../api';
import { targetId } from './targets';
import { EMPTY_GRAPH_RUN } from '../graphRun';
import { useCallback } from 'react';
import { buildAttachmentLine, buildHistoryPayload, buildStreamRequestBody, resolveConversationTitle } from './send/buildRequest';
import { appendUserMessage, buildAssistantBubble, buildNewConversation } from './send/optimistic';
import { handleAgentEvent } from './send/handleAgentResponse';
import { handleFlowEvent } from './send/handleFlowResponse';
import { handleTeamEvent } from './send/handleTeamResponse';
import { mapSendError, noStreamPatch } from './send/errors';
import { DETACHED, streamChatOverChannel } from './send/channelStream';
import { claimTurn, releaseTurn } from '../chatLiveTurn';

// How long a turn followed over its own streaming response stays claimed after
// the response ends: its last events reach the channel after the response
// body, and must still count as this tab's echo.
const ECHO_GRACE_MS = 5000;

// What a stored conversation talks to, for a turn sent to it while another
// one is open (the page's pickers show the open one's).
function targetOf(conv) {
  const targetMode = conv?.target_mode || (conv?.team_id ? 'team' : conv?.flow_id ? 'flow' : 'agent');
  return {
    targetMode,
    selectedAgent: conv?.agent_id || '',
    selectedFlow: conv?.flow_id || '',
    selectedTeam: conv?.team_id || '',
  };
}

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
 *
 * A turn belongs to its conversation, not to the page (./useChatTurns.js):
 * several conversations can be answering at once. The page-wide panels
 * (Process, the graph mirror, artifacts, the session stream) follow only the
 * conversation that is open, so a turn running behind another chat records
 * its run and session on its own entry and leaves those panels alone.
 *
 * `sendMessage(text, {convId})` sends to a conversation other than the open
 * one (a message that waited for that conversation's turn to end); it talks
 * to whatever that conversation talks to and leaves the composer as it is.
 */
export function useChatSend(deps) {
  const {
    beginTurn, clientId, conversations, currentConvId, currentConvIdRef, endTurn, input,
    isTurnRunning, loadProcessData, mergeArtifact, navigate, noteTurn, pendingAttachments,
    pendingReferences, processOpen, selectCommand, selectedAgent, selectedFlow, selectedProject,
    selectedTeam, selectedWorkspace, setActiveRunId, setAttachmentError, setConversations,
    setCurrentConvId, setGraphRun, setInput, setPendingAttachments, setPendingReferences,
    setProcessInsights, setSelectedAgent, setSessionId, stream, t, targetMode, textareaRef,
  } = deps;

  // ---- send message ----
  const sendMessage = useCallback(async (overrideText, { convId: toConvId = null } = {}) => {
    const background = Boolean(toConvId && toConvId !== currentConvId);
    const backgroundConv = background ? conversations.find((c) => c.id === toConvId) : null;
    if (background && !backgroundConv) return;
    const target = background
      ? targetOf(backgroundConv)
      : { targetMode, selectedAgent, selectedFlow, selectedTeam };
    // Button clicks call this with their value; the onClick handler passes a
    // SyntheticEvent, so only honour an explicit string override.
    const text = (typeof overrideText === 'string' ? overrideText : input).trim();
    // The composer's attachments belong to the open conversation.
    const attachments = background ? [] : pendingAttachments;
    const references = background ? [] : pendingReferences;
    const isFlowMode = target.targetMode === 'flow';
    const isTeamMode = target.targetMode === 'team';
    // Flows and teams both answer with several bubbles rather than one streamed
    // reply, so the single-assistant-bubble path is skipped for both.
    const isMultiAgent = isFlowMode || isTeamMode;
    if (!text && !attachments.length && !references.length) return;
    if (isTurnRunning(background ? toConvId : currentConvId)) return;
    if (!targetId(target.targetMode, target)) return;

    if (!background) {
      // Each turn walks the graph again: carrying the previous turn's path over
      // would show a route this run never took.
      setGraphRun(EMPTY_GRAPH_RUN);

      // Handle special client-side slash commands
      if (text === '/clear') { selectCommand({ name: '/clear' }); return; }
      if (text === '/new') { selectCommand({ name: '/new' }); return; }
      if (text === '/help') { selectCommand({ name: '/help' }); return; }
      if (text === '/config') { selectCommand({ name: '/config' }); return; }
    }

    const attachmentLine = buildAttachmentLine(t, { pendingAttachments: attachments, pendingReferences: references });
    const userMsgText = [text, attachmentLine].filter(Boolean).join('\n');
    const userMsg = { id: genId(), role: 'user', content: userMsgText };

    // Ensure there is an active conversation
    let convId = background ? toConvId : currentConvId;
    if (!convId) {
      convId = genId();
      const newConv = buildNewConversation({
        convId, text, attachmentLine, t, targetMode, isFlowMode, isTeamMode,
        selectedAgent, selectedFlow, selectedTeam, selectedWorkspace, selectedProject,
      });
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConvId(convId);
      // Ahead of the render, so the turn's first events already count as
      // the open conversation's.
      currentConvIdRef.current = convId;
      navigate(`/chat/${convId}`);
    }

    // Append user message
    setConversations((prev) => appendUserMessage(prev, convId, userMsg, text, attachmentLine));

    if (!background) {
      setInput('');
      setPendingAttachments([]);
      setPendingReferences([]);
      setAttachmentError('');
      if (textareaRef.current) textareaRef.current.style.height = 'auto';
    }

    const ctrl = new AbortController();
    const turnKey = genId();
    // Over the tab's SSE connection once it is up (see send/channelStream);
    // before that, the turn's own streaming response.
    const overChannel = Boolean(clientId && stream);
    claimTurn(turnKey);
    // Leaving the Chat page lets go of the turn without stopping it: the
    // conversation's live mirror shows it when the page is back (chatLiveTurn)
    // and the server writes its answer (chat/broadcast.py). A turn on its own
    // streaming response keeps reading it, since closing that would end the
    // turn's bookkeeping on the server.
    const detach = () => {
      releaseTurn(turnKey);
      if (overChannel) ctrl.abort(DETACHED);
    };
    beginTurn(convId, ctrl, { detach });
    // Whether this turn's conversation is the one on screen, asked per event:
    // the person can switch away and back while it runs.
    const here = () => currentConvIdRef.current === convId;

    // Use the conversation's own workspace (set at creation time), not the current global selection.
    // This locks the conversation to the workspace it was started in.
    const convRecord = conversations.find((c) => c.id === convId);
    const effectiveWorkspace = convRecord?.workspace || selectedWorkspace;
    const historyPayload = buildHistoryPayload(convRecord?.messages);
    const convTitle = resolveConversationTitle({ text, attachmentLine, convRecord, t });
    // The turn's handoffs (the agent gave the conversation to another one, see
    // send/handleAgentResponse), read once the turn is over however it ended.
    const handoffs = [];

    try {
      // In agent mode we create one assistant bubble up front and stream into it.
      // In flow mode we wait for node_start events and create one bubble per node.
      const assistantId = isMultiAgent ? null : genId();
      if (!isMultiAgent) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? { ...c, messages: [...c.messages, buildAssistantBubble(assistantId, target.selectedAgent)] }
              : c,
          ),
        );
      }

      // Mutated from inside the event handlers; read after the stream resolves.
      // nodeMsgIds maps node_id -> message bubble id, for flow mode.
      // handoffs collects the turn's `handoff` events (the agent gave the
      // conversation to another one, see send/handleAgentResponse).
      const state = { runId: null, finalPayload: null, currentNodeId: null, nodeMsgIds: {}, handoffs };
      const ctx = {
        convId, setConversations,
        setActiveRunId: (id) => { noteTurn(convId, { runId: id }); if (here()) setActiveRunId(id); },
        setSessionId: (id) => { noteTurn(convId, { sessionId: id }); if (here()) setSessionId(id); },
        setGraphRun: (update) => { if (here()) setGraphRun(update); },
        setProcessInsights: (update) => { if (here()) setProcessInsights(update); },
        mergeArtifact: (art) => { if (here()) mergeArtifact(art); },
        processOpen, isMultiAgent, isFlowMode,
        assistantId, userMsgText, t, state,
      };

      const body = {
        ...buildStreamRequestBody({
          targetMode: target.targetMode, isFlowMode, isTeamMode,
          selectedAgent: target.selectedAgent, selectedFlow: target.selectedFlow,
          selectedTeam: target.selectedTeam, text, effectiveWorkspace,
          projectId: convRecord?.project_id, convId, convTitle, clientId, historyPayload,
          pendingAttachments: attachments, pendingReferences: references, selectedWorkspace,
        }),
        client_turn_id: turnKey,
      };
      const follow = overChannel
        ? (args) => streamChatOverChannel({
          ...args, stream, onLateRun: (runId) => { stopMessage(runId).catch(() => {}); },
        })
        : streamChat;
      await follow({
        signal: ctrl.signal,
        body,
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
                // ctx.assistantId: after a handoff the open bubble is the
                // receiving agent's, not the one created above.
                messages: c.messages.map((m) =>
                  m.id === ctx.assistantId && !m.content
                    ? { ...m, ...noStreamPatch(t) }
                    : m
                ),
              },
            ),
          );
        }
      } else if ((state.runId || state.finalPayload.run_id) && processOpen && here()) {
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
      endTurn(convId, ctrl);
      if (overChannel) releaseTurn(turnKey);
      else setTimeout(() => releaseTurn(turnKey), ECHO_GRACE_MS);
      // The conversation now belongs to the agent that answered: the top bar
      // shows it and the next turn goes to it (the conversation record was
      // pointed at it when the handoff arrived).
      const lastHandoff = handoffs[handoffs.length - 1];
      if (lastHandoff?.to_agent_id && setSelectedAgent && here()) setSelectedAgent(lastHandoff.to_agent_id);
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
  }, [input, pendingAttachments, pendingReferences, targetMode, selectedAgent, selectedFlow, selectedTeam, t, currentConvId, currentConvIdRef, conversations, setConversations, clientId, stream, selectedWorkspace, selectCommand, selectedProject, navigate, processOpen, mergeArtifact, loadProcessData, beginTurn, noteTurn, endTurn, isTurnRunning, setActiveRunId, setAttachmentError, setCurrentConvId, setGraphRun, setInput, setPendingAttachments, setPendingReferences, setProcessInsights, setSelectedAgent, setSessionId, textareaRef]);

  // Build view: clicking a file chip in the transcript scrolls the always-open
  // Artifacts panel to that file's diff.
  return { sendMessage };
}

export default useChatSend;
