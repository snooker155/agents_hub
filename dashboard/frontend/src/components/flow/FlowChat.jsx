import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, Send, StopCircle, User, Workflow } from 'lucide-react';
import { stopMessage, startChatOverSSE } from '../../api';
import { useStream } from '../stream';
import { useI18n } from '../../i18n';
import { useInlineChatOpen } from '../pageChat/pageChat';

// ---------------------------------------------------------------------------
// Slim flow chat — embedded version of the Chat page scoped to a single flow.
// Each user turn runs through every node of the flow (one bubble per node). The
// run's events are delivered over the app's single multiplexed SSE connection
// (POST /api/chat/stream-sse returns immediately; events arrive on the
// `chat:{conversation_id}` channel), so a flow chat doesn't hold a second
// long-lived connection and exhaust the browser's per-origin limit.
// ---------------------------------------------------------------------------

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// Minimal markdown-ish renderer: fenced code blocks + inline code, otherwise plain.
function renderContent(text) {
  const parts = String(text || '').split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```')) {
      const inner = part.slice(3, -3);
      const newline = inner.indexOf('\n');
      const code = newline > 0 ? inner.slice(newline + 1) : inner;
      return (
        <pre
          key={i}
          className="my-2 overflow-x-auto whitespace-pre rounded-lg bg-gray-900 p-3 text-xs text-gray-100"
        >
          {code}
        </pre>
      );
    }
    return (
      <span key={i} className="whitespace-pre-wrap">
        {part}
      </span>
    );
  });
}

function ChatBubble({ msg, agentLabel }) {
  const isUser = msg.role === 'user';
  return (
    <div className={`mb-4 flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className="flex shrink-0 flex-col items-center gap-1">
        <div
          className={`flex h-7 w-7 items-center justify-center rounded-full text-white ${
            isUser ? 'bg-indigo-600' : 'bg-gray-800'
          }`}
        >
          {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
        </div>
        {!isUser && agentLabel && (
          <span className="max-w-[52px] break-words text-center text-[9px] font-medium leading-tight text-gray-400">
            {agentLabel}
          </span>
        )}
      </div>
      <div
        className={`max-w-[78%] text-sm leading-relaxed ${
          isUser
            ? 'rounded-2xl rounded-tr-sm bg-indigo-600 px-3.5 py-2.5 text-white'
            : 'rounded-2xl rounded-tl-sm border border-gray-200 bg-white px-3.5 py-2.5 text-gray-800 shadow-sm'
        } ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
      >
        {!isUser && !msg.content && msg.running ? (
          <span className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{msg.running_tool || 'Working'}</span>
            <span className="flex gap-1">
              {[0, 150, 300].map((d) => (
                <span
                  key={d}
                  className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
                  style={{ animationDelay: `${d}ms` }}
                />
              ))}
            </span>
          </span>
        ) : isUser ? (
          <span className="whitespace-pre-wrap">{msg.content}</span>
        ) : (
          <div>{renderContent(msg.content)}</div>
        )}
      </div>
    </div>
  );
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
}) {
  const { t } = useI18n();
  // The flow's own chat, in the editor's right-hand panel: while it is mounted
  // the floating launcher would sit on its composer, so it stands down.
  useInlineChatOpen();
  const { on, acquireChannel, clientId } = useStream();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const activeRunIdRef = useRef(null);
  // Tears down the current turn's SSE channel subscription (listener + interest).
  const subCleanupRef = useRef(null);
  const endRef = useRef(null);
  const textareaRef = useRef(null);

  const flowName = flow?.name || 'flow';

  // Drop the channel subscription if the component unmounts mid-run.
  useEffect(() => () => subCleanupRef.current?.(), []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

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

  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  }, []);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || loading || !flowId) return;
    if (!workspace) {
      setMessages((prev) => [
        ...prev,
        { id: genId(), role: 'agent', content: t('flowFlowChat.selectWorkspaceFirst'), error: true },
      ]);
      return;
    }

    const history = messages
      .filter((m) => (m.role === 'user' || m.role === 'agent') && String(m.content || '').trim())
      .slice(-40)
      .map((m) => ({ role: m.role, content: String(m.content || '') }));

    const userMsg = { id: genId(), role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

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
      if (event.type === 'done') sawDone = true;
      if (event.type === 'flow_meta') {
        // handled by onStreamEvent
      } else if (event.type === 'node_start') {
        currentNodeId = event.node_id;
        const msgId = genId();
        nodeMsgIds[event.node_id] = msgId;
        if (event.run_id) activeRunIdRef.current = event.run_id;
        setMessages((prev) => [
          ...prev,
          {
            id: msgId,
            role: 'agent',
            agent_id: event.agent_id || event.agent_label || '',
            agent_label: event.agent_label || '',
            node_id: event.node_id,
            content: '',
            running: true,
            error: false,
          },
        ]);
      } else if (event.type === 'node_done') {
        const msgId = nodeMsgIds[event.node_id];
        setMessages((prev) =>
          prev.map((m) =>
            m.id === msgId
              ? {
                  ...m,
                  // Prefer the node's final response over accumulated stream
                  // tokens so a re-stated answer isn't repeated in the bubble.
                  content: (event.response || '').trim() || (m.content || '').trim() || '',
                  error: !event.ok,
                  running: false,
                  running_tool: null,
                }
              : m
          )
        );
      } else if (event.type === 'tool_start') {
        const tgt = targetMsgId(event);
        setMessages((prev) =>
          prev.map((m) => (m.id === tgt ? { ...m, running_tool: event.tool } : m))
        );
      } else if (event.type === 'token') {
        const tgt = targetMsgId(event);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === tgt
              ? { ...m, content: `${m.content || ''}${event.token || ''}`, running_tool: null }
              : m
          )
        );
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
        message: text,
        workspace: workspace || null,
        conversation_id: conversationId,
        conversation_title: flowName,
        history,
        attachments: [],
        client_id: clientId,
      });
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: genId(), role: 'agent', content: err?.response?.data?.detail || err.message || t('flowFlowChat.startChatFailed'), error: true },
      ]);
      finalize();
    }
  }, [input, loading, flowId, workspace, messages, clientId, conversationId, on, acquireChannel, t, onStreamEvent, onActivity, flowName]);

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

  const labelFor = (msg) =>
    msg.agent_label || agentLabels[msg.agent_id] || msg.agent_id || 'Agent';

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden bg-slate-50">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 py-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-100">
          <Workflow className="h-4 w-4 text-emerald-600" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-bold text-slate-900">{t('flowFlowChat.chatWithFlow')}</div>
          <div className="truncate text-[11px] text-slate-400">{flowName}</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 && !loading ? (
          <div className="flex h-full min-h-[200px] flex-col items-center justify-center text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-100 shadow-sm">
              <Workflow className="h-7 w-7 text-emerald-600" />
            </div>
            <p className="max-w-[240px] text-sm leading-relaxed text-slate-500">
              Send a message to run it through every node of this flow, in DAG order. Each node
              produces its own reply and feeds the next.
            </p>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <ChatBubble key={msg.id} msg={msg} agentLabel={msg.role !== 'user' ? labelFor(msg) : undefined} />
            ))}
            <div ref={endRef} />
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-slate-200 bg-white px-3 py-3">
        <div className="mx-auto w-full max-w-2xl">
        <div className="flex items-center gap-2 rounded-2xl border border-slate-300 bg-white px-3 py-2 shadow-sm transition focus-within:border-emerald-400 focus-within:ring-2 focus-within:ring-emerald-100">
          <textarea
            ref={textareaRef}
            className="flex-1 resize-none self-center bg-transparent text-sm leading-relaxed text-slate-800 placeholder-slate-400 focus:outline-none disabled:opacity-50"
            placeholder={!flowId ? t('flowFlowChat.noFlowSelected') : `Message ${flowName}…`}
            rows={1}
            value={input}
            disabled={loading || !flowId}
            onChange={(e) => {
              setInput(e.target.value);
              resizeTextarea();
            }}
            onKeyDown={handleKeyDown}
          />
          {loading ? (
            <button
              onClick={stopGeneration}
              title={t('flowFlowChat.stop')}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-100 text-red-600 transition hover:bg-red-200"
            >
              <StopCircle className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={!input.trim() || !flowId}
              title={t('flowFlowChat.sendEnter')}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <p className="mt-1.5 text-center text-[11px] text-slate-400">{t('flowFlowChat.enterToSendShiftEnter')}</p>
        </div>
      </div>
    </div>
  );
}
