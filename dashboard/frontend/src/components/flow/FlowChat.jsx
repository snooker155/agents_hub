import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { CornerDownRight, Send, StopCircle, Workflow, X } from 'lucide-react';
import { stopMessage, startChatOverSSE, summarizeFlowChatTurn } from '../../api';
import { useStream } from '../stream';
import { useI18n } from '../../i18n';
import { useInlineChatOpen } from '../pageChat/pageChat';
import ExampleRequests from '../chat/ExampleRequests';
import fillComposer from '../chat/fillComposer';
import { getFlowExamples } from '../../api/flows';
import { buildAttachmentLine, historyWindowStart } from '../chat/send/buildRequest';
import { useChatComposerInput } from '../chat/useChatComposerInput';
import { AttachButton, AttachPickers, PendingAttachments, PendingReferences } from '../chat/ComposerAttachments';
import { attachmentsPayload, referencesPayload } from '../chat/composerPayload';
import { stripUiBlock } from '../chat/markdown';
import AgentTranscript from '../chat/AgentTranscript';
import ViewModeToggle from '../chat/ViewModeToggle';
import { useChatViewMode } from '../chat/viewMode';
import { summaryViewMessages, turnsWithoutSummary, useFlowSummaryMode } from './summaryView';
import { agentMessage, applyTurnEvent, settleTurn } from '../chat/turnEvents';
import { useMessagesWithTrails } from '../chat/runTrail';
import { useChatScroll } from '../chat/useChatScroll';
import ComposerBox from '../chat/ComposerBox';
import { COMPOSER_SEND_CLS, COMPOSER_STOP_CLS, COMPOSER_TEXTAREA_CLS } from '../chat/composerStyles';
import { conversationArtifacts } from '../chat/artifactOps';
import { conversationViews } from '../chat/turnViews';
import { entityNodeText, rerunBubbleText, routeText } from '../flowEditor/runFormat';

// ---------------------------------------------------------------------------
// Slim flow chat — embedded version of the Chat page scoped to a single flow.
// Each user turn runs through every node of the flow (one bubble per node),
// drawn the way the Chat page draws a turn (chat/AgentTranscript.jsx): each
// node's thoughts, tool calls and files, in the Chat or Build view. In the
// Summary view a turn is one reply instead: the step in progress while it
// works, then a summary of what was done (flow/summaryView.js). The
// run's events are delivered over the app's single multiplexed SSE connection
// (POST /api/chat/stream-sse returns immediately; events arrive on the
// `chat:{conversation_id}` channel), so a flow chat doesn't hold a second
// long-lived connection and exhaust the browser's per-origin limit.
// ---------------------------------------------------------------------------

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

