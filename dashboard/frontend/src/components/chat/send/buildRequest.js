/**
 * Building the outgoing turn: the "attached ..." lines appended under the
 * user's text, the trimmed history the backend gets as context, the
 * conversation's title, and the request body `streamChat` posts.
 *
 * Pure functions only, so the send path's shape can be checked without
 * mounting a component or opening a stream.
 */

import { LANGUAGES, translate } from '../../../i18n';

// A conversation title is never allowed to run on; the same cap is used for
// the auto-title and for the fallback title taken from the first message.
function truncateTitle(basis, max = 50) {
  return basis.length > max ? `${basis.slice(0, max)}…` : basis;
}

// The `chat.attachedEntities` / `chat.attachedFiles` lines shown under the
// user's typed text so the transcript reflects what was attached, even though
// the actual attachment payload travels separately in the request body.
function buildAttachmentLine(t, { pendingAttachments, pendingReferences }) {
  const hasAttachments = (pendingAttachments || []).length > 0;
  const hasReferences = (pendingReferences || []).length > 0;
  const referenceLine = hasReferences
    ? t('chat.attachedEntities', { entities: pendingReferences.map((r) => r.label || r.id).join(', ') })
    : '';
  return [
    hasReferences ? referenceLine : '',
    hasAttachments
      ? t('chat.attachedFiles', { files: pendingAttachments.map((a) => a.filename).join(', ') })
      : '',
  ].filter(Boolean).join('\n');
}

// Up to the last 40 user/agent turns with actual text, sent as `history`.
// Everything else a turn produced (tool calls, artifacts, thoughts) is
// reconstructed server-side from the run log, not resent.
function buildHistoryPayload(messages) {
  return (messages || [])
    .filter((m) => (m.role === 'user' || m.role === 'agent') && String(m.content || '').trim())
    .slice(-40)
    .map((m) => ({ role: m.role, content: String(m.content || '') }));
}

// A conversation keeps its first typed title until it's replaced by hand, but
// "still the placeholder" is checked against every locale's wording, so a
// title written before a language switch is still recognised as unset
// afterwards.
function resolveConversationTitle({ text, attachmentLine, convRecord, t }) {
  const autoTitleBasis = (text || attachmentLine || '').trim();
  const autoTitle = autoTitleBasis ? truncateTitle(autoTitleBasis) : t('chat.newConversation');
  const placeholderTitles = new Set(
    LANGUAGES.map((l) => translate(l.code, 'chat.newConversation').trim().toLowerCase()),
  );
  const isPlaceholderTitle = !convRecord?.title || placeholderTitles.has(convRecord.title.trim().toLowerCase());
  return (!convRecord || (convRecord.messages || []).length === 0 || isPlaceholderTitle)
    ? autoTitle
    : convRecord.title;
}

// The body `streamChat` posts: everything the backend needs to run one turn
// against whichever of agent / flow / team this send targets.
function buildStreamRequestBody({
  targetMode, isFlowMode, isTeamMode, selectedAgent, selectedFlow, selectedTeam,
  text, effectiveWorkspace, projectId, convId, convTitle, clientId, historyPayload,
  pendingAttachments, pendingReferences, selectedWorkspace,
}) {
  return {
    agent_id: targetMode === 'agent' ? selectedAgent : null,
    flow_id: isFlowMode ? selectedFlow : null,
    team_id: isTeamMode ? selectedTeam : null,
    message: text,
    workspace: effectiveWorkspace || null,
    project_id: projectId || null,
    conversation_id: convId,
    conversation_title: convTitle,
    client_id: clientId,
    history: historyPayload,
    attachments: (pendingAttachments || []).map((a) => ({
      filename: a.filename,
      content: a.content,
      store_to_workspace: Boolean(a.store_to_workspace && selectedWorkspace),
    })),
    // Pointers only — the server renders each entity into the prompt at
    // request time (chat/references.py), so nothing stale is sent.
    references: (pendingReferences || []).map((r) => ({ kind: r.kind, id: r.id, label: r.label || '' })),
  };
}

export { truncateTitle, buildAttachmentLine, buildHistoryPayload, resolveConversationTitle, buildStreamRequestBody };
