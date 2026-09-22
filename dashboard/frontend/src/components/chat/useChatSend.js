import { appendLiveThought, genId, mergeMessageFile } from './turnState';
import { streamChat } from '../../api';
import { appendIntoDelegation, appendUnderDelegation, mapDelegation, resolveDelegationTool } from './delegationTimeline';
import { stripUiBlock } from './markdown';
import { targetId } from './targets';
import { EMPTY_GRAPH_RUN, reduceGraphRun } from '../graphRun';
import { LANGUAGES, translate } from '../../i18n';
import { useCallback } from 'react';

/**
 * Sending a turn.
 *
 * The longest single thing this page does, and the reason it is here rather
 * than inline: one send fans out into the optimistic bubble, the three target
 * modes, the token stream and everything that rides on it (tools, thoughts,
 * artifacts, views, the graph mirror), and the transcript that is written when
 * it ends. None of that is of any interest to the panes that render the result.
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

    const referenceLine = hasReferences
      ? t('chat.attachedEntities', { entities: pendingReferences.map((r) => r.label || r.id).join(', ') })
      : '';
    const attachmentLine = [
      hasReferences ? referenceLine : '',
      hasAttachments
        ? t('chat.attachedFiles', { files: pendingAttachments.map((a) => a.filename).join(', ') })
        : '',
    ].filter(Boolean).join('\n');
    const userMsgText = [text, attachmentLine].filter(Boolean).join('\n');
    const userMsg = { id: genId(), role: 'user', content: userMsgText };

    // Ensure there is an active conversation
    let convId = currentConvId;
    if (!convId) {
      convId = genId();
      const basis = text || attachmentLine || t('chat.newChat');
      const title = basis.length > 50 ? basis.slice(0, 50) + '…' : basis;
      const newConv = {
        id: convId,
        title,
        agent_id: targetMode === 'agent' ? selectedAgent : null,
        flow_id: isFlowMode ? selectedFlow : null,
        team_id: isTeamMode ? selectedTeam : null,
        target_mode: targetMode,
        workspace: selectedWorkspace,
        project_id: selectedProject || null,
        messages: [],
        created_at: new Date().toISOString(),
      };
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConvId(convId);
      navigate(`/chat/${convId}`);
    }

    // Append user message
    setConversations((prev) =>
      prev.map((c) =>
        c.id === convId
          ? {
              ...c,
              messages: [...c.messages, userMsg],
              title: c.messages.length === 0
                ? ((text || attachmentLine).length > 50 ? (text || attachmentLine).slice(0, 50) + '…' : (text || attachmentLine))
                : c.title,
            }
          : c,
      ),
    );

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
    const historyPayload = ((convRecord?.messages || [])
      .filter((m) => (m.role === 'user' || m.role === 'agent') && String(m.content || '').trim())
      .slice(-40)
      .map((m) => ({
        role: m.role,
        content: String(m.content || ''),
      })));
    const autoTitleBasis = (text || attachmentLine || '').trim();
    const autoTitle = autoTitleBasis
      ? (autoTitleBasis.length > 50 ? autoTitleBasis.slice(0, 50) + '…' : autoTitleBasis)
      : t('chat.newConversation');
    // The placeholder check compares against every locale's wording so a title
    // written in one language is still recognised after switching.
    const placeholderTitles = new Set(
      LANGUAGES.map((l) => translate(l.code, 'chat.newConversation').trim().toLowerCase())
    );
    const isPlaceholderTitle = !convRecord?.title || placeholderTitles.has(convRecord.title.trim().toLowerCase());
    const convTitle = (!convRecord || (convRecord.messages || []).length === 0 || isPlaceholderTitle)
      ? autoTitle
      : convRecord.title;

    try {
      // In agent mode we create one assistant bubble up front and stream into it.
      // In flow mode we wait for node_start events and create one bubble per node.
      const assistantId = isMultiAgent ? null : genId();
      // Maps node_id -> message bubble id for flow mode.
      const nodeMsgIds = {};
      let currentNodeId = null;
      if (!isMultiAgent) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  messages: [
                    ...c.messages,
                    {
                      id: assistantId,
                      role: 'agent',
                      agent_id: selectedAgent,
                      content: '',
                      error: false,
                      run_id: null,
                      inbound_tokens: null,
                      outbound_tokens: null,
                      total_tokens: null,
                      tool_calls: null,
                      duration_ms: null,
                    },
                  ],
                }
              : c,
          ),
        );
      }

      // Mutated from inside the onEvent closure; read after the stream resolves.
      let finalPayload = null;
      let runId = null;

      await streamChat({
        signal: ctrl.signal,
        body: {
          agent_id: targetMode === 'agent' ? selectedAgent : null,
          flow_id: isFlowMode ? selectedFlow : null,
          team_id: isTeamMode ? selectedTeam : null,
          message: text,
          workspace: effectiveWorkspace || null,
          project_id: convRecord?.project_id || null,
          conversation_id: convId,
          conversation_title: convTitle,
          client_id: clientId,
          history: historyPayload,
          attachments: pendingAttachments.map((a) => ({
            filename: a.filename,
            content: a.content,
            store_to_workspace: Boolean(a.store_to_workspace && selectedWorkspace),
          })),
          // Pointers only — the server renders each entity into the prompt at
          // request time (chat/references.py), so nothing stale is sent.
          references: pendingReferences.map((r) => ({ kind: r.kind, id: r.id, label: r.label || '' })),
        },
        onEvent: (event) => {
          // Helper: returns the id of the assistant bubble that should receive
          // streaming events for the current event. In flow mode this is the
          // bubble for the active node; in agent mode it's the single assistantId.
          const targetMsgId = () => {
            if (isFlowMode) {
              const nid = event.node_id || currentNodeId;
              return nid ? nodeMsgIds[nid] : null;
            }
            return assistantId;
          };

          // A team answers as a conversation: every board entry becomes its own
          // bubble, labelled with who said it and who it was for. There are no
          // token events to stream — the members' turns are whole replies.
          if (event.type === 'team_message') {
            if (event.run_id) { runId = event.run_id; setActiveRunId(event.run_id); }
            const recipients = (event.recipients || []).filter((r) => r !== '*');
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: [
                    ...c.messages,
                    {
                      id: genId(),
                      role: 'agent',
                      agent_id: event.sender,
                      agent_label: recipients.length
                        ? `${event.sender} → ${recipients.join(', ')}`
                        : event.sender,
                      content: event.content || '',
                      error: event.kind === 'error',
                      run_id: event.run_id || null,
                      inbound_tokens: null,
                      outbound_tokens: null,
                      total_tokens: event.tokens || null,
                      tool_calls: null,
                      duration_ms: null,
                    },
                  ],
                },
              ),
            );
            return;
          }
          if (event.type === 'team_meta') {
            if (event.session_id) setSessionId(event.session_id);
            return;
          }

          // Delegated child runs (run_agent_tool) stream their inner events
          // tagged with `delegation` + the child run_id. Fold them into a nested
          // `delegation` timeline entry instead of the top-level timeline, so the
          // UI renders a live nested block. Handled first, then return, so the
          // tagged tool_start/tool_end/think below don't also hit the top level.
          if (event.type === 'delegation_start' || event.type === 'delegation_end' || event.delegation) {
            const tgt = targetMsgId();
            if (tgt) {
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) => {
                      if (m.id !== tgt) return m;
                      const messageRunId = m.run_id || null;
                      let tl = m.timeline || [];
                      if (event.type === 'delegation_start') {
                        tl = appendUnderDelegation(tl, event.parent_run_id, messageRunId, {
                          type: 'delegation',
                          run_id: event.run_id,
                          agent_id: event.agent_id,
                          agent_name: event.agent_name || event.agent_id,
                          depth: event.depth || 1,
                          input: event.input || '',
                          running: true,
                          ok: null,
                          timeline: [],
                        });
                      } else if (event.type === 'delegation_end') {
                        tl = mapDelegation(tl, event.run_id, (d) => ({
                          ...d,
                          running: false,
                          ok: event.ok,
                          error: event.error || '',
                          duration_ms: event.duration_ms,
                        }));
                      } else if (event.type === 'think' || event.type === 'plan') {
                        tl = appendIntoDelegation(tl, event.run_id, {
                          type: 'reasoning', kind: event.type, step: event.step, content: event.content,
                        });
                      } else if (event.type === 'tool_start') {
                        tl = appendIntoDelegation(tl, event.run_id, {
                          type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true,
                        });
                      } else if (event.type === 'tool_end') {
                        tl = resolveDelegationTool(tl, event.run_id, { output: event.output });
                      } else if (event.type === 'tool_error') {
                        tl = resolveDelegationTool(tl, event.run_id, { output: `ERROR: ${event.error}`, error: true });
                      }
                      return { ...m, timeline: tl };
                    }),
                  },
                ),
              );
            }
            return;
          }

          if (event.type === 'flow_meta') {
            // Flow chat: capture session_id and flow agent_id mapping for bubbles.
            if (event.session_id) setSessionId(event.session_id);
          } else if (event.type === 'node_start') {
            // Create a new assistant bubble for this node.
            currentNodeId = event.node_id;
            const msgId = genId();
            nodeMsgIds[event.node_id] = msgId;
            const nodeRunId = event.run_id || null;
            if (nodeRunId) {
              runId = nodeRunId;
              setActiveRunId(nodeRunId);
            }
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: [
                    ...c.messages,
                    {
                      id: msgId,
                      role: 'agent',
                      agent_id: event.agent_id || event.agent_label || '',
                      agent_label: event.agent_label || '',
                      node_id: event.node_id,
                      content: '',
                      error: false,
                      run_id: nodeRunId,
                      inbound_tokens: null,
                      outbound_tokens: null,
                      total_tokens: null,
                      tool_calls: null,
                      duration_ms: null,
                    },
                  ],
                },
              ),
            );
            if (processOpen && nodeRunId) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: [
                  ...(prev.message_runs || []),
                  {
                    message_id: nodeRunId,
                    run_id: nodeRunId,
                    timestamp: new Date().toISOString(),
                    input: userMsgText,
                    output: '',
                    tools: [],
                    thinking: [],
                    inbound_tokens: 0,
                    outbound_tokens: 0,
                    total_tokens: 0,
                    tool_calls: 0,
                    duration_ms: 0,
                    agent_id: event.agent_id || '',
                  },
                ],
              }));
            }
          } else if (event.type === 'node_done') {
            // Finalize one node's bubble; the surrounding loop continues into the next node.
            const msgId = nodeMsgIds[event.node_id];
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === msgId
                      ? {
                          ...m,
                          // Prefer the node's final response over accumulated stream
                          // tokens (see the agent-mode `done` handler) to avoid repeating
                          // the answer when the model re-states it across LLM turns.
                          content: (event.response || '').trim() || (event.response_obj ? '' : stripUiBlock(m.content)) || '',
                          entities: event.entities || m.entities || null,
                          thinking_live: '',
                          error: !event.ok,
                          run_id: event.run_id || m.run_id,
                          inbound_tokens: event.usage?.inbound_tokens ?? null,
                          outbound_tokens: event.usage?.outbound_tokens ?? null,
                          total_tokens: event.usage?.total_tokens ?? null,
                          tool_calls: event.tool_calls ?? null,
                          duration_ms: event.duration_ms ?? null,
                        }
                      : m
                  ),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => {
                const inTok = event.usage?.inbound_tokens || 0;
                const outTok = event.usage?.outbound_tokens || 0;
                const totTok = event.usage?.total_tokens || (inTok + outTok);
                return {
                  ...prev,
                  token_usage: {
                    inbound_tokens: (prev.token_usage?.inbound_tokens || 0) + inTok,
                    outbound_tokens: (prev.token_usage?.outbound_tokens || 0) + outTok,
                    total_tokens: (prev.token_usage?.total_tokens || 0) + totTok,
                  },
                  context_peak: Math.max(prev.context_peak || 0, event.usage?.context_used || 0),
                  context_window_tokens: event.usage?.context_window || prev.context_window_tokens || 0,
                  message_runs: (prev.message_runs || []).map((mr) =>
                    mr.run_id === event.run_id
                      ? {
                          ...mr,
                          output: event.response || mr.output || '',
                          inbound_tokens: inTok,
                          outbound_tokens: outTok,
                          total_tokens: totTok,
                          tool_calls: event.tool_calls ?? mr.tool_calls ?? 0,
                          duration_ms: event.duration_ms ?? mr.duration_ms ?? 0,
                        }
                      : mr
                  ),
                };
              });
            }
          } else if (event.type === 'meta' && event.run_id) {
            runId = event.run_id;
            setActiveRunId(runId);
            if (event.session_id) setSessionId(event.session_id);
            if (processOpen && !isMultiAgent) {
              // In flow mode, node_start already created the process row.
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: [
                  ...(prev.message_runs || []),
                  {
                    message_id: runId,
                    run_id: runId,
                    timestamp: new Date().toISOString(),
                    input: userMsgText,
                    output: '',
                    tools: [],
                    thinking: [],
                    inbound_tokens: 0,
                    outbound_tokens: 0,
                    total_tokens: 0,
                    tool_calls: 0,
                    duration_ms: 0,
                  },
                ],
              }));
            }
            if (!isMultiAgent) {
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) => (m.id === assistantId ? { ...m, run_id: runId } : m)),
                  },
                ),
              );
            }
          } else if (event.type === 'think_delta') {
            // A slice of reasoning the model is still writing. Shown as a
            // three-line ticker under the "working" indicator until the matching
            // `think` event arrives with the completed thought.
            const tgt = targetMsgId();
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? { ...m, thinking_live: appendLiveThought(m.thinking_live, event.delta) }
                      : m
                  ),
                },
              ),
            );
          } else if (event.type === 'think' || event.type === 'plan') {
            // Reasoning steps are appended in execution order so the UI can
            // render each one inline, before the response that followed it.
            // Also appended to `timeline` (the Build-view chronological feed).
            const tgt = targetMsgId();
            const step = { kind: event.type, step: event.step, content: event.content };
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          reasoning: [...(m.reasoning || []), step],
                          timeline: [...(m.timeline || []), { type: 'reasoning', ...step }],
                          // The thought is complete and now renders as a collapsible
                          // ReasoningStep, so the live ticker of the same text goes away.
                          thinking_live: '',
                          // The think/plan content arrives complete in this event
                          // (it's the tool-call input), so the ChatTrail box is
                          // the marker. Don't also set running_tool — that renders a
                          // stale "running" spinner *below* an already-finished thought.
                        }
                      : m
                  ),
                },
              ),
            );
            // Native model thoughts also feed the Process column live.
            if (processOpen && event.native) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? { ...mr, reasoning: [...(mr.reasoning || []), { step: event.step, content: event.content, native: true }] }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'artifact') {
            // File change — surface in the Artifacts panel and inline in the feed.
            mergeArtifact(event);
            const tgt = targetMsgId();
            const entry = {
              type: 'artifact',
              op: event.op,
              path: event.path,
              additions: event.additions,
              deletions: event.deletions,
            };
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          timeline: [...(m.timeline || []), entry],
                          // Persisted alongside the reply so the chat bubble lists
                          // what the agent changed, not just the Artifacts column.
                          files: mergeMessageFile(m.files, event),
                        }
                      : m
                  ),
                },
              ),
            );
          } else if (event.type === 'graph_node_start') {
            setGraphRun((prev) => reduceGraphRun(prev, event));
            // An imported agent walking its own graph. One timeline step per
            // node, in the current bubble: the agent is still one agent, and the
            // nodes are how it got to the answer.
            const tgt = targetMsgId();
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          timeline: [
                            ...(m.timeline || []),
                            { type: 'graph_node', node: event.node, depth: event.depth || 0, running: true, ok: null },
                          ],
                        }
                      : m
                  ),
                },
              ),
            );
          } else if (event.type === 'graph_node_end') {
            setGraphRun((prev) => reduceGraphRun(prev, event));
            const tgt = targetMsgId();
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) => {
                    if (m.id !== tgt || !m.timeline) return m;
                    const tl = [...m.timeline];
                    // Last running entry with this name: a graph with a loop
                    // enters the same node more than once, and resolving the
                    // first would leave later passes looking unfinished.
                    for (let i = tl.length - 1; i >= 0; i -= 1) {
                      if (tl[i].type === 'graph_node' && tl[i].running
                          && (!event.node || tl[i].node === event.node)) {
                        tl[i] = { ...tl[i], running: false, ok: event.ok !== false, error: event.error || '', next: event.next || null };
                        break;
                      }
                    }
                    return { ...m, timeline: tl };
                  }),
                },
              ),
            );
          } else if (event.type === 'tool_start') {
            // Update message bubble to show the running tool name, and append a
            // tool entry to the Build-view timeline (resolved on tool_end).
            const tgt = targetMsgId();
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          running_tool: event.tool,
                          timeline: [
                            ...(m.timeline || []),
                            { type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true },
                          ],
                        }
                      : m
                  ),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => ({
                ...prev,
                tools: [
                  ...(prev.tools || []),
                  {
                    step: event.step,
                    tool: event.tool,
                    input: event.input,
                    output: null,
                    running: true,
                  },
                ],
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? {
                        ...mr,
                        tools: [
                          ...(mr.tools || []),
                          {
                            step: event.step,
                            tool: event.tool,
                            input: event.input,
                            output: null,
                            running: true,
                          },
                        ],
                        tool_calls: ((mr.tool_calls || 0) + 1),
                      }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'tool_end') {
            // Keep running_tool set so the name stays visible until the next token arrives.
            // Resolve the last running tool entry in the Build-view timeline.
            {
              const tgt = targetMsgId();
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) => {
                      if (m.id !== tgt || !m.timeline) return m;
                      const tl = [...m.timeline];
                      for (let i = tl.length - 1; i >= 0; i -= 1) {
                        if (tl[i].type === 'tool' && tl[i].running) {
                          tl[i] = { ...tl[i], output: event.output, running: false };
                          break;
                        }
                      }
                      return { ...m, timeline: tl };
                    }),
                  },
                ),
              );
            }
            if (processOpen) {
              setProcessInsights((prev) => {
                const tools = [...(prev.tools || [])];
                for (let i = tools.length - 1; i >= 0; i -= 1) {
                  if (tools[i].running) {
                    tools[i] = { ...tools[i], output: event.output, running: false };
                    break;
                  }
                }
                const message_runs = (prev.message_runs || []).map((mr, idx) => {
                  if (idx !== (prev.message_runs || []).length - 1) return mr;
                  const mrTools = [...(mr.tools || [])];
                  for (let i = mrTools.length - 1; i >= 0; i -= 1) {
                    if (mrTools[i].running) {
                      mrTools[i] = { ...mrTools[i], output: event.output, running: false };
                      break;
                    }
                  }
                  return { ...mr, tools: mrTools };
                });
                return { ...prev, tools, message_runs };
              });
            }
          } else if (event.type === 'token') {
            const tgt = targetMsgId();
            const tok = event.token || '';
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) => {
                    if (m.id !== tgt) return m;
                    // Coalesce contiguous tokens into the trailing text segment so
                    // the Build-view feed shows continuous prose, not per-token noise.
                    const tl = [...(m.timeline || [])];
                    const last = tl[tl.length - 1];
                    if (last && last.type === 'text') {
                      tl[tl.length - 1] = { ...last, text: `${last.text || ''}${tok}` };
                    } else {
                      tl.push({ type: 'text', text: tok });
                    }
                    return {
                      ...m,
                      content: `${m.content || ''}${tok}`,
                      running_tool: null,
                      timeline: tl,
                    };
                  }),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? { ...mr, output: `${mr.output || ''}${event.token || ''}` }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'done') {
            finalPayload = event;
            const resolvedRunId = runId || event.run_id || null;
            if (resolvedRunId) setActiveRunId(resolvedRunId);
            // In flow mode the per-node bubbles were already finalized via
            // node_done, and in team mode every reply is already on the board,
            // so the overall "done" event only carries run-level metadata.
            if (!isMultiAgent) {
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) =>
                      m.id === assistantId
                        ? {
                            ...m,
                            // Prefer the backend's final response (the de-duplicated
                            // AgentFinish output). Streamed tokens accumulate text from
                            // every LLM turn — including forced-review re-statements — so
                            // using them as-is can repeat the answer. Fall back to the
                            // streamed content only when no final response was sent.
                            content: (event.response || '').trim() || (event.response_obj ? '' : stripUiBlock(m.content)) || '',
                            // Structured response (buttons / Telegram keyboard / …); rendered
                            // as interactive UI under the bubble. null for plain replies.
                            response_obj: event.response_obj || null,
                            // Service entities this turn touched, rendered as links
                            // under the reply (see EntityLinks).
                            entities: event.entities || m.entities || null,
                            thinking_live: '',
                            error: !event.ok,
                            run_id: resolvedRunId,
                            inbound_tokens: event.usage?.inbound_tokens ?? m.inbound_tokens ?? null,
                            outbound_tokens: event.usage?.outbound_tokens ?? m.outbound_tokens ?? null,
                            total_tokens: event.usage?.total_tokens ?? m.total_tokens ?? null,
                            tool_calls: event.tool_calls ?? m.tool_calls ?? null,
                            duration_ms: event.duration_ms ?? m.duration_ms ?? null,
                            // How full the model's window was at this turn's
                            // biggest call, kept on the message so the meter
                            // survives switching conversations and reloading —
                            // the transcript is the context, and it is what
                            // localStorage holds.
                            context_used: event.usage?.context_used ?? m.context_used ?? null,
                            context_window: event.usage?.context_window ?? m.context_window ?? null,
                            context_overflow: event.error_code === 'context_overflow',
                          }
                        : m
                    ),
                  },
                ),
              );
            }
            if (processOpen && !isMultiAgent) {
              setProcessInsights((prev) => {
                const inTok = event.usage?.inbound_tokens || 0;
                const outTok = event.usage?.outbound_tokens || 0;
                const totTok = event.usage?.total_tokens || (inTok + outTok);
                return {
                  ...prev,
                  token_usage: {
                    inbound_tokens: (prev.token_usage?.inbound_tokens || 0) + inTok,
                    outbound_tokens: (prev.token_usage?.outbound_tokens || 0) + outTok,
                    total_tokens: (prev.token_usage?.total_tokens || 0) + totTok,
                  },
                  context_peak: Math.max(prev.context_peak || 0, event.usage?.context_used || 0),
                  context_window_tokens: event.usage?.context_window || prev.context_window_tokens || 0,
                  message_runs: (prev.message_runs || []).map((mr, idx) =>
                    idx === (prev.message_runs || []).length - 1
                      ? {
                          ...mr,
                          output: event.response || mr.output || '',
                          inbound_tokens: inTok,
                          outbound_tokens: outTok,
                          total_tokens: totTok,
                          tool_calls: event.tool_calls ?? mr.tool_calls ?? 0,
                          duration_ms: event.duration_ms ?? mr.duration_ms ?? 0,
                        }
                      : mr
                  ),
                };
              });
            }
          }
        },
      });

      if (!finalPayload) {
        if (!isMultiAgent) {
          setConversations((prev) =>
            prev.map((c) =>
              c.id !== convId ? c : {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantId && !m.content
                    ? { ...m, content: t('chat.noStreamedOutput'), error: true }
                    : m
                ),
              },
            ),
          );
        }
      } else if ((runId || finalPayload.run_id) && processOpen) {
        loadProcessData(runId || finalPayload.run_id);
      }
    } catch (err) {
      if (err?.name === 'AbortError') return;

      const errMsg = {
        id: genId(),
        role: 'agent',
        content: err.response?.data?.detail || err.message || t('chat.failedToGetResponse'),
        error: true,
        run_id: null,
      };
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