export default function FlowChat({
  flow,
  flowId,
  workspace,
  agentLabels = {},
  chatNonce = 0,
  // Resume support: when a chat History record is opened, the parent passes the
  // record's conversation id + reconstructed messages so new turns append to the
  // SAME conversation (and therefore grow the same History record).
  resumeConversationId = null,
  resumeMessages = null,
  resumeKey = null,
  // Forwarded the raw stream events (flow_meta / node_start / node_done /
  // node_skip / done) so the parent (FlowEditor) can drive the canvas, node
  // statuses, and Logs panel live straight from the stream — no refetch.
  onStreamEvent = null,
  // Called once when a turn ends so the parent can reconcile the History list /
  // persisted record. Not used for live progress anymore (see onStreamEvent).
  onActivity = null,
  // Told the files and views this conversation's nodes produced whenever
  // they change, `{artifacts, views}`, for the page's Artifacts tab.
  onArtifacts = null,
  // The node the next message goes to, `{id, label}` or null: the flow then
  // starts at that node with the message as its input and runs to the end
  // (flow/rerun.py start_at). Shown over the box; cleared once sent.
  startNode = null,
  onClearStartNode = null,
  // Run this conversation's last turn again from a node, with that turn's
  // inputs: `{node_id, label, mode, key}`, a new `key` per request. Told
  // back through onRerunHandled once the turn is sent (or cannot be).
  rerunRequest = null,
  onRerunHandled = null,
}) {
  const { t, language } = useI18n();
  // The flow's own chat, in the editor's right-hand panel: while it is mounted
  // the floating launcher would sit on its composer, so it stands down.
  useInlineChatOpen();
  const { on, acquireChannel, clientId } = useStream();
  const [messages, setMessages] = useState([]);
  // The node bubble being written right now.
  const [streamingId, setStreamingId] = useState(null);
  const [viewMode, setViewMode] = useChatViewMode();
  // The Summary view is this chat's own third choice, kept apart from the
  // Chat / Build choice every chat shares; leaving it goes back to that one.
  const [summaryMode, setSummaryMode] = useFlowSummaryMode();
  const shownMode = summaryMode ? 'summary' : viewMode;
  // Turns being summed up after the fact, the conversation already asked
  // about (asked again on the next switch to Summary), and a failure.
  const [writingTurns, setWritingTurns] = useState(() => new Set());
  const [askedFor, setAskedFor] = useState(null);
  const [summaryError, setSummaryError] = useState('');
  const changeMode = useCallback((mode) => {
    setSummaryMode(mode === 'summary');
    if (mode !== 'summary') {
      setViewMode(mode);
      setAskedFor(null);
      setSummaryError('');
    }
  }, [setSummaryMode, setViewMode]);
  // A History record reopened here carries only each node's answer; its
  // steps and files come from the node's run. A run again from a node has
  // no message of its own, so its bubble says what it was.
  const withTrails = useMessagesWithTrails(messages, true);
  const shownMessages = useMemo(
    () => withTrails.map((m) => {
      if (m.rerun && !m.content) return { ...m, content: rerunBubbleText(t, m.rerun) };
      // A switch has no answer: its bubble says which step it chose.
      if (m.route && !m.content) return { ...m, content: routeText(t, m.route) };
      return m;
    }),
    [withTrails, t],
  );
  // The files and views the flow's nodes produced in this conversation, for
  // the page's Artifacts tab (the Chat page's Artifacts panel).
  const artifacts = useMemo(() => conversationArtifacts(shownMessages), [shownMessages]);
  const artifactViews = useMemo(
    () => conversationViews(shownMessages).filter((v) => v.view_kind !== 'code'),
    [shownMessages],
  );
  useEffect(() => {
    onArtifacts?.({ artifacts, views: artifactViews });
  }, [artifacts, artifactViews, onArtifacts]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const activeRunIdRef = useRef(null);
  // Tears down the current turn's SSE channel subscription (listener + interest).
  const subCleanupRef = useRef(null);
  const textareaRef = useRef(null);

  const flowName = flow?.name || 'flow';

  // The box over the foot of the conversation: its height, measured, is the
  // room the message list keeps free under its last message.
  const composerRef = useRef(null);
  const [composerHeight, setComposerHeight] = useState(0);
  useLayoutEffect(() => {
    const el = composerRef.current;
    if (!el) return undefined;
    const measure = () => setComposerHeight(el.getBoundingClientRect().height);
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Drop the channel subscription if the component unmounts mid-run.
  useEffect(() => () => subCleanupRef.current?.(), []);

  // Reset the thread when the flow changes or a new chat is started (chatNonce
  // bump). A fresh nonce also yields a new conversationId below, so the new
  // thread becomes its own History record.
  useEffect(() => {
    subCleanupRef.current?.(); // drop a previous conversation's live subscription
    setLoading(false);
    setMessages([]);
    setInput('');
  }, [flowId, chatNonce]);

  // When a History record is opened (resumeKey set), seed the thread with its
  // reconstructed messages so the conversation can continue. When the resume is
  // dropped (resumeKey → null, e.g. on deselect), clear the thread so the chat
  // returns to an empty new chat rather than keeping the historical messages.
  useEffect(() => {
    subCleanupRef.current?.(); // drop a previous conversation's live subscription
    setLoading(false);
    setMessages(resumeKey == null ? [] : resumeMessages || []);
    setInput('');
  }, [resumeKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // The conversation id: a resumed record's own id (so turns append to it),
  // otherwise the per-nonce id. The nonce makes each "New chat" a distinct
  // conversation (and therefore a distinct flow-chat history record).
  const conversationId = useMemo(
    () =>
      resumeConversationId
        ? resumeConversationId
        : chatNonce
        ? `flow-chat-${flowId}-${chatNonce}`
        : `flow-chat-${flowId}`,
    [resumeConversationId, flowId, chatNonce]
  );

  // Opens at the end and stays there while a reply is written, as the Chat
  // page does: also once steps and files load into reopened bubbles, once a
  // hidden tab shows, and when the box over the foot grows.
  const lastUserId = [...shownMessages].reverse().find((m) => m.role === 'user')?.id ?? null;
  const { scrollerRef, contentRef, onScroll, followEnd } = useChatScroll({
    convId: conversationId, lastUserId, messages: shownMessages,
  });
  useLayoutEffect(() => { followEnd(); }, [composerHeight, loading, followEnd]);

  // What rides with the next message, as on the Chat page: files from the
  // computer or the workspace, and hub records (chat/ComposerAttachments.jsx).
  const fileInputRef = useRef(null);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [pendingReferences, setPendingReferences] = useState([]);
  const [attachmentError, setAttachmentError] = useState('');
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [contextKinds, setContextKinds] = useState([]);
  const [pickerKind, setPickerKind] = useState(null);
  const [filePickerOpen, setFilePickerOpen] = useState(false);
  // Up to ten lines, then it scrolls (lib/autoGrow.js); follows the text
  // so a box emptied after a send shrinks back too.
  const {
    resizeTextarea, onPickFiles, removeAttachment, toggleAttachmentStore,
    addReferences, removeReference, addWorkspaceFiles,
  } = useChatComposerInput({
    attachMenuOpen, contextKinds, pendingAttachments, selectedWorkspace: workspace,
    setAttachmentError, setContextKinds, setPendingAttachments, setPendingReferences, textareaRef,
  });
  useEffect(() => { resizeTextarea(); }, [input, resizeTextarea]);
  const hasPayload = Boolean(input.trim() || pendingAttachments.length || pendingReferences.length);

  // One turn: a message (to the flow's start, or to `startNode`), or with
  // `rerun` the last turn again from a node, which carries no message.
  const sendTurn = useCallback(async ({ rerun = null } = {}) => {
    const text = rerun ? '' : input.trim();
    if (!rerun && !text && !pendingAttachments.length && !pendingReferences.length) return;
    if (loading || !flowId) return;
    if (!workspace) {
      setMessages((prev) => [
        ...prev,
        { id: genId(), role: 'agent', content: t('flowFlowChat.selectWorkspaceFirst'), error: true },
      ]);
      return;
    }

    // A turn's summary restates its nodes' answers, which are in already.
    const said = messages
      .filter((m) => !m.rerun && !m.summary && (m.role === 'user' || m.role === 'agent') && String(m.content || '').trim());
    const history = said
      .slice(historyWindowStart(said.length))
      .map((m) => ({ role: m.role, content: String(m.content || '') }));

    // What was attached, named under the text, as the Chat page's bubble does.
    const attachmentLine = rerun ? '' : buildAttachmentLine(t, { pendingAttachments, pendingReferences });
    const rerunInfo = rerun ? { node_id: rerun.node_id, label: rerun.label, mode: rerun.mode } : null;
    const userMsg = rerunInfo
      ? { id: genId(), role: 'user', content: '', rerun: rerunInfo }
      : { id: genId(), role: 'user', content: [text, attachmentLine].filter(Boolean).join('\n') };
    const attachments = rerun ? [] : attachmentsPayload(pendingAttachments, workspace);
    const references = rerun ? [] : referencesPayload(pendingReferences);
    const toNode = rerun ? null : startNode;
    setMessages((prev) => [...prev, userMsg]);
    if (!rerun) {
      setInput('');
      setPendingAttachments([]);
      setPendingReferences([]);
      setAttachmentError('');
      if (textareaRef.current) textareaRef.current.style.height = 'auto';
      if (toNode) onClearStartNode?.();
    }

    if (!clientId) {
      setMessages((prev) => [
        ...prev,
        { id: genId(), role: 'agent', content: t('flowFlowChat.liveNotReady'), error: true },
      ]);
      return;
    }

    setLoading(true);
    // Tear down any lingering subscription from a previous (e.g. stopped) turn so
    // we never run two subscriptions on the same channel at once.
    subCleanupRef.current?.();

    // Per-turn state, captured by the channel handler below (which outlives the
    // POST and runs until the terminal sentinel arrives over the SSE).
    const nodeMsgIds = {};
    let summaryMsgId = null;
    // The turn's id (flow_meta), on each of its messages, so a summary
    // written for it later finds it.
    let turnId = null;
    let currentNodeId = null;
    let sawDone = false;
    let finished = false;
    const channel = `chat:${conversationId}`;

    const targetMsgId = (event) => {
      const nid = event.node_id || currentNodeId;
      return nid ? nodeMsgIds[nid] : null;
    };

    let off = null;
    let release = null;
    const cleanup = () => {
      off?.(); release?.();
      off = null; release = null;
      if (subCleanupRef.current === cleanup) subCleanupRef.current = null;
    };
    const finalize = () => {
      if (finished) return;
      finished = true;
      setLoading(false);
      setStreamingId(null);
      // Nothing is left running once the turn is over (a stop, an error).
      const ids = new Set([...Object.values(nodeMsgIds), summaryMsgId].filter(Boolean));
      setMessages((prev) => prev.map((m) => (ids.has(m.id) && m.running ? { ...settleTurn(m), running: false } : m)));
      activeRunIdRef.current = null;
      // Close the live run if it ended without a terminal `done` (Stop / error)
      // so the parent doesn't leave a node stuck running.
      if (!sawDone) onStreamEvent?.({ type: 'done', ok: false });
      onActivity?.(); // turn ended — reconcile History list + persisted record
      cleanup();
    };

    const handleEvent = (event) => {
      // Server sentinel: the run is over, stop listening.
      if (event.type === 'chat_stream_end') { finalize(); return; }
      // Forward every event to the parent's live stream-log reducer first so the
      // canvas/logs/statuses update in lockstep with the bubbles. flow_meta opens
      // the live History record, so tag it with this turn's user message — the
      // parent titles the record by the conversation's first message.
      onStreamEvent?.(event.type === 'flow_meta' ? { ...event, user_message: text } : event);
      if (event.type === 'done') {
        sawDone = true;
        // A turn refused before any node started (a node that cannot run
        // again from that turn, an invalid flow) says why.
        if (event.ok === false && event.error && !Object.keys(nodeMsgIds).length) {
          setMessages((prev) => [...prev, { id: genId(), role: 'agent', content: event.error, error: true }]);
        }
      }
      if (event.type === 'flow_meta') {
        // The canvas side is handled by onStreamEvent.
        if (event.turn_id) {
          turnId = event.turn_id;
          setMessages((prev) => prev.map((m) => (m.id === userMsg.id ? { ...m, turn_id: turnId } : m)));
        }
      } else if (event.type === 'flow_summary_delta' || event.type === 'flow_summary') {
        // The turn's one closing message (Summary view), written after
        // its last node; `flow_summary` carries the whole text.
        if (!summaryMsgId) {
          summaryMsgId = genId();
          const id = summaryMsgId;
          setMessages((prev) => [...prev, agentMessage({
            id, summary: true, running: true, error: false, ...(turnId ? { turn_id: turnId } : {}),
          })]);
        }
        const id = summaryMsgId;
        setStreamingId(event.type === 'flow_summary' ? null : id);
        setMessages((prev) => prev.map((m) => (m.id !== id ? m
          : event.type === 'flow_summary'
            ? { ...m, content: event.content || m.content, running: false }
            : { ...m, content: (m.content || '') + (event.content || '') })));
      } else if (event.type === 'node_start') {
        currentNodeId = event.node_id;
        const msgId = genId();
        nodeMsgIds[event.node_id] = msgId;
        if (event.run_id) activeRunIdRef.current = event.run_id;
        setStreamingId(msgId);
        setMessages((prev) => [
          ...prev,
          agentMessage({
            id: msgId,
            agent_id: event.agent_id || event.agent_label || '',
            agent_label: event.agent_label || '',
            node_id: event.node_id,
            run_id: event.run_id || null,
            running: true,
            error: false,
            ...(turnId ? { turn_id: turnId } : {}),
          }),
        ]);
      } else if (event.type === 'node_done') {
        const msgId = nodeMsgIds[event.node_id];
        setMessages((prev) =>
          prev.map((m) =>
            m.id === msgId
              ? {
                  // Prefer the node's final response over accumulated stream
                  // tokens so a re-stated answer isn't repeated in the bubble.
                  ...settleTurn(m, {
                    content: (event.response || '').trim()
                      || (event.response_obj ? '' : stripUiBlock(m.content || '').trim())
                      || (event.ok ? '' : event.error || '')
                      // A node with no agent says what it did instead.
                      || (event.is_agent === false && !event.route ? entityNodeText(t, event) : ''),
                  }),
                  ...(event.route && event.ok ? { route: event.route } : {}),
                  entities: event.entities || m.entities || null,
                  citations: event.citations || m.citations || null,
                  run_id: event.run_id || m.run_id,
                  error: !event.ok,
                  running: false,
                }
              : m
          )
        );
        setStreamingId((cur) => (cur === msgId ? null : cur));
      } else {
        // Thoughts, tools, text, files: the reducer every chat shares, into
        // the bubble of the node that sent them.
        const tgt = targetMsgId(event);
        if (tgt) setMessages((prev) => prev.map((m) => (m.id === tgt ? applyTurnEvent(m, event) : m)));
      }
    };

    // Subscribe BEFORE the POST so we don't miss early events; the server also
    // subscribes our client_id server-side before it starts publishing.
    off = on(channel, handleEvent);
    release = acquireChannel(channel);
    subCleanupRef.current = cleanup;

    try {
      await startChatOverSSE({
        agent_id: null,
        flow_id: flowId,
        // A run again from a node sends what its bubble says; the server
        // takes the last turn's own message for the flow (chat/pipelines.py).
        message: rerunInfo ? rerunBubbleText(t, rerunInfo) : text,
        ...(summaryMode ? { flow_summary: true } : {}),
        ...(rerunInfo ? { flow_rerun: { node_id: rerunInfo.node_id, mode: rerunInfo.mode } } : {}),
        ...(toNode ? { flow_start_node: toNode.id } : {}),
        workspace: workspace || null,
        conversation_id: conversationId,
        conversation_title: flowName,
        history,
        attachments,
        references,
        client_id: clientId,
      });
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: genId(), role: 'agent', content: err?.response?.data?.detail || err.message || t('flowFlowChat.startChatFailed'), error: true },
      ]);
      finalize();
    }
  }, [input, loading, flowId, workspace, messages, clientId, conversationId, on, acquireChannel, t, onStreamEvent, onActivity, flowName, pendingAttachments, pendingReferences, startNode, onClearStartNode, summaryMode]);

  const sendMessage = useCallback(() => sendTurn(), [sendTurn]);

  // Summaries of turns sent in the Chat view, written on request in the
  // Summary view (routes/flows.py summarize_flow_chat_turn). Each lands at
  // the end of its turn.
  const writeSummaries = useCallback(async (turns) => {
    const conv = conversationId;
    setAskedFor(conv);
    setSummaryError('');
    let failed = 0;
    for (const turn of turns) {
      setWritingTurns((prev) => new Set(prev).add(turn.turn_id));
      try {
        const { data } = await summarizeFlowChatTurn(flowId, conv, turn);
        setMessages((prev) => {
          let at = -1;
          prev.forEach((m, i) => { if (m.turn_id === turn.turn_id) at = i; });
          if (at === -1) return prev;
          const summary = agentMessage({ id: genId(), summary: true, turn_id: turn.turn_id, content: data?.summary || '' });
          return [...prev.slice(0, at + 1), summary, ...prev.slice(at + 1)];
        });
      } catch {
        failed += 1;
      } finally {
        setWritingTurns((prev) => { const next = new Set(prev); next.delete(turn.turn_id); return next; });
      }
    }
    if (failed) setSummaryError(t('flowFlowChat.summaryFailed', { count: failed }));
    onActivity?.();
  }, [conversationId, flowId, onActivity, t]);

  // A run again from a node asked for by the page: sent once per request,
  // when no turn is open (a busy chat reports it back unsent).
  const handledRerunRef = useRef(null);
  useEffect(() => {
    if (!rerunRequest || rerunRequest.key === handledRerunRef.current) return;
    handledRerunRef.current = rerunRequest.key;
    if (!loading) sendTurn({ rerun: rerunRequest });
    onRerunHandled?.(!loading);
  }, [rerunRequest, loading, sendTurn, onRerunHandled]);

  const stopGeneration = () => {
    if (activeRunIdRef.current) stopMessage(activeRunIdRef.current).catch(() => {});
    // The backend pump still emits its terminal sentinel, which finalizes the
    // turn; setting loading false here just gives immediate feedback.
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // A turn's summary is the flow's own reply.
  const labelFor = (msg) =>
    (msg.summary ? flowName : '') || msg.agent_label || agentLabels[msg.agent_id] || msg.agent_id || 'Agent';
  const transcript = summaryMode
    ? summaryViewMessages(shownMessages, {
      streamingId, loading, writing: writingTurns, writingLabel: t('flowFlowChat.summaryWriting'),
    })
    // The Chat and Build views show what the nodes said, not a summary.
    : shownMessages.filter((m) => !m.summary);
  // Turns sent in the Chat view have no summary: in the Summary view the
  // chat asks once whether to write them (until asked again in another
  // conversation), and writes them one by one on a yes.
  const missing = summaryMode && !writingTurns.size && askedFor !== conversationId
    ? turnsWithoutSummary(shownMessages, { loading })
    : [];
  // A turn is open and nothing of its reply is on show: before the first
  // node, or in the Summary view between two nodes.
  const waiting = loading && !streamingId && transcript[transcript.length - 1]?.role === 'user';

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-slate-50">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-100">
          <Workflow className="h-4 w-4 text-emerald-600" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-slate-900">{t('flowFlowChat.chatWithFlow')}</div>
          <div className="truncate text-[11px] text-slate-400">{flowName}</div>
        </div>
        {/* Chat or Build, the same choice as on the Chat page. */}
        <ViewModeToggle viewMode={shownMode} onChange={changeMode} summary />
      </div>

      {/* The conversation, with the box laid over its foot as on the Chat
          page: the messages scroll behind the box, and the list keeps the
          box's height free under its last message. */}
      <div className="relative min-h-0 flex-1">
      <div ref={scrollerRef} onScroll={onScroll} className="h-full overflow-y-auto px-3 pt-3" style={{ paddingBottom: composerHeight + 12 }}>
        {messages.length === 0 && !loading ? (
          <div className="flex min-h-full flex-col items-center justify-center py-4 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-100 shadow-sm">
              <Workflow className="h-7 w-7 text-emerald-600" />
            </div>
            <p className="max-w-sm text-sm leading-relaxed text-slate-500">
              {t('chat.emptyFlow')}
            </p>
            {/* The flow's own example requests, else its first agent's
                (GET /api/flows/{id}/examples); a click fills the box. */}
            <ExampleRequests
              compact
              load={() => getFlowExamples(flowId, language)}
              loadKey={flowId ? `${flowId}:${language}` : null}
              onPick={(text) => fillComposer(text, setInput, textareaRef)}
            />
          </div>
        ) : (
          <div ref={contentRef}>
            <AgentTranscript
              messages={transcript}
              viewMode={summaryMode ? 'chat' : viewMode}
              streamingId={streamingId}
              waiting={waiting}
              agentName={flowName}
              agentNameFor={labelFor}
            />
            {missing.length ? (
              <div className="mx-2 mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs text-indigo-900" data-testid="flow-summary-offer">
                <span className="min-w-0 flex-1">{t('flowFlowChat.summaryOffer', { count: missing.length })}</span>
                <button type="button" onClick={() => writeSummaries(missing)} className="rounded-lg bg-indigo-600 px-2.5 py-1 font-medium text-white transition hover:bg-indigo-700">
                  {t('flowFlowChat.summaryOfferYes')}
                </button>
                <button type="button" onClick={() => setAskedFor(conversationId)} className="rounded-lg px-2.5 py-1 font-medium text-indigo-700 transition hover:bg-indigo-100">
                  {t('flowFlowChat.summaryOfferNo')}
                </button>
              </div>
            ) : null}
            {summaryError ? (
              <div className="mx-2 mb-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700" data-testid="flow-summary-error">{summaryError}</div>
            ) : null}
          </div>
        )}
      </div>

      <div ref={composerRef} className="pointer-events-none absolute inset-x-0 bottom-0 z-10 px-3 pb-3 pt-2">
        <div className="pointer-events-auto mx-auto w-full max-w-3xl">
          <input ref={fileInputRef} type="file" multiple className="hidden" onChange={onPickFiles} />
          <PendingReferences references={pendingReferences} onRemove={removeReference} disabled={loading} />
          <PendingAttachments
            attachments={pendingAttachments}
            workspace={workspace}
            onToggleStore={toggleAttachmentStore}
            onRemove={removeAttachment}
            disabled={loading}
          />
          {startNode ? (
            <div className="mb-2 flex items-center gap-2 rounded-xl border border-cyan-200 bg-cyan-50 px-3 py-1.5 text-xs text-cyan-800" data-testid="flow-chat-start-node">
              <CornerDownRight className="h-3.5 w-3.5 shrink-0 text-cyan-600" />
              <span className="min-w-0 flex-1">{t('flowFlowChat.toNode', { node: startNode.label })}</span>
              <button
                type="button"
                onClick={() => onClearStartNode?.()}
                title={t('flowFlowChat.toNodeCancel')}
                aria-label={t('flowFlowChat.toNodeCancel')}
                className="shrink-0 rounded-md p-0.5 text-cyan-600 transition hover:bg-cyan-100 hover:text-cyan-800"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : null}
          <ComposerBox hint={t('flowFlowChat.enterToSendShiftEnter')}
            leading={(
              <AttachButton
                open={attachMenuOpen}
                onOpenChange={setAttachMenuOpen}
                disabled={loading || !flowId}
                contextKinds={contextKinds}
                onFiles={() => fileInputRef.current?.click()}
                onWorkspaceFiles={() => setFilePickerOpen(true)}
                onKind={setPickerKind}
              />
            )}
            actions={loading ? (
              <button type="button" onClick={stopGeneration} title={t('flowFlowChat.stop')} className={COMPOSER_STOP_CLS}>
                <StopCircle className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="button"
                onClick={sendMessage}
                disabled={!hasPayload || !flowId}
                title={t('flowFlowChat.sendEnter')}
                className={COMPOSER_SEND_CLS}
              >
                <Send className="h-3.5 w-3.5" />
              </button>
            )}
          >
            <textarea
              ref={textareaRef}
              className={COMPOSER_TEXTAREA_CLS}
              placeholder={!flowId ? t('flowFlowChat.noFlowSelected')
                : startNode ? t('flowFlowChat.messageNode', { node: startNode.label })
                  : t('flowFlowChat.messageFlow', { name: flowName })}
              rows={1}
              value={input}
              disabled={loading || !flowId}
              onChange={(e) => {
                setInput(e.target.value);
                resizeTextarea();
              }}
              onKeyDown={handleKeyDown}
            />
          </ComposerBox>
          {attachmentError && <p className="mt-2 text-xs text-red-600">{attachmentError}</p>}
          <AttachPickers
            workspace={workspace}
            filePickerOpen={filePickerOpen}
            onCloseFilePicker={() => setFilePickerOpen(false)}
            attachments={pendingAttachments}
            onAddWorkspaceFiles={addWorkspaceFiles}
            pickerKind={pickerKind}
            onClosePicker={() => setPickerKind(null)}
            references={pendingReferences}
            onAddReferences={addReferences}
          />
        </div>
      </div>
      </div>
    </div>
  );
}
